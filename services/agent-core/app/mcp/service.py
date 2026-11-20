"""MCP session registry + Tool Registry bridge (original implementation).

Live sessions are process-local, keyed by MCPServer id. Connecting a
server performs the real handshake, lists its tools (paginated), and
registers each tool in the unified Tool Registry under a namespaced id
``mcp.<server>.<tool>`` so the standard permission check
(execute_tool with an agent's tool list) governs MCP calls exactly like
built-in tools. Disconnecting closes the subprocess (stdin close) and
unregisters the namespaced tools.
"""
import threading

from app import models
from app.mcp.client import StdioMCPSession, result_text
from app.tools.registry import REGISTRY, _HANDLERS, ToolSpec, ToolError

SESSIONS = {}  # server_id -> {"session": StdioMCPSession, "tool_ids": [str]}

# Connect/disconnect of the SAME server are serialised: two overlapping
# connects could otherwise each spawn a process and overwrite each
# other's registry entries, orphaning one process entirely.
_server_locks = {}
_server_locks_guard = threading.Lock()


def _server_lock(server_id):
    with _server_locks_guard:
        return _server_locks.setdefault(server_id, threading.Lock())


def register_server_tools(server_id, server_name, session):
    # The namespace is the immutable server id, NOT a sanitised name:
    # two different names can sanitise to the same string (any two
    # Chinese names both became "_"), which let one server's tools
    # overwrite — and inherit the permissions of — another's.
    tool_ids = []
    if "tools" not in (getattr(session, "server_capabilities", None) or {}):
        return tool_ids  # server never declared a tools capability
    for t in session.list_tools():
        tool_id = f"mcp.{server_id}.{t['name']}"
        raw_name = t["name"]

        def _make_handler(sess, name):
            def handler(args):
                res = sess.call_tool(name, args or {})
                if res.get("isError"):
                    raise ToolError(f"MCP 工具「{name}」返回错误："
                                    f"{result_text(res)}", "TOOL_ERROR")
                return {"content": res.get("content") or [],
                        "text": result_text(res), "isError": False}
            return handler

        REGISTRY[tool_id] = ToolSpec(
            tool_id, f"MCP {server_name}:{raw_name}",
            t.get("description", ""), t.get("inputSchema") or {}, ["mcp"], 30)
        _HANDLERS[tool_id] = _make_handler(session, raw_name)
        tool_ids.append(tool_id)
    return tool_ids


def unregister_server_tools(tool_ids):
    for tid in tool_ids or []:
        REGISTRY.pop(tid, None)
        _HANDLERS.pop(tid, None)


def connect(db, server_id):
    with _server_lock(server_id):
        return _connect_locked(db, server_id)


def _connect_locked(db, server_id):
    s = db.query(models.MCPServer).get(server_id)
    if s is None:
        raise ValueError(f"MCP 服务器不存在：{server_id}")
    if s.transport != "stdio":
        raise ValueError(f"当前客户端只支持本地命令连接方式，收到的是：{s.transport}")
    if server_id in SESSIONS:
        _disconnect_locked(server_id)
    command_line = s.command_or_url
    extra_args = getattr(s, "args", None) or []
    if extra_args:
        import shlex
        command_line = command_line + " " + " ".join(shlex.quote(str(a)) for a in extra_args)
    session = StdioMCPSession(command_line, env=getattr(s, "env", None))
    tool_ids = []
    try:
        session.start()
        info = session.initialize()
        tool_ids = register_server_tools(server_id, s.name, session)
        SESSIONS[server_id] = {"session": session, "tool_ids": tool_ids}
        s.tools = tool_ids
        db.commit()
    except Exception:
        # A failed connect leaves NOTHING behind: no orphan process, no
        # half-registered tools, no committed state claiming success.
        db.rollback()
        SESSIONS.pop(server_id, None)
        unregister_server_tools(tool_ids)
        try:
            session.close()
        except Exception:
            pass
        raise
    return {"server_id": server_id, "server_info": session.server_info,
            "protocol_version": session.protocol_version,
            "initialize_result": info, "tools": session.list_tools(),
            "registered_tool_ids": tool_ids}


def disconnect(server_id):
    with _server_lock(server_id):
        return _disconnect_locked(server_id)


def _disconnect_locked(server_id):
    entry = SESSIONS.pop(server_id, None)
    if entry is None:
        return {"disconnected": False}
    unregister_server_tools(entry["tool_ids"])
    entry["session"].close()
    return {"disconnected": True}


def get_session(server_id):
    entry = SESSIONS.get(server_id)
    if entry is None:
        raise ValueError(f"MCP 服务器 {server_id} 尚未连接")
    return entry["session"]


def call(server_id, tool_name, arguments=None):
    session = get_session(server_id)
    res = session.call_tool(tool_name, arguments or {})
    return {"tool": tool_name, "content": res.get("content") or [],
            "text": result_text(res), "isError": bool(res.get("isError"))}
