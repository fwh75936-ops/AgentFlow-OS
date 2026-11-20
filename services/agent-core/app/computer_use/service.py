"""Computer Use loop: observe -> permission check -> act -> observe -> verify.

HONESTY RULE: an action is only reported as executed when a real tool
(xdotool / scrot) actually ran it against a live X display. The shared
desktop runs on display :1 (TigerVNC + XFCE, see app/workspace/desktop.py);
when the backend process itself has no DISPLAY, the effective display is
:1 whenever that VNC server is really running. If xdotool is missing or no
display is live, act() returns status="unavailable" with executed=False —
never a fake success. Every act() is gated by the desktop lease: only the
lease-holding agent may act, and after a user force-takeover all agent
actions are rejected.
"""
import os, shutil, subprocess, time
from app.workspace import desktop, service as ws

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SHOTS_DIR = os.path.join(_BASE_DIR, "data", "computer_use_shots")


def _effective_display():
    """DISPLAY of the backend process wins; otherwise fall back to the shared
    desktop's :1 when (and only when) its VNC server is actually running."""
    if os.environ.get("DISPLAY"):
        return os.environ["DISPLAY"]
    if desktop._vnc_running():
        return desktop.DISPLAY
    return None


def _tools():
    return {"xdotool": shutil.which("xdotool"), "scrot": shutil.which("scrot"),
            "display": _effective_display()}


def _xenv(display):
    env = dict(os.environ)
    env["DISPLAY"] = display
    return env


def _run(tool_path, args, display, timeout=15):
    return subprocess.run([tool_path] + args, capture_output=True, text=True,
                          timeout=timeout, env=_xenv(display))


def mouse_location():
    t = _tools()
    if not (t["xdotool"] and t["display"]):
        return {"available": False}
    try:
        proc = _run(t["xdotool"], ["getmouselocation", "--shell"], t["display"])
    except subprocess.TimeoutExpired:
        return {"available": False, "reason": "xdotool 超时未响应"}
    if proc.returncode != 0:
        return {"available": False, "stderr": proc.stderr[-200:]}
    out = {}
    for line in proc.stdout.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            out[k.lower()] = int(v) if v.lstrip("-").isdigit() else v
    return {"available": True, **out}


def take_screenshot(display=None):
    """Real screenshot via scrot. Returns dict with path on success."""
    t = _tools()
    display = display or t["display"]
    if not t["scrot"]:
        return {"available": False, "reason": "未安装 scrot"}
    if not display:
        return {"available": False, "reason": "没有可用的图形显示"}
    os.makedirs(SHOTS_DIR, exist_ok=True)
    import uuid
    path = os.path.join(SHOTS_DIR, f"shot-{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}.png")
    proc = _run(t["scrot"], [path], display, timeout=20)
    if proc.returncode != 0 or not os.path.exists(path):
        return {"available": False, "reason": (proc.stderr or "截图失败")[-300:]}
    return {"available": True, "tool": "scrot", "path": path,
            "bytes": os.path.getsize(path)}


def observe(db):
    t = _tools()
    shot = {"available": bool(t["scrot"] and t["display"]),
            "reason": None if (t["scrot"] and t["display"]) else
                      ("未安装 scrot" if not t["scrot"] else "没有可用的图形显示")}
    windows = {"available": bool(t["xdotool"] and t["display"]),
               "reason": None if t["xdotool"] else "未安装 xdotool"}
    return {"desktop": desktop.status(), "lease": ws.current_lease(db),
            "tools": t, "screenshot": shot, "windows": windows,
            "mouse": mouse_location()}


def check_permission(db, agent_id):
    lease = ws.current_lease(db)
    if lease.get("holder_type") == "agent" and lease.get("holder_agent_id") == agent_id:
        return True, "员工持有桌面控制权"
    if lease.get("holder_type") == "user":
        return False, "桌面正由用户接管，员工动作已被禁止"
    return False, "员工未持有桌面控制权"


# Risk grading (idea adapted from OpenHands' confirmation-mode design,
# original implementation): ordinary pointer/typing actions are low risk;
# a small set of destructive key chords is HIGH risk and is parked as
# "awaiting_confirmation" unless the caller explicitly passes approved=True.
_KEY_ALIASES = {"control": "ctrl", "mod1": "alt", "mod4": "super",
                "mod5": "alt", "command": "super", "cmd": "super",
                "win": "super", "meta": "super"}
_MODIFIERS = ("ctrl", "alt", "shift", "super")


def _normalize_key(key) -> str:
    """Canonical chord form: aliases resolved, modifiers sorted, so
    'Control+Alt+Delete', 'alt+ctrl+Delete' and 'ctrl+alt+delete' are
    the SAME chord for risk grading."""
    parts = [p for p in str(key or "").replace(" ", "").lower().split("+") if p]
    parts = [_KEY_ALIASES.get(p, p) for p in parts]
    mods = sorted(p for p in parts if p in _MODIFIERS)
    rest = [p for p in parts if p not in _MODIFIERS]
    return "+".join(mods + rest)


HIGH_RISK_KEYS = {_normalize_key(k) for k in
                  ("ctrl+alt+delete", "ctrl+alt+backspace", "alt+f4")}

