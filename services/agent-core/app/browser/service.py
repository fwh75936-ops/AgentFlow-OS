"""Shared-desktop browser: a REAL Chrome/Chromium window on the shared
XFCE desktop (display :1, the one users watch through the remote
desktop). Tools and the UI drive it through the Chrome DevTools
Protocol on a localhost port — navigation, page text and screenshots
all come from that visible browser instance, never from a headless
fetcher.

Control is lease-gated exactly like Computer Use: an agent may drive
the browser only while it holds the desktop lease (a free lease is
acquired for the acting agent; a user-held lease blocks agents).
Every action is written to the workspace event log (audit).

Configuration (browser_config row) genuinely steers launches:
homepage is the first page opened, the search engine builds search
URLs, the download directory is written into the profile preferences,
and the profile directory follows the persistence switches:
  persist off          -> throwaway profile, deleted when stopped
  persist on, shared   -> data/browser_profiles/shared
  persist on, per-agent-> data/browser_profiles/agent-<id>
"""
import base64
import json
import os
import shutil
import subprocess
import time
import urllib.parse
import urllib.request
from datetime import datetime

from app import models
from app.database import SessionLocal
from app.workspace import desktop as ws_desktop
from app.workspace import service as ws_service
from app.workspace.config import get_shared_root

CDP_PORT = 9223
CDP_BASE = f"http://127.0.0.1:{CDP_PORT}"
_BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA_DIR = os.environ.get("AGENTFLOW_DATA_DIR", os.path.join(_BASE_DIR, "data"))
SHOTS_DIR = os.path.join(DATA_DIR, "browser_shots")
PROFILES_ROOT = os.path.join(DATA_DIR, "browser_profiles")

SEARCH_ENGINES = {
    "baidu": ("百度", "https://www.baidu.com/s?wd={q}"),
    "bing": ("必应", "https://www.bing.com/search?q={q}"),
    "google": ("Google", "https://www.google.com/search?q={q}"),
    "duckduckgo": ("DuckDuckGo", "https://duckduckgo.com/?q={q}"),
    "sogou": ("搜狗", "https://www.sogou.com/web?query={q}"),
}

_proc: subprocess.Popen | None = None
_proc_profile: str | None = None
_proc_agent: int | None = None
# Whether the RUNNING instance's profile is throwaway — recorded at
# launch time. stop_browser must decide cleanup from this record, never
# from whatever config happens to be current when it is called.
_proc_ephemeral: bool = False

import threading
_lifecycle_lock = threading.RLock()

# CDP is a loopback interface: never route it through a proxy taken from
# the environment (a proxy without a 127.0.0.1 exclusion breaks local
# Chrome detection while the browser is in fact running).
_NO_PROXY_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class BrowserError(Exception):
    pass


# ------------------------------------------------------------------- config

def get_config(db) -> models.BrowserConfig:
    row = db.query(models.BrowserConfig).get(1)
    if row is None:
        row = models.BrowserConfig(id=1)
        db.add(row)
        try:
            db.commit()
        except Exception:
            # A concurrent first-touch inserted id=1 first; use theirs.
            db.rollback()
            row = db.query(models.BrowserConfig).get(1)
            if row is None:
                raise
            return row
        db.refresh(row)
    return row


def config_out(row) -> dict:
    return {"homepage": row.homepage, "search_engine": row.search_engine,
            "search_engine_label": SEARCH_ENGINES.get(row.search_engine, ("", ""))[0],
            "download_dir": row.download_dir,
            "persist_profile": bool(row.persist_profile),
            "profile_per_agent": bool(row.profile_per_agent)}


def _valid_homepage(value) -> bool:
    if value in ("", "about:blank"):
        return True
    if not isinstance(value, str):
        return False
    parsed = urllib.parse.urlparse(value)
    return parsed.scheme in ("http", "https") and bool(parsed.hostname)


def update_config(db, patch: dict) -> dict:
    row = get_config(db)
    if "search_engine" in patch and patch["search_engine"] not in SEARCH_ENGINES:
        raise BrowserError(f"不支持的搜索引擎：{patch['search_engine']}")
    if "homepage" in patch and not _valid_homepage(patch["homepage"]):
        raise BrowserError("主页必须是 http:// 或 https:// 网址（或留空）")
    for key in ("homepage", "search_engine", "download_dir",
                "persist_profile", "profile_per_agent"):
        if key in patch:
            setattr(row, key, patch[key])
    db.commit(); db.refresh(row)
    return config_out(row)


