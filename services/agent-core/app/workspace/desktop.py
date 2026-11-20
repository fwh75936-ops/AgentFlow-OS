"""Shared graphical desktop runtime: XFCE on TigerVNC, viewed via noVNC.

Honesty rule: this module only reports/starts what is actually installed.
If XFCE / VNC / noVNC binaries are missing, status says so (state
``unavailable`` with the missing list) — no fake "running" state, no static
screenshot stand-in. In the Docker deployment (docker/linux-workspace) all
components are baked into the single shared container image.
"""
import os
import shutil
import signal
import subprocess
import time

from app.workspace import config

DISPLAY = ":1"
VNC_PORT = 5901
NOVNC_PORT = 6080
VNC_PASSWORD = "agentflow"


def _passwd_cmd():
    return shutil.which("vncpasswd") or shutil.which("tigervncpasswd")


def components():
    return {
        "xvfb_or_vncserver": bool(shutil.which("tigervncserver") or shutil.which("vncserver")),
        "xfce": bool(shutil.which("startxfce4")),
        "websockify": bool(shutil.which("websockify")),
        "novnc_web": os.path.isdir("/usr/share/novnc"),
        "vnc_passwd_tool": bool(_passwd_cmd()),
    }


def _missing():
    c = components()
    need = {"xvfb_or_vncserver": "tigervnc-standalone-server",
            "xfce": "xfce4-session (XFCE desktop)",
            "websockify": "websockify", "novnc_web": "novnc",
            "vnc_passwd_tool": "tigervnc-tools (vncpasswd)"}
    return [need[k] for k, v in c.items() if not v]


def _pid_alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except (OSError, TypeError):
        return False


def _websockify_pid():
    pid_file = os.path.join(config.get_runtime_dir(), "websockify.pid")
    try:
        with open(pid_file) as f:
            pid = int(f.read().strip())
    except (OSError, ValueError):
        return None
    if not _pid_alive(pid):
        return None
    # PIDs get recycled: a live pid is only OUR websockify when its
    # command line says so. Trusting the file blindly could report a
    # stranger process as the desktop bridge (and stop() would kill it).
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            if b"websockify" not in f.read():
                return None
    except OSError:
        pass  # no /proc (non-Linux dev host): fall back to liveness only
    return pid


def _vnc_running():
    try:
        out = subprocess.run(["pgrep", "-f", "Xtigervnc.*:1"], capture_output=True,
                             text=True, timeout=5)
        return out.returncode == 0 and bool(out.stdout.strip())
    except Exception:
        return False


def status():
    missing = _missing()
    vnc = _vnc_running()
    ws = _websockify_pid()
    if missing:
        state = "unavailable"
    elif vnc and ws:
        state = "running"
    elif vnc:
        state = "degraded"  # desktop itself runs; the web viewer bridge is down
    else:
        state = "stopped"
    return {
        "state": state,
        "display": DISPLAY,
        "desktop_environment": "XFCE",
        "vnc_server": "TigerVNC",
        "vnc_port": VNC_PORT,
        "vnc_localhost_only": True,
        "novnc_port": NOVNC_PORT,
        "novnc_url": f"http://127.0.0.1:{NOVNC_PORT}/vnc.html" if ws else None,
        "vnc_password": VNC_PASSWORD,
        "websockify_running": bool(ws),
        "components": components(),
        "missing": missing,
    }


def start():
    missing = _missing()
    if missing:
        return {"started": False, "missing": missing, "status": status()}
    if _vnc_running():
        _ensure_websockify()
        return {"started": True, "already_running": True, "status": status()}
    home = os.path.expanduser("~")
    vnc_dir = os.path.join(home, ".vnc")
    os.makedirs(vnc_dir, exist_ok=True)
    xstartup = os.path.join(vnc_dir, "xstartup")
    with open(xstartup, "w") as f:
        f.write("#!/bin/sh\nunset SESSION_MANAGER\nunset DBUS_SESSION_BUS_ADDRESS\n"
                "exec startxfce4\n")
    os.chmod(xstartup, 0o755)
    passwd_file = os.path.join(vnc_dir, "passwd")
    # Local single-user trust boundary: the VNC server binds localhost
    # only and noVNC is served on 127.0.0.1 only, so the password's job
    # is just to keep other local processes out. It is a FIXED local
    # password, shown in the desktop status of the user's own backend —
    # a random one (the previous behaviour) left the user permanently
    # locked out of their own desktop, with no way to ever learn it.
    pw = VNC_PASSWORD
    # vncpasswd output is BINARY (obfuscated password bytes) — no text mode.
    proc = subprocess.run([_passwd_cmd(), "-f"], input=pw.encode(),
                          capture_output=True)
    if proc.returncode != 0 or not proc.stdout:
        return {"started": False, "error": "创建桌面密码文件失败",
                "status": status()}
    with open(passwd_file, "wb") as f:
        f.write(proc.stdout)
    os.chmod(passwd_file, 0o600)
    server = shutil.which("tigervncserver") or shutil.which("vncserver")
    try:
        proc = subprocess.run([server, DISPLAY, "-geometry", "1280x800", "-depth", "24",
                               "-localhost", "yes"],
                              capture_output=True, text=True, timeout=60)
    except Exception as e:
        return {"started": False, "error": f"启动桌面服务失败：{e}"[-500:],
                "status": status()}
    time.sleep(2)
    if not _vnc_running():
        return {"started": False, "error": (proc.stderr or proc.stdout or "桌面服务启动失败")[-500:],
                "status": status()}
    _ensure_websockify()
    return {"started": True, "status": status()}


def _ensure_websockify():
    if _websockify_pid():
        return
    if not (shutil.which("websockify") and os.path.isdir("/usr/share/novnc")):
        return
    runtime = config.get_runtime_dir()
    log = open(os.path.join(runtime, "websockify.log"), "ab")
    proc = subprocess.Popen(["websockify", "--web=/usr/share/novnc",
                             f"127.0.0.1:{NOVNC_PORT}", f"localhost:{VNC_PORT}"],
                            stdout=log, stderr=log, start_new_session=True)
    with open(os.path.join(runtime, "websockify.pid"), "w") as f:
        f.write(str(proc.pid))
    # A websockify that dies instantly (port taken, bad args) must not
    # leave a pid file behind that later reads as "running".
    time.sleep(0.3)
    if proc.poll() is not None:
        try:
            os.unlink(os.path.join(runtime, "websockify.pid"))
        except OSError:
            pass


def stop():
    ws = _websockify_pid()
    if ws:
        try:
            os.kill(ws, signal.SIGTERM)
        except OSError:
            pass
    server = shutil.which("tigervncserver") or shutil.which("vncserver")
    if server and _vnc_running():
        subprocess.run([server, "-kill", DISPLAY], capture_output=True, text=True, timeout=30)
    return {"stopped": True, "status": status()}
