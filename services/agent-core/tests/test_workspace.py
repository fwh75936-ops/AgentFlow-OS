"""Shared Linux Workspace tests: one env, isolated sessions, audit logs,
desktop lease, shared files + conflicts, tool-registry integration.
All assertions run against real subprocesses and a real (temporary) SQLite DB
and shared directory — no mocked execution."""
import os
import threading
import time

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models
from app.database import Base
from app.tools.registry import execute_tool, list_tools, ToolError
from app.workspace import config, service


@pytest.fixture()
def ws(tmp_path, monkeypatch):
    root = tmp_path / "shared"
    root.mkdir()
    monkeypatch.setenv("AGENTFLOW_SHARED_ROOT", str(root))
    engine = create_engine("sqlite:///" + str(tmp_path / "ws.db"),
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    db = factory()
    yield db, factory, str(root)
    db.close()


def make_agent(db, name, tools, caps=None, scores=None):
    a = models.Agent(name=name, role="test", tools=tools,
                     capability_tags=caps or [], capability_scores=scores or {},
                     status="active")
    db.add(a)
    db.commit()
    db.refresh(a)
    return a


LINUX_TOOLS = ["linux_exec", "linux_file_read", "linux_file_write",
               "linux_file_list", "desktop_control", "run_pytest"]


def test_single_env_shared_by_all_agents(ws):
    db, _factory, root = ws
    agents = [make_agent(db, n, LINUX_TOOLS) for n in ("产品经理", "开发工程师", "测试工程师")]
    sessions = [service.create_session(db, a.id) for a in agents]
    assert {s.env_id for s in sessions} == {config.ENV_ID}  # ONE environment
    status = service.env_status(db)
    assert status["env_id"] == config.ENV_ID
    assert status["logical_root"] == "/workspace"
    assert status["shared_root"] == os.path.abspath(root)
    assert status["active_sessions"] == 3


def test_exec_records_full_audit_log(ws):
    db, _factory, _root = ws
    dev = make_agent(db, "开发工程师", LINUX_TOOLS)
    s = service.create_session(db, dev.id, task_id=42)
    res = service.exec_in_session(db, s, "echo hello-workspace")
    assert res["exit_code"] == 0 and "hello-workspace" in res["stdout"]
    logs = service.list_command_logs(db, session_id=s.id)
    assert len(logs) == 1
    row = logs[0]
    assert row.agent_id == dev.id and row.task_id == 42
    assert row.command == "echo hello-workspace" and row.exit_code == 0
    assert row.started_at is not None and row.finished_at is not None
    assert "hello-workspace" in row.stdout


def test_sessions_isolated_cwd_and_concurrent(ws):
    db, factory, root = ws
    dev = make_agent(db, "开发工程师", LINUX_TOOLS)
    tester = make_agent(db, "测试工程师", LINUX_TOOLS)
    s1 = service.create_session(db, dev.id)
    s2 = service.create_session(db, tester.id)
    service.exec_in_session(db, s1, "mkdir -p proj && cd proj")
    r1 = service.exec_in_session(db, s1, "pwd")
    r2 = service.exec_in_session(db, s2, "pwd")
    assert r1["stdout"].strip().endswith("proj")  # s1 kept its own cwd
    assert r2["stdout"].strip() == os.path.abspath(root)  # s2 unaffected

    # Different sessions really run concurrently (separate DB sessions/threads).
    def run_in_own_session(agent_id, marker):
        d = factory()
        try:
            sess = service.create_session(d, agent_id)
            return service.exec_in_session(d, sess, f"sleep 1 && echo {marker}")
        finally:
            d.close()

    t0 = time.time()
    results = {}
    threads = [threading.Thread(
        target=lambda a=a, m=m: results.update({m: run_in_own_session(a, m)}))
        for a, m in ((dev.id, "dev-done"), (tester.id, "tester-done"))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    elapsed = time.time() - t0
    assert all(r["exit_code"] == 0 for r in results.values())
    assert elapsed < 1.8, f"sessions appear serialised: {elapsed:.2f}s"


def test_high_risk_command_blocked_without_approval(ws):
    db, _factory, _root = ws
    dev = make_agent(db, "开发工程师", LINUX_TOOLS)
    s = service.create_session(db, dev.id)
    res = service.exec_in_session(db, s, "docker ps")
    assert res["blocked"] is True and res["exit_code"] is None
    log = service.list_command_logs(db, session_id=s.id)[0]
    assert log.status == "blocked"
    # With explicit approval the block lifts (docker is absent here -> rc 127,
    # which is the honest result, not a fake success).
    res2 = service.exec_in_session(db, s, "docker ps", approved=True)
    assert res2["blocked"] is False and res2["exit_code"] not in (None, 0)


def test_shared_files_visible_and_conflict_coordination(ws):
    db, _factory, root = ws
    dev = make_agent(db, "开发工程师", LINUX_TOOLS)
    tester = make_agent(db, "测试工程师", LINUX_TOOLS)
    out = service.write_file(db, dev.id, "proj/app.py", "x = 1\n")
    assert out["version"] == 1
    # The file really exists on the one shared filesystem, readable by another agent.
    assert os.path.isfile(os.path.join(root, "proj", "app.py"))
    read = service.read_file(db, "proj/app.py")
    assert read["content"] == "x = 1\n" and read["version"] == 1
    # Optimistic concurrency: dev bumps to v2, tester's write based on v1 conflicts.
    service.write_file(db, dev.id, "proj/app.py", "x = 2\n", expected_version=1)
    with pytest.raises(service.ConflictError):
        service.write_file(db, tester.id, "proj/app.py", "x = 3\n", expected_version=1)
    # Advisory lock: while dev holds the lock, tester cannot write at all.
    service.acquire_file_lock(db, dev.id, "proj/app.py")
    with pytest.raises(service.ConflictError):
        service.write_file(db, tester.id, "proj/app.py", "x = 3\n")
    service.release_file_lock(db, dev.id, "proj/app.py")
    ok = service.write_file(db, tester.id, "proj/app.py", "x = 3\n", expected_version=2)
    assert ok["version"] == 3
    types = [e.event_type for e in service.list_events(db, limit=50)]
    assert "file_created" in types and "file_conflict" in types


def test_desktop_lease_no_conflict_wait_timeout_force_takeover(ws):
    db, _factory, _root = ws
    a = make_agent(db, "开发工程师", LINUX_TOOLS)
    b = make_agent(db, "测试工程师", LINUX_TOOLS)
    r1 = service.acquire_desktop_lease(db, a.id, ttl_seconds=60)
    assert r1["state"] == "held"
    r2 = service.acquire_desktop_lease(db, b.id, ttl_seconds=60)
    assert r2["state"] == "waiting" and r2["position"] == 1  # no double control
    with pytest.raises(ValueError):
        service.renew_desktop_lease(db, b.id)  # non-holder cannot renew
    rel = service.release_desktop_lease(db, agent_id=a.id)
    assert rel["granted_agent_id"] == b.id  # FIFO handover
    assert service.current_lease(db)["holder_agent_id"] == b.id
    # User force takeover revokes the agent's control immediately.
    take = service.force_takeover_desktop(db)
    assert take["state"] == "held_by_user"
    assert service.current_lease(db)["holder_type"] == "user"
    r3 = service.acquire_desktop_lease(db, b.id, ttl_seconds=60)
    assert r3["state"] == "waiting"
    service.release_desktop_lease(db, holder_type="user")
    assert service.current_lease(db)["holder_agent_id"] == b.id
    # Timeout: a 1-second lease expires and frees the desktop.
    service.release_desktop_lease(db, agent_id=b.id)
    service.acquire_desktop_lease(db, a.id, ttl_seconds=1)
    time.sleep(1.2)
    assert service.current_lease(db)["holder_agent_id"] is None
    types = [e.event_type for e in service.list_events(db, limit=100)]
    for expected in ("desktop_lease_acquired", "desktop_lease_waiting",
                     "desktop_lease_released", "desktop_force_takeover",
                     "desktop_lease_expired"):
        assert expected in types, expected


def test_linux_tools_registered_and_permission_gated(ws):
    ids = {t["id"] for t in list_tools()}
    assert {"linux_exec", "linux_file_read", "linux_file_write",
            "linux_file_list", "desktop_control"} <= ids
    # Agent without the tool is denied by the unified registry.
    with pytest.raises(ToolError):
        execute_tool("linux_exec", {"agent_id": 1, "command": "echo x"},
                     agent_tools=[], approved=True)
    # High-risk tool without human approval is denied even with permission.
    with pytest.raises(ToolError):
        execute_tool("linux_exec", {"agent_id": 1, "command": "echo x"},
                     agent_tools=["linux_exec"], approved=False)


def test_path_escape_rejected(ws):
    db, _factory, _root = ws
    dev = make_agent(db, "开发工程师", LINUX_TOOLS)
    with pytest.raises(ValueError):
        service.write_file(db, dev.id, "../outside.txt", "nope")
    with pytest.raises(ValueError):
        service.read_file(db, "/etc/passwd")


def test_env_status_name_is_chinese(ws):
    # 工作室「共享 Linux 电脑」卡直接显示 env_status 的 name；英文原名
    # "Shared Linux Workspace" 会原样漏到界面上。
    db, _factory, _root = ws
    assert service.env_status(db)["name"] == "共享 Linux 电脑"