# ------------------------------------------------------------------ runtime

def find_binary() -> str | None:
    for name in ("google-chrome", "google-chrome-stable", "chromium",
                 "chromium-browser"):
        path = shutil.which(name)
        if path:
            return path
    return None


def _profile_dir(cfg, agent_id) -> str:
    if not cfg.persist_profile:
        return os.path.join(PROFILES_ROOT, "_ephemeral")
    if cfg.profile_per_agent and agent_id:
        return os.path.join(PROFILES_ROOT, f"agent-{agent_id}")
    return os.path.join(PROFILES_ROOT, "shared")


def _write_preferences(profile: str, cfg) -> None:
    default_dir = os.path.join(profile, "Default")
    os.makedirs(default_dir, exist_ok=True)
    prefs_path = os.path.join(default_dir, "Preferences")
    prefs = {}
    if os.path.isfile(prefs_path):
        try:
            prefs = json.loads(open(prefs_path, encoding="utf-8").read())
        except Exception:
            prefs = {}
    download_dir = cfg.download_dir or get_shared_root()
    prefs.setdefault("download", {})["default_directory"] = download_dir
    prefs["download"]["prompt_for_download"] = False
    prefs["homepage"] = cfg.homepage
    prefs["homepage_is_newtabpage"] = False
    prefs.setdefault("session", {})["restore_on_startup"] = 4
    prefs["session"]["startup_urls"] = [cfg.homepage]
    with open(prefs_path, "w", encoding="utf-8") as f:
        json.dump(prefs, f, ensure_ascii=False)


