"""Computer Use REAL GUI action tests (round-3 completion).

The gate test always runs. The GUI test runs only when xdotool + scrot are
installed; it starts the real shared desktop (TigerVNC + XFCE on :1) via
app.workspace.desktop, acquires the desktop lease, and performs real
xdotool actions: screenshot, mouse move (verified via getmouselocation),
click, and keyboard typing that executes a real shell command in a real
xfce4-terminal (proof file on disk). Before/after screenshots are copied
to docs/assets/ as durable evidence.
"""
import os, shutil, subprocess, time

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models
from app.database import Base
from app.computer_use import service as cu
from app.workspace import desktop, service as ws

HAVE_GUI_TOOLS = bool(shutil.which("xdotool") and shutil.which("scrot")
                      and shutil.which("xfce4-terminal"))
PROOF = "/tmp/agentflow_cu_proof.txt"


@pytest.fixture()
def dbx(tmp_path, monkeypatch):
    root = tmp_path / "shared"; root.mkdir()
    monkeypatch.setenv("AGENTFLOW_SHARED_ROOT", str(root))
    engine = create_engine("sqlite:///" + str(tmp_path / "cu.db"),
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    db = sessionmaker(bind=engine, autocommit=False, autoflush=False)()
    yield db
    db.close()


def mkagent(db, name):
    a = models.Agent(name=name, role="t", tools=["desktop_control"],
                     capability_tags=[], capability_scores={}, status="active")
    db.add(a); db.commit(); db.refresh(a); return a


def test_act_rejected_without_lease(dbx):
    a = mkagent(dbx, "no-lease")
    r = cu.act(dbx, a.id, "move", {"x": 10, "y": 10})
    assert r["status"] == "rejected" and r["executed"] is False
    r2 = cu.act(dbx, a.id, "screenshot")
    assert r2["status"] == "rejected" and r2["executed"] is False


def test_act_reasons_are_chinese(dbx):
    """Computer Use 的拒绝/不可用原因须为中文（工具名 xdotool/scrot 保留原名）。"""
    a = mkagent(dbx, "reason-zh")
    r = cu.act(dbx, a.id, "move", {"x": 10, "y": 10})
    assert r["reason"] == "员工未持有桌面控制权"
    ws.force_takeover_desktop(dbx)
    r2 = cu.act(dbx, a.id, "move", {"x": 10, "y": 10})
    assert r2["reason"] == "桌面正由用户接管，员工动作已被禁止"
    ws.release_desktop_lease(dbx, holder_type="user")
    ws.acquire_desktop_lease(dbx, a.id, ttl_seconds=600)
    r3 = cu.act(dbx, a.id, "bogus_action")
    # 无图形显示的沙箱先走到不可用分支，有显示时才走到未知动作分支；
    # 两种分支的原因都须是中文。
    assert r3["reason"] in ("未知的动作",
                           "没有可用的图形显示，界面动作未执行",
                           "本机未安装 xdotool，界面动作未执行")
    for bad in (r, r2, r3):
        assert not any(w in bad["reason"] for w in
                       ("not installed", "no live", "unknown action", "lease"))


@pytest.mark.skipif(not HAVE_GUI_TOOLS, reason="xdotool/scrot/xfce4-terminal not installed")
def test_real_gui_actions_on_shared_desktop(dbx):
    started = desktop.start()
    assert started["started"] is True, started
    assert desktop.status()["state"] == "running"
    display = desktop.DISPLAY

    a = mkagent(dbx, "cu-agent")
    ws.acquire_desktop_lease(dbx, a.id, ttl_seconds=600)

    # screenshot BEFORE (real scrot through the lease-gated act API)
    before = cu.act(dbx, a.id, "screenshot")
    assert before["executed"] is True and before["screenshot"]["bytes"] > 0
    before_path = before["screenshot"]["path"]

    # real mouse move, verified by re-reading the pointer position
    mv = cu.act(dbx, a.id, "move", {"x": 320, "y": 210})
    assert mv["executed"] is True, mv
    assert mv["mouse_after"]["x"] == 320 and mv["mouse_after"]["y"] == 210

    # open a REAL terminal on the shared desktop (test harness setup),
    # focus it with the harness, then let the AGENT click + type.
    env = dict(os.environ); env["DISPLAY"] = display
    term = subprocess.Popen(["xfce4-terminal", "--geometry=100x30+200+150"],
                            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(30):
            out = subprocess.run(["xdotool", "search", "--name", "Terminal"],
                                 capture_output=True, text=True, env=env)
            if out.returncode == 0 and out.stdout.strip():
                break
            time.sleep(0.5)
        subprocess.run(["xdotool", "search", "--name", "Terminal", "windowactivate", "--sync"],
                       capture_output=True, env=env)
        time.sleep(0.5)
        if os.path.exists(PROOF):
            os.remove(PROOF)
        click = cu.act(dbx, a.id, "click", {"x": 400, "y": 300})
        assert click["executed"] is True, click
        typ = cu.act(dbx, a.id, "type",
                     {"text": f"echo AGENTFLOW_CU_PROOF > {PROOF}"})
        assert typ["executed"] is True, typ
        key = cu.act(dbx, a.id, "key", {"key": "Return"})
        assert key["executed"] is True, key
        deadline = time.time() + 15
        content = ""
        while time.time() < deadline:
            if os.path.exists(PROOF):
                content = open(PROOF).read().strip()
                if content:
                    break
            time.sleep(0.3)
        assert content == "AGENTFLOW_CU_PROOF", f"typed command did not execute: {content!r}"

        # screenshot AFTER: terminal + typed command must change the pixels
        after = cu.act(dbx, a.id, "screenshot")
        assert after["executed"] is True
        after_path = after["screenshot"]["path"]
        assert open(before_path, "rb").read() != open(after_path, "rb").read()

        # durable evidence in docs/assets/
        repo_root = os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.dirname(os.path.abspath(__file__)))))
        assets = os.path.join(repo_root, "docs", "assets")
        os.makedirs(assets, exist_ok=True)
        shutil.copyfile(before_path, os.path.join(assets, "computer-use-before.png"))
        shutil.copyfile(after_path, os.path.join(assets, "computer-use-after.png"))
    finally:
        term.terminate()

    # high-risk key chord is parked for confirmation even with a lease
    hr = cu.act(dbx, a.id, "key", {"key": "ctrl+alt+Delete"})
    assert hr["status"] == "awaiting_confirmation" and hr["executed"] is False
    assert cu.action_risk("key", {"key": "Return"}) == "low"
    assert cu.action_risk("move", {"x": 1, "y": 1}) == "low"

    # user force-takeover => agent actions rejected even for screenshots
    ws.force_takeover_desktop(dbx)
    r = cu.act(dbx, a.id, "move", {"x": 1, "y": 1})
    assert r["status"] == "rejected" and r["executed"] is False
    ws.release_desktop_lease(dbx, holder_type="user")
