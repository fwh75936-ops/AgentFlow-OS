"""Unified Tool Registry. Every agent tool call goes through here:
permission check (agent's tool list) -> approval flag for high-risk -> execute.
File tools are confined to AGENTFLOW_WORKSPACE_ROOT (default ./data/workspaces)."""
import os
import shlex
import subprocess
import sys
from dataclasses import dataclass, field

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
WORKSPACE_ROOT = os.environ.get("AGENTFLOW_WORKSPACE_ROOT", os.path.join(BASE_DIR, "data", "workspaces"))
os.makedirs(WORKSPACE_ROOT, exist_ok=True)

class ToolError(Exception):
    def __init__(self, message, error_type="TOOL_ERROR"):
        super().__init__(message)
        self.error_type = error_type

@dataclass
class ToolSpec:
    id: str
    name: str
    description: str
    input_schema: dict = field(default_factory=dict)
    permissions: list = field(default_factory=list)
    timeout: int = 60
    retry_policy: dict = field(default_factory=lambda: {"max_retries": 0})
    high_risk: bool = False
    requires_approval: bool = False

def _confined(path: str) -> str:
    # Resolve symlinks and compare by path components: a plain string
    # prefix check accepts siblings like "<root>_backup", and abspath
    # alone lets a symlink inside the root point outside it.
    root = os.path.realpath(WORKSPACE_ROOT)
    full = os.path.realpath(os.path.join(root, path))
    if os.path.commonpath([root, full]) != root:
        raise ToolError(f"路径超出工作区范围：{path}", "PERMISSION_ERROR")
    return full

def _file_read(args):
    p = _confined(args["path"])
    if not os.path.isfile(p):
        raise ToolError(f"文件不存在：{args['path']}", "ENVIRONMENT_ERROR")
    with open(p, encoding="utf-8", errors="replace") as f:
        return {"content": f.read()[:100000]}

def _file_write(args):
    p = _confined(args["path"])
    os.makedirs(os.path.dirname(p), exist_ok=True)
    content = args.get("content", "")
    with open(p, "w", encoding="utf-8") as f:
        f.write(content)
    return {"written": args["path"], "bytes": len(content.encode("utf-8"))}

def _run_command(args):
    cmd = args["command"]
    parts = shlex.split(cmd) if isinstance(cmd, str) else list(cmd)
    proc = subprocess.run(parts, cwd=WORKSPACE_ROOT, capture_output=True, text=True,
                          timeout=int(args.get("timeout", 60)))
    return {"returncode": proc.returncode, "stdout": proc.stdout[-8000:], "stderr": proc.stderr[-4000:]}

def _run_pytest(args):
    target = _confined(args.get("path", "."))
    proc = subprocess.run([sys.executable, "-m", "pytest", "-q"], cwd=target,
                          capture_output=True, text=True, timeout=int(args.get("timeout", 120)))
    return {"returncode": proc.returncode, "passed": proc.returncode == 0,
            "stdout": proc.stdout[-8000:], "stderr": proc.stderr[-4000:]}

# ---- Shared Linux Workspace tools ----
# These delegate to app.workspace.service, i.e. the ONE shared Linux env.
# Permission + approval are enforced by execute_tool below before any handler
# runs; the service adds its own per-command high-risk check and audit log.

def _ws_db():
    from app.database import SessionLocal, init_db
    init_db()
    return SessionLocal()

def _linux_exec(args):
    from app.workspace import service
    db = _ws_db()
    try:
        agent_id = int(args["agent_id"])
        session = None
        if args.get("session_id"):
            from app import models
            session = db.query(models.WorkspaceSession).get(int(args["session_id"]))
            if session is not None and session.agent_id != agent_id:
                # A session belongs to the agent that opened it; naming
                # someone else's session id must not hijack their shell.
                raise ToolError("终端会话属于其他员工，不能使用", "PERMISSION_ERROR")
        if session is None:
            session = service.create_session(db, agent_id, args.get("task_id"))
        # Registry-level approval was already enforced by execute_tool.
        return service.exec_in_session(db, session, args["command"],
                                       task_id=args.get("task_id"), approved=True,
                                       timeout=args.get("timeout"))
    finally:
        db.close()

def _linux_file_read(args):
    from app.workspace import service
    db = _ws_db()
    try:
        return service.read_file(db, args["path"])
    finally:
        db.close()

def _linux_file_write(args):
    # Agent tool writes are STAGED for user review (Pilot-style staged
    # diff): the shared file is not touched until the user accepts the
    # change on the AI 电脑 page. Direct service.write_file calls (the
    # REST path the user drives themselves) keep the immediate behavior.
    from app.workspace import review
    db = _ws_db()
    try:
        change = review.stage_change(db, int(args["agent_id"]), args["path"],
                                     args.get("content", ""),
                                     task_id=args.get("task_id"))
        return {"staged": True, "change_id": change["id"], "path": change["path"],
                "status": "pending_review",
                "message": "变更已提交，等待用户在 AI 电脑页审阅接受后才会写入共享文件"}
    finally:
        db.close()

def _linux_file_list(args):
    from app.workspace import service
    db = _ws_db()
    try:
        return {"files": service.list_files(db, args.get("path", ""))}
    finally:
        db.close()

def _browser_call(fn_name, args):
    from app.browser import service
    db = _ws_db()
    try:
        fn = getattr(service, fn_name)
        if fn_name == "open_url":
            return fn(db, args.get("url", ""), agent_id=int(args["agent_id"]))
        if fn_name == "search":
            return fn(db, args.get("query", ""), agent_id=int(args["agent_id"]))
        return fn(db, agent_id=int(args["agent_id"]))
    except service.BrowserError as e:
        raise ToolError(str(e), "TOOL_ERROR")
    finally:
        db.close()