def _cdp_alive() -> bool:
    try:
        with _NO_PROXY_OPENER.open(CDP_BASE + "/json/version", timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


def _targets() -> list:
    with _NO_PROXY_OPENER.open(CDP_BASE + "/json/list", timeout=3) as r:
        return json.loads(r.read())


def _page_target() -> dict | None:
    for t in _targets():
        if t.get("type") == "page" and t.get("webSocketDebuggerUrl"):
            return t
    return None


def _cdp_call(method: str, params: dict | None = None, timeout: float = 15.0):
    """One-shot CDP call against the current page target over websocket."""
    from websockets.sync.client import connect as ws_connect
    target = _page_target()
    if target is None:
        raise BrowserError("浏览器还没有打开的页面")
    with ws_connect(target["webSocketDebuggerUrl"],
                    open_timeout=5, close_timeout=2) as ws:
        ws.send(json.dumps({"id": 1, "method": method, "params": params or {}}))
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                msg = json.loads(ws.recv(timeout=max(1.0, deadline - time.time())))
            except TimeoutError:
                raise BrowserError("浏览器调用超时")
            if msg.get("id") == 1:
                if "error" in msg:
                    raise BrowserError(f"浏览器调用失败：{msg['error'].get('message', msg['error'])}")
                return msg.get("result", {})
    raise BrowserError("浏览器调用超时")


def _audit(db, agent_id, detail):
    ws_service.emit_event(db, "browser_action", agent_id=agent_id, detail=detail)


def _gate_lease(db, agent_id):
    """Same rule as Computer Use, with one convenience: a completely
    free lease is acquired for the acting agent instead of failing."""
    lease = ws_service.current_lease(db)
    if lease.get("holder_type") == "agent" and lease.get("holder_agent_id") == agent_id:
        return
    if lease.get("holder_type") == "user":
        raise BrowserError("桌面正被用户控制，员工暂时不能操作浏览器")
    if lease.get("holder_type") == "agent":
        raise BrowserError(f"桌面正被员工 {lease.get('holder_agent_id')} 控制，浏览器暂时不可用")
    ws_service.acquire_desktop_lease(db, agent_id)


def ensure_browser(db, cfg, agent_id=None) -> dict:
    """Start the desktop (if needed) and the browser (if needed), then
    return the live status. Raises BrowserError with the real reason."""
    with _lifecycle_lock:
        return _ensure_browser_locked(db, cfg, agent_id)


def _ensure_browser_locked(db, cfg, agent_id=None) -> dict:
    global _proc, _proc_profile, _proc_agent, _proc_ephemeral
    binary = find_binary()
    if not binary:
        raise BrowserError("这台电脑还没有安装 Chrome 或 Chromium 浏览器")
    # A live X display is what the browser actually needs; the desktop
    # service's own start path additionally wants the full XFCE/noVNC
    # stack, which may be absent while a VNC server is already serving
    # the same display (e.g. started by the user or the OS image).
    display_live = (ws_desktop.status().get("state") == "running"
                    or ws_desktop._vnc_running())
    if not display_live:
        result = ws_desktop.start()
        if not result.get("started") and ws_desktop.status().get("state") != "running":
            detail = result.get("error")
            if not detail and result.get("missing"):
                detail = "缺少桌面组件：" + "、".join(result["missing"])
            raise BrowserError(f"共享桌面没有启动起来：{detail or '未知原因'}")
    profile = _profile_dir(cfg, agent_id)
    if _cdp_alive():
        if _proc_profile == profile:
            return status(db)
        stop_browser()  # profile mode changed: restart under the new profile
    os.makedirs(profile, exist_ok=True)
    _write_preferences(profile, cfg)
    env = {**os.environ, "DISPLAY": ws_desktop.DISPLAY}
    argv = [binary, f"--remote-debugging-port={CDP_PORT}",
            f"--user-data-dir={profile}", "--no-first-run",
            "--no-default-browser-check", "--start-maximized",
            "--disable-dev-shm-usage"]
    # Chrome refuses to start as root with its sandbox on; only relax
    # it in that case (development VMs) — regular user installs keep
    # the sandbox untouched.
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        argv.append("--no-sandbox")
    # Honor the standard proxy environment (corporate / sandboxed
    # networks): pass only scheme://host:port — embedded credentials
    # are dropped, Chrome cannot use them from a flag anyway.
    proxy = (os.environ.get("HTTPS_PROXY") or os.environ.get("https_proxy")
             or os.environ.get("ALL_PROXY") or os.environ.get("all_proxy") or "")
    if proxy:
        parsed = urllib.parse.urlparse(proxy)
        if parsed.hostname:
            netloc = parsed.hostname + (f":{parsed.port}" if parsed.port else "")
            argv.append(f"--proxy-server={parsed.scheme or 'http'}://{netloc}")
    # A stored homepage that fails validation (legacy data) must never
    # reach the Chrome command line, where a leading "-" would be parsed
    # as a switch; fall back to a blank page instead.
    homepage = cfg.homepage if _valid_homepage(cfg.homepage) else "about:blank"
    argv.append(homepage or "about:blank")
    _proc = subprocess.Popen(argv, env=env,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    _proc_profile = profile
    _proc_agent = agent_id
    _proc_ephemeral = not cfg.persist_profile
    for _ in range(40):
        if _proc.poll() is not None:
            code = _proc.returncode
            _proc = None; _proc_profile = None; _proc_agent = None
            raise BrowserError(f"浏览器进程启动后立即退出（退出码 {code}）")
        if _cdp_alive():
            return status(db)
        time.sleep(0.5)
    # Timed out: do not leave the half-started process behind to hold
    # the profile directory and confuse the next attempt.
    try:
        _proc.kill(); _proc.wait(timeout=5)
    except Exception:
        pass
    _proc = None; _proc_profile = None; _proc_agent = None
    raise BrowserError("浏览器进程已启动，但调试接口没有响应")


def stop_browser(cfg=None) -> None:
    global _proc, _proc_profile, _proc_agent, _proc_ephemeral
    with _lifecycle_lock:
        if _proc is not None:
            _proc.terminate()
            try:
                _proc.wait(timeout=5)
            except Exception:
                _proc.kill()
                try:
                    _proc.wait(timeout=5)
                except Exception:
                    pass
            _proc = None
        else:
            # Only ever stop browsers carrying OUR profiles root in
            # their command line — never a user's own debugging Chrome.
            subprocess.run(["pkill", "-f",
                            f"remote-debugging-port={CDP_PORT}.*user-data-dir={PROFILES_ROOT}"],
                           capture_output=True)
        if _proc_ephemeral and _proc_profile:
            shutil.rmtree(_proc_profile, ignore_errors=True)
        _proc_profile = None
        _proc_agent = None
        _proc_ephemeral = False


def status(db) -> dict:
    cfg = get_config(db)
    binary = find_binary()
    running = _cdp_alive()
    page = {}
    if running:
        try:
            t = _page_target() or {}
            page = {"url": t.get("url", ""), "title": t.get("title", "")}
        except Exception:
            page = {}
    return {"installed": bool(binary), "binary": binary or "",
            "desktop_running": ws_desktop.status().get("state") == "running",
            "running": running, "page": page,
            "profile_dir": _proc_profile or _profile_dir(cfg, None),
            "config": config_out(cfg)}


# ------------------------------------------------------------------- actions

def _prepare(db, agent_id):
    cfg = get_config(db)
    if agent_id:
        _gate_lease(db, agent_id)
    return cfg


def open_url(db, url: str, agent_id=None) -> dict:
    parsed = urllib.parse.urlparse(url or "")
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise BrowserError("只允许打开带完整主机的 http:// 或 https:// 网址")
    with _lifecycle_lock:
        cfg = _prepare(db, agent_id)
        ensure_browser(db, cfg, agent_id)
        res = _cdp_call("Page.navigate", {"url": url})
        if res.get("errorText"):
            raise BrowserError(f"页面导航失败：{res['errorText']}")
        time.sleep(1.2)
        st = status(db)
        _audit(db, agent_id, f"打开网址 {url}")
        return {"url": st["page"].get("url", url), "title": st["page"].get("title", "")}


def search(db, query: str, agent_id=None) -> dict:
    cfg = _prepare(db, agent_id)
    _label, template = SEARCH_ENGINES.get(cfg.search_engine, SEARCH_ENGINES["baidu"])
    url = template.format(q=urllib.parse.quote(query))
    result = open_url(db, url, agent_id)
    _audit(db, agent_id, f"用{_label}搜索：{query}")
    return result


def read_page(db, agent_id=None) -> dict:
    cfg = _prepare(db, agent_id)
    ensure_browser(db, cfg, agent_id)
    res = _cdp_call("Runtime.evaluate", {
        "expression": "JSON.stringify({url: location.href, title: document.title, "
                      "text: (document.body ? document.body.innerText : '').slice(0, 8000)})",
        "returnByValue": True})
    if res.get("exceptionDetails"):
        raise BrowserError("页面脚本执行出错，没能读取页面文字")
    raw = (res.get("result") or {}).get("value") or "{}"
    data = json.loads(raw)
    _audit(db, agent_id, f"读取页面文字 {data.get('url', '')}")
    return data


def screenshot(db, agent_id=None) -> dict:
    cfg = _prepare(db, agent_id)
    ensure_browser(db, cfg, agent_id)
    res = _cdp_call("Page.captureScreenshot", {"format": "png"})
    data = base64.b64decode(res["data"])
    os.makedirs(SHOTS_DIR, exist_ok=True)
    import uuid
    name = f"shot-{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}.png"
    path = os.path.join(SHOTS_DIR, name)
    with open(path, "wb") as f:
        f.write(data)
    _audit(db, agent_id, f"浏览器截图 {name}")
    return {"path": path, "filename": name, "bytes": len(data)}


def clear_data(db) -> dict:
    """Wipe every browser profile (history, cookies, logins). The
    browser is stopped first so files are not locked by the process."""
    stop_browser()
    removed = 0
    failed = []
    if os.path.isdir(PROFILES_ROOT):
        for entry in os.listdir(PROFILES_ROOT):
            path = os.path.join(PROFILES_ROOT, entry)
            try:
                if os.path.isdir(path) and not os.path.islink(path):
                    shutil.rmtree(path)
                else:
                    os.remove(path)
                removed += 1
            except OSError:
                failed.append(entry)
    _audit(db, None, "清除全部浏览数据（历史、Cookie 与登录状态）"
           + (f"；{len(failed)} 项删除失败" if failed else ""))
    return {"cleared_profiles": removed, "failed": failed}
