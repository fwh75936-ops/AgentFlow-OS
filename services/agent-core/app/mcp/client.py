"""Minimal MCP client over stdio (original implementation).

Design (MCP as an edge adapter, not an engine):
  * one subprocess per server, stderr drained separately from the
    JSON-RPC stdout stream;
  * a session object with a pending-request map (id -> waiter) fed by a
    reader thread;
  * strict lifecycle: initialize request -> server picks the protocol
    version -> client sends notifications/initialized -> only then are
    tools/list and tools/call allowed (calling earlier raises);
  * tools/list follows nextCursor pagination and is cached per session;
  * closing the session closes stdin, which ends the server process.
"""
import json
import subprocess
import sys
import threading

PROTOCOL_VERSION = "2024-11-05"
CLIENT_INFO = {"name": "agentflow-os", "version": "0.1.0"}


class MCPError(Exception):
    def __init__(self, message, code=None):
        super().__init__(message)
        self.code = code


class StdioMCPSession:
    def __init__(self, command, timeout=10.0, env=None):
        if isinstance(command, str):
            import shlex
            command = shlex.split(command)
        self.command = command
        self.env = {str(k): str(v) for k, v in (env or {}).items()}
        self.timeout = timeout
        self.proc = None
        self._next_id = 0
        self._id_lock = threading.Lock()
        self._write_lock = threading.Lock()
        self._pending = {}       # id -> {"event": Event, "response": dict}
        self._pending_lock = threading.Lock()
        self.stderr_tail = []
        self.state = "new"       # new -> handshaking -> ready -> closed | dead
        self.server_info = None
        self.server_capabilities = {}
        self.protocol_version = None
        self._tools_cache = None
        self._reader_thread = None

    # ---- process plumbing -------------------------------------------------
    def start(self):
        if self.proc is not None:
            return self
        import os
        merged_env = {**os.environ, **self.env} if self.env else None
        try:
            self.proc = subprocess.Popen(
                self.command, stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, bufsize=1, env=merged_env,
                encoding="utf-8", errors="replace")  # MCP wire format is UTF-8
        except OSError as e:
            raise MCPError(f"无法启动 MCP 服务器命令（{self.command[0] if self.command else ''}）：{e}")
        self._reader_thread = threading.Thread(target=self._reader, daemon=True)
        self._reader_thread.start()
        threading.Thread(target=self._stderr_drain, daemon=True).start()
        return self

    def _stderr_drain(self):
        try:
            for line in self.proc.stderr:
                self.stderr_tail.append(line.rstrip())
                del self.stderr_tail[:-50]
        except Exception:
            pass

    def _reader(self):
        try:
            for line in self.proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                except Exception:
                    continue
                if not isinstance(msg, dict):
                    continue  # valid JSON, wrong shape: ignore, stay alive
                if "method" in msg and "id" in msg:
                    # A server-initiated REQUEST (e.g. ping) is not a
                    # response: never match it to a waiter; answer it.
                    self._answer_server_request(msg)
                    continue
                if "method" in msg:
                    # Notification: only tools/list_changed matters here.
                    if msg.get("method") == "notifications/tools/list_changed":
                        self._tools_cache = None
                    continue
                rid = msg.get("id")
                if rid is None:
                    continue
                with self._pending_lock:
                    waiter = self._pending.pop(rid, None)
                if waiter is not None:
                    waiter["response"] = msg
                    waiter["event"].set()
        except Exception:
            pass
        finally:
            # The read loop is over: this session can never deliver a
            # response again, so it must stop presenting itself as ready.
            if self.state in ("ready", "handshaking"):
                self.state = "dead"
            with self._pending_lock:
                waiters = list(self._pending.values())
                self._pending.clear()
            for w in waiters:
                w["response"] = {"error": {"code": -32000,
                                            "message": "server process ended"}}
                w["event"].set()

    def _answer_server_request(self, msg):
        if msg.get("method") == "ping":
            reply = {"jsonrpc": "2.0", "id": msg.get("id"), "result": {}}
        else:
            reply = {"jsonrpc": "2.0", "id": msg.get("id"),
                     "error": {"code": -32601,
                               "message": f"客户端不支持服务器请求：{msg.get('method')}"}}
        try:
            self._send(reply)
        except Exception:
            pass

    def _send(self, obj):
        if self.proc is None or self.proc.poll() is not None:
            raise MCPError("服务器进程未在运行")
        with self._write_lock:
            self.proc.stdin.write(json.dumps(obj) + "\n")
            self.proc.stdin.flush()

    # ---- JSON-RPC ---------------------------------------------------------
    def request(self, method, params=None):
        with self._id_lock:
            self._next_id += 1
            rid = self._next_id
        waiter = {"event": threading.Event(), "response": None}
        with self._pending_lock:
            self._pending[rid] = waiter
        try:
            self._send({"jsonrpc": "2.0", "id": rid, "method": method,
                        "params": params or {}})
        except Exception:
            with self._pending_lock:
                self._pending.pop(rid, None)
            raise
        if not waiter["event"].wait(self.timeout):
            with self._pending_lock:
                self._pending.pop(rid, None)
            raise MCPError(f"请求超时：{method}")
        resp = waiter["response"] or {}
        if "error" in resp:
            err = resp["error"] or {}
            raise MCPError(err.get("message", "MCP 返回未知错误"), err.get("code"))
        return resp.get("result")

    def notify(self, method, params=None):
        self._send({"jsonrpc": "2.0", "method": method, "params": params or {}})

    # ---- MCP lifecycle ----------------------------------------------------
    def initialize(self):
        if self.proc is None:
            self.start()
        if self.state == "ready":
            return self.server_info
        self.state = "handshaking"
        res = self.request("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": CLIENT_INFO})
        self.protocol_version = (res or {}).get("protocolVersion")
        if not self.protocol_version:
            self.state = "dead"
            raise MCPError("服务器的握手回复里没有协议版本，不能确认兼容性")
        if self.protocol_version not in ("2024-11-05", "2025-03-26", "2025-06-18"):
            self.state = "dead"
            raise MCPError(f"服务器要求使用未支持的协议版本：{self.protocol_version}")
        self.server_info = (res or {}).get("serverInfo")
        self.server_capabilities = (res or {}).get("capabilities") or {}
        self.notify("notifications/initialized")
        self.state = "ready"
        return res

    def _require_ready(self):
        if self.state == "dead":
            raise MCPError("会话已失效：服务器进程已结束或输出中断，请重新连接")
        if self.state != "ready":
            raise MCPError("会话尚未初始化：需先完成握手才能执行操作")

    def list_tools(self, use_cache=True):
        self._require_ready()
        if use_cache and self._tools_cache is not None:
            return self._tools_cache
        tools, cursor = [], None
        seen_cursors = set()
        for _page in range(50):  # hard bounds: a looping cursor must not hang connect
            params = {"cursor": cursor} if cursor else {}
            page = self.request("tools/list", params) or {}
            tools.extend(page.get("tools") or [])
            if len(tools) > 1000:
                raise MCPError("服务器返回的工具数量超过上限（1000）")
            cursor = page.get("nextCursor")
            if not cursor:
                break
            if cursor in seen_cursors:
                raise MCPError("服务器的工具分页游标重复，无法完整获取工具列表")
            seen_cursors.add(cursor)
        else:
            raise MCPError("服务器的工具分页超过 50 页，无法完整获取工具列表")
        self._tools_cache = tools
        return tools

    def call_tool(self, name, arguments=None):
        self._require_ready()
        res = self.request("tools/call", {"name": name, "arguments": arguments or {}})
        return res or {}

    def close(self):
        self.state = "closed"
        if self.proc is not None:
            try:
                self.proc.stdin.close()  # closing stdin ends a stdio server
            except Exception:
                pass
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                try:
                    self.proc.wait(timeout=5)  # reap: no zombie left behind
                except Exception:
                    pass
            except Exception:
                pass
            self.proc = None
        if self._reader_thread is not None:
            self._reader_thread.join(timeout=2)


def result_text(call_result):
    """Flatten text content blocks of a tools/call result."""
    parts = []
    for block in (call_result or {}).get("content") or []:
        if block.get("type") == "text":
            parts.append(block.get("text", ""))
    return "\n".join(parts)


def default_python():
    return sys.executable