def _browser_open(args):
    return _browser_call("open_url", args)

def _browser_search(args):
    return _browser_call("search", args)

def _browser_read(args):
    return _browser_call("read_page", args)

def _browser_screenshot(args):
    return _browser_call("screenshot", args)

def _desktop_control(args):
    from app.workspace import service
    db = _ws_db()
    try:
        action = args.get("action", "acquire")
        agent_id = int(args["agent_id"])
        if action == "acquire":
            return service.acquire_desktop_lease(db, agent_id, args.get("ttl_seconds"))
        if action == "renew":
            return service.renew_desktop_lease(db, agent_id, args.get("ttl_seconds"))
        if action == "release":
            return service.release_desktop_lease(db, agent_id=agent_id)
        raise ToolError(f"未知的桌面操作：{action}", "TOOL_ERROR")
    finally:
        db.close()

REGISTRY = {
    "file_read": ToolSpec("file_read", "读文件", "读取任务工作区内的文件",
                          {"path": "string"}, ["fs:read"], 30),
    "file_write": ToolSpec("file_write", "写文件", "把内容写入任务工作区内的文件",
                           {"path": "string", "content": "string"}, ["fs:write"], 30),
    "run_command": ToolSpec("run_command", "执行命令", "在任务工作区中执行命令",
                            {"command": "string"}, ["exec"], 60, high_risk=True, requires_approval=True),
    "run_pytest": ToolSpec("run_pytest", "运行测试", "在工作区目录中运行自动化测试",
                           {"path": "string"}, ["exec", "test"], 120),
    "linux_exec": ToolSpec("linux_exec", "共享电脑执行命令",
                           "在唯一的共享 Linux 环境中，通过员工自己的终端会话执行命令",
                           {"agent_id": "int", "command": "string"}, ["exec"], 120,
                           high_risk=True, requires_approval=True),
    "linux_file_read": ToolSpec("linux_file_read", "读共享文件",
                                "读取共享目录 /workspace 中的文件",
                                {"path": "string"}, ["fs:read"], 30),
    "linux_file_write": ToolSpec("linux_file_write", "写共享文件",
                                 "把内容提交为共享目录 /workspace 的待审变更，用户在 AI 电脑页接受后才真正写入并做版本校验",
                                 {"agent_id": "int", "path": "string", "content": "string"}, ["fs:write"], 30),
    "linux_file_list": ToolSpec("linux_file_list", "列共享文件",
                                "列出共享目录 /workspace 中的文件",
                                {"path": "string"}, ["fs:read"], 30),
    "desktop_control": ToolSpec("desktop_control", "共享桌面控制",
                                "申请、续期或释放唯一的共享桌面控制权",
                                {"agent_id": "int", "action": "string"}, ["desktop"], 30),
    "browser_open": ToolSpec("browser_open", "浏览器打开网址",
                             "在共享桌面上的真实浏览器里打开网址，用户能在远程桌面里亲眼看到",
                             {"agent_id": "int", "url": "string"}, ["browser"], 60),
    "browser_search": ToolSpec("browser_search", "浏览器搜索",
                               "用配置好的搜索引擎在共享桌面的浏览器里搜索",
                               {"agent_id": "int", "query": "string"}, ["browser"], 60),
    "browser_read": ToolSpec("browser_read", "读取浏览器页面",
                             "读取共享桌面浏览器当前页面的标题与文字",
                             {"agent_id": "int"}, ["browser"], 30),
    "browser_screenshot": ToolSpec("browser_screenshot", "浏览器截图",
                                   "给共享桌面浏览器当前页面截图并存证",
                                   {"agent_id": "int"}, ["browser"], 30),
}
_HANDLERS = {"file_read": _file_read, "file_write": _file_write,
             "run_command": _run_command, "run_pytest": _run_pytest,
             "linux_exec": _linux_exec, "linux_file_read": _linux_file_read,
             "linux_file_write": _linux_file_write, "linux_file_list": _linux_file_list,
             "desktop_control": _desktop_control,
             "browser_open": _browser_open, "browser_search": _browser_search,
             "browser_read": _browser_read, "browser_screenshot": _browser_screenshot}

def list_tools():
    return [vars(t) for t in REGISTRY.values()]

def check_permission(agent_tools, tool_id):
    return tool_id in (agent_tools or [])

def execute_tool(tool_id, args, agent_tools, approved=False):
    spec = REGISTRY.get(tool_id)
    if spec is None:
        raise ToolError(f"未知工具：{tool_id}", "TOOL_ERROR")
    if not check_permission(agent_tools, tool_id):
        raise ToolError(f"员工没有使用工具「{spec.name}」的权限", "PERMISSION_ERROR")
    if spec.requires_approval and not approved:
        raise ToolError(f"工具「{spec.name}」是高风险操作，需要人工审批后才能执行", "PERMISSION_ERROR")
    args = dict(args or {})
    if "timeout" in args:
        # The registry's per-tool timeout is a hard ceiling; a caller
        # supplied value may only shorten it.
        try:
            args["timeout"] = max(1, min(int(args["timeout"]), spec.timeout))
        except (TypeError, ValueError):
            args["timeout"] = spec.timeout
    try:
        return _HANDLERS[tool_id](args)
    except ToolError:
        raise
    except subprocess.TimeoutExpired:
        raise ToolError(f"工具「{spec.name}」执行超时", "TIMEOUT")
    except Exception as e:
        raise ToolError(f"工具「{spec.name}」执行失败：{e}", "TOOL_ERROR")