def action_risk(action, args=None):
    args = args or {}
    if action == "key" and _normalize_key(args.get("key", "")) in HIGH_RISK_KEYS:
        return "high"
    return "low"


def act(db, agent_id, action, args=None, approved=False):
    """action: screenshot|click|double_click|right_click|type|key|scroll|move"""
    ok, why = check_permission(db, agent_id)
    if not ok:
        return {"status": "rejected", "executed": False, "action": action, "reason": why}
    if action_risk(action, args) == "high" and not approved:
        return {"status": "awaiting_confirmation", "executed": False, "action": action,
                "risk": "high",
                "reason": "高风险按键组合已暂存，待人工确认后执行"}
    t = _tools()
    args = args or {}
    if action == "screenshot":
        if not t["scrot"]:
            return {"status": "unavailable", "executed": False, "action": action,
                    "reason": "未安装 scrot，截图未执行"}
        if not t["display"]:
            return {"status": "unavailable", "executed": False, "action": action,
                    "reason": "没有可用的图形显示，截图未执行"}
        shot = take_screenshot(t["display"])
        if not shot.get("available"):
            return {"status": "unavailable", "executed": False, "action": action,
                    "reason": shot.get("reason")}
        return {"status": "executed", "executed": True, "action": action,
                "screenshot": shot}
    if not t["xdotool"]:
        return {"status": "unavailable", "executed": False, "action": action,
                "reason": "本机未安装 xdotool，界面动作未执行"}
    if not t["display"]:
        return {"status": "unavailable", "executed": False, "action": action,
                "reason": "没有可用的图形显示，界面动作未执行"}
    if action not in ("click", "double_click", "right_click", "move",
                      "type", "key", "scroll"):
        return {"status": "rejected", "executed": False, "action": action, "reason": "未知的动作"}
    # Actuation is serialised: a coordinate click is move+click, and a
    # concurrent move from another call must not slip in between them
    # and redirect the click.
    with _act_lock:
        return _act_locked(t, action, args)


import threading
_act_lock = threading.Lock()


def _act_locked(t, action, args):
    cmds = {"click": ["click", "1"], "double_click": ["click", "--repeat", "2", "1"],
            "right_click": ["click", "3"],
            "move": ["mousemove", str(args.get("x", 0)), str(args.get("y", 0))],
            "type": ["type", "--delay", "12", "--", str(args.get("text", ""))],
            "key": ["key", str(args.get("key", "Return"))],
            "scroll": ["click", "4"]}
    # click-family actions may carry target coordinates: move first, then click.
    if action in ("click", "double_click", "right_click") and ("x" in args or "y" in args):
        try:
            mv = _run(t["xdotool"], ["mousemove", str(args.get("x", 0)), str(args.get("y", 0))],
                      t["display"])
        except subprocess.TimeoutExpired:
            return {"status": "failed", "executed": False, "action": action,
                    "reason": "移动鼠标超时，点击未执行"}
        if mv.returncode != 0:
            return {"status": "failed", "executed": False, "action": action,
                    "returncode": mv.returncode, "stderr": mv.stderr[-500:]}
    try:
        proc = _run(t["xdotool"], cmds[action], t["display"])
    except subprocess.TimeoutExpired:
        # The tool may have partially delivered the action (typing is
        # key-by-key): report honestly instead of pretending a clean
        # not-executed, which would invite a duplicating retry.
        return {"status": "failed", "executed": False, "possibly_executed": True,
                "action": action,
                "reason": "工具执行超时，动作可能已部分执行，请先观察桌面再决定是否重试"}
    if proc.returncode != 0 and "display" in (proc.stderr or "").lower() \
            and "open" in (proc.stderr or "").lower():
        return {"status": "unavailable", "executed": False, "action": action,
                "reason": "无法连接图形显示，动作未执行"}
    result = {"status": "executed" if proc.returncode == 0 else "failed",
              "executed": proc.returncode == 0, "action": action,
              "returncode": proc.returncode, "stderr": proc.stderr[-500:]}
    if action == "move" and proc.returncode == 0:
        result["mouse_after"] = mouse_location()
    return result


def run_cycle(db, agent_id, action, args=None):
    args = args or {}
    before = observe(db)
    result = act(db, agent_id, action, args)
    after = observe(db)
    # "verified" is reserved for effects that were actually CHECKED:
    # a screenshot verifies when its file exists; a move verifies when
    # the pointer really is at the target afterwards. Everything else
    # reports executed-but-unverified — a tool exiting 0 does not prove
    # the intended UI effect happened (a popup may have eaten the click).
    verified = False
    if result.get("executed") is True:
        if action == "screenshot":
            shot = result.get("screenshot") or {}
            verified = bool(shot.get("available") and shot.get("path")
                            and os.path.exists(shot["path"]))
        elif action == "move":
            m = result.get("mouse_after") or {}
            verified = (m.get("available") is True
                        and m.get("x") == args.get("x", 0)
                        and m.get("y") == args.get("y", 0))
    return {"observe_before": {"lease": before["lease"], "tools": before["tools"]},
            "act": result, "observe_after": {"lease": after["lease"]},
            "verified": verified,
            "note": "verified 只在效果被真实核对时为真（截图文件存在、指针到达目标位置）；"
                    "其余动作即使执行成功也记为未验证"}
