"""Regression tests for the 2026-11-19 round-9 backend sweep: desktop
start failures must report a Chinese reason, never a bare English
literal or a dumped Python dict."""
from types import SimpleNamespace

import pytest

from app.browser import service as browser_service
from app.workspace import desktop


def _isolate_desktop(monkeypatch, tmp_path, run_results):
    """Desktop start with no real binaries: components present, VNC not
    running, HOME redirected into tmp_path, subprocess faked."""
    monkeypatch.setattr(desktop, "_missing", lambda: [])
    monkeypatch.setattr(desktop, "_vnc_running", lambda: False)
    monkeypatch.setattr(desktop, "status", lambda: {"state": "stopped"})
    monkeypatch.setattr("os.path.expanduser", lambda p: str(tmp_path))
    monkeypatch.setattr(desktop.time, "sleep", lambda s: None)
    calls = {"n": 0}

    def fake_run(*args, **kwargs):
        res = run_results[min(calls["n"], len(run_results) - 1)]
        calls["n"] += 1
        return res

    monkeypatch.setattr(desktop.subprocess, "run", fake_run)


def test_desktop_vncpasswd_failure_is_chinese(monkeypatch, tmp_path):
    _isolate_desktop(monkeypatch, tmp_path, [
        SimpleNamespace(returncode=1, stdout=b"")])
    r = desktop.start()
    assert r["started"] is False
    assert r["error"] == "创建桌面密码文件失败"
    assert "vncpasswd" not in r["error"]


def test_desktop_vnc_server_failure_is_chinese(monkeypatch, tmp_path):
    _isolate_desktop(monkeypatch, tmp_path, [
        SimpleNamespace(returncode=0, stdout=b"hashed-password"),
        SimpleNamespace(returncode=0, stdout="", stderr="")])
    r = desktop.start()
    assert r["started"] is False
    assert r["error"] == "桌面服务启动失败"
    assert "vnc failed" not in r["error"]


def _mock_desktop_down(monkeypatch, start_result):
    monkeypatch.setattr(browser_service, "find_binary",
                        lambda: "/usr/bin/chromium")
    monkeypatch.setattr(browser_service.ws_desktop, "status",
                        lambda: {"state": "stopped"})
    monkeypatch.setattr(browser_service.ws_desktop, "_vnc_running",
                        lambda: False)
    monkeypatch.setattr(browser_service.ws_desktop, "start",
                        lambda: start_result)


def test_browser_error_reports_desktop_reason_not_dict(monkeypatch):
    _mock_desktop_down(monkeypatch, {
        "started": False, "error": "创建桌面密码文件失败",
        "status": {"state": "stopped"}})
    with pytest.raises(browser_service.BrowserError) as exc:
        browser_service._ensure_browser_locked(None, {})
    assert "创建桌面密码文件失败" in str(exc.value)
    assert "{'started'" not in str(exc.value)


def test_browser_error_names_missing_components(monkeypatch):
    _mock_desktop_down(monkeypatch, {
        "started": False, "missing": ["tigervncserver"],
        "status": {"state": "stopped"}})
    with pytest.raises(browser_service.BrowserError) as exc:
        browser_service._ensure_browser_locked(None, {})
    assert "缺少桌面组件：tigervncserver" in str(exc.value)
    assert "{'started'" not in str(exc.value)
