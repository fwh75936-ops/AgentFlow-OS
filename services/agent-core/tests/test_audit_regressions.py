"""Regression tests for the 2026-11-18 full-backend audit fixes.

Each test pins one confirmed bug from the audit so it cannot silently
return: atomic task claiming, DAG incompleteness, strict approval
booleans, verification honesty, lease expiry handoff, tool path
confinement, MCP tool namespacing, mention overlap, route validation,
process-group timeout kills and dependency-install target pinning.
"""
import json
import os
import time
from datetime import datetime, timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models
from app.database import Base


@pytest.fixture()
def dbx(tmp_path, monkeypatch):
    root = tmp_path / "shared"; root.mkdir()
    monkeypatch.setenv("AGENTFLOW_SHARED_ROOT", str(root))
    engine = create_engine("sqlite:///" + str(tmp_path / "audit.db"),
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    # The runner/chief open their own sessions from app.database.SessionLocal;
    # point it at this test database like the other suites do.
    from app import database
    monkeypatch.setattr(database, "SessionLocal", factory)
    db = factory()
    yield db
    db.close()


def mkagent(db, name, tools=None, caps=None, **kw):
    a = models.Agent(name=name, role="t", tools=tools or [],
                     capability_tags=caps or [], capability_scores={},
                     status="active", **kw)
    db.add(a); db.commit(); db.refresh(a); return a


def mktask(db, title="t", status="pending"):
    t = models.Task(title=title, status=status)
    db.add(t); db.commit(); db.refresh(t); return t


# ---------------------------------------------------------- runner

def test_run_task_rejects_second_claim(dbx):
    from app.execution.runner import run_task
    t = mktask(dbx, status="running")
    with pytest.raises(RuntimeError):
        run_task(dbx, t)


def test_dag_with_unreachable_node_fails_task(dbx):
    from app.execution.runner import run_task
    t = mktask(dbx)
    dbx.add(models.TaskNode(task_id=t.id, name="孤儿节点", node_type="agent",
                            dependencies=[999999], status="pending"))
    dbx.commit()
    run_task(dbx, t)
    dbx.refresh(t)
    assert t.status == "failed"  # never reported as success


def test_tool_node_string_approval_does_not_execute(dbx):
    from app.execution.runner import run_task
    a = mkagent(dbx, "执行者", tools=["linux_exec"])
    t = mktask(dbx)
    cfg = {"tool_id": "linux_exec",
           "tool_args": {"command": "echo SHOULD_NOT_RUN"},
           "approved": "false"}  # string must not count as approval
    dbx.add(models.TaskNode(task_id=t.id, name="命令节点", node_type="tool",
                            input_data=json.dumps(cfg), status="pending"))
    dbx.commit()
    run_task(dbx, t)
    node = dbx.query(models.TaskNode).filter_by(task_id=t.id).first()
    assert node.status == "failed"
    assert "SHOULD_NOT_RUN" not in (node.output_data or "")


# ---------------------------------------------------------- verification

def test_record_verification_requires_registered_artifact(dbx):
    from app.verification import state as vs
    with pytest.raises(ValueError):
        vs.record_verification(dbx, "outputs/never-written.md", True)


def test_structural_required_fields_rejects_non_dict(dbx):
    from app.verification.engine import structural_verify
    r = structural_verify(["a", "b"], ["标题"])
    assert not r.passed


def test_rule_verify_unknown_op_and_missing_field_fail(dbx):
    from app.verification.engine import rule_verify
    assert not rule_verify({"x": 1}, [{"field": "x", "op": "approx", "value": 1}]).passed
    assert not rule_verify({}, [{"field": "缺失", "op": "gte", "value": 1}]).passed


def test_record_route_rejects_string_passed(dbx):
    from app.verification.routes import record
    with pytest.raises(HTTPException) as ei:
        record({"artifact_path": "x.md", "passed": "false"}, dbx)
    assert ei.value.status_code == 422


# ---------------------------------------------------------- workspace leases

def test_expired_desktop_lease_hands_over(dbx):
    from app.workspace import service as ws
    a = mkagent(dbx, "甲"); b = mkagent(dbx, "乙")
    ws.acquire_desktop_lease(dbx, a.id, ttl_seconds=60)
    ws.acquire_desktop_lease(dbx, b.id, ttl_seconds=60)  # queued
    lease = dbx.query(models.DesktopLease).filter_by(holder_agent_id=a.id).first()
    lease.expires_at = datetime.utcnow() - timedelta(seconds=1)
    dbx.commit()
    cur = ws.current_lease(dbx)
    assert cur is not None and cur["holder_agent_id"] == b.id  # handed over, not "free"


def test_exec_on_closed_session_is_audited_failure(dbx):
    from app.workspace import service as ws
    a = mkagent(dbx, "甲")
    s = ws.create_session(dbx, a.id)
    sid = s["id"] if isinstance(s, dict) else s.id
    ws.close_session(dbx, sid)
    session_obj = dbx.query(models.WorkspaceSession).get(sid)
    r = ws.exec_in_session(dbx, session_obj, "echo hi")
    assert r["status"] == "failed" and "会话已关闭" in (r.get("stderr") or "")


def test_run_capped_timeout_kills_whole_group(tmp_path):
    from app.workspace.service import run_capped
    marker = tmp_path / "should_not_exist.txt"
    cmd = f"(sleep 2; touch {marker}) & sleep 30"
    _, _, _, timed_out = run_capped(["/bin/bash", "-lc", cmd], timeout=1)
    assert timed_out
    time.sleep(2.5)
    assert not marker.exists()  # background child was killed with the group


# ---------------------------------------------------------- tool registry

def test_confined_rejects_prefix_sibling_and_symlink(dbx, tmp_path, monkeypatch):
    from app.tools import registry
    root = tmp_path / "workspaces"; root.mkdir()
    sibling = tmp_path / "workspaces_backup"; sibling.mkdir()
    (sibling / "secret.txt").write_text("x")
    outside = tmp_path / "outside"; outside.mkdir()
    (outside / "secret.txt").write_text("x")
    os.symlink(outside, root / "link")
    monkeypatch.setattr(registry, "WORKSPACE_ROOT", str(root))
    with pytest.raises(registry.ToolError):
        registry._confined("../workspaces_backup/secret.txt")
    with pytest.raises(registry.ToolError):
        registry._confined("link/secret.txt")


def test_session_ownership_enforced(dbx, monkeypatch):
    from app.tools import registry
    from app.workspace import service as ws
    a = mkagent(dbx, "甲", tools=["linux_exec"]); b = mkagent(dbx, "乙")
    s = ws.create_session(dbx, a.id)
    sid = s["id"] if isinstance(s, dict) else s.id
    monkeypatch.setattr(registry, "_ws_db", lambda: dbx)
    with pytest.raises(registry.ToolError):
        registry._linux_exec({"command": "true", "session_id": sid,
                              "agent_id": b.id})


# ---------------------------------------------------------- MCP namespacing

def test_mcp_tool_ids_namespaced_by_server_id(dbx):
    from app.mcp import service as mcp_service
    from app.tools.registry import REGISTRY

    class FakeSession:
        server_capabilities = {"tools": {}}
        def list_tools(self):
            return [{"name": "echo", "description": "d", "inputSchema": {}}]

    ids = []
    try:
        for name in ("甲服务", "乙服务"):
            srv = models.MCPServer(name=name, transport="stdio",
                                   command_or_url="true")
            dbx.add(srv); dbx.commit(); dbx.refresh(srv)
            registered = mcp_service.register_server_tools(srv.id, srv.name, FakeSession())
            ids.append(registered[0])
        assert ids[0] != ids[1]
        assert all(i.startswith("mcp.") and i.endswith(".echo") for i in ids)
    finally:
        for i in ids:
            REGISTRY.pop(i, None)


# ---------------------------------------------------------- teams mentions

def test_mentions_longest_name_wins():
    from app.teams.chat import parse_mentions
    from types import SimpleNamespace
    members = {1: SimpleNamespace(id=1, name="林"),
               2: SimpleNamespace(id=2, name="林晓")}
    assert parse_mentions("@林晓 请看一下", members) == [2]
    assert parse_mentions("@林 请看一下", members) == [1]


# ---------------------------------------------------------- API route guards

def _mk_provider(db):
    p = models.ModelProvider(name="p", base_url="https://x", api_key="k",
                             format="chat_completions", models=[])
    db.add(p); db.commit(); db.refresh(p); return p


def test_provider_set_models_rejects_non_list(dbx):
    from app.api.routes import provider_set_models
    p = _mk_provider(dbx)
    with pytest.raises(HTTPException) as ei:
        provider_set_models(p.id, {"models": "gpt-x"}, dbx)
    assert ei.value.status_code == 422


def test_cancel_and_retry_state_guards(dbx):
    from app.api.routes import cancel_task, retry_task
    done = mktask(dbx, status="completed")
    with pytest.raises(HTTPException) as ei:
        cancel_task(done.id, dbx)
    assert ei.value.status_code == 409
    # 409 文案直接进前端提示：状态必须中文，不得原样输出 completed。
    assert "已完成" in str(ei.value.detail)
    assert "completed" not in str(ei.value.detail)
    running = mktask(dbx, status="running")
    with pytest.raises(HTTPException) as ei:
        retry_task(running.id, dbx)
    assert ei.value.status_code == 409


def test_get_or_404_huge_id_is_404(dbx):
    from app.api.routes import _get_or_404
    with pytest.raises(HTTPException) as ei:
        _get_or_404(dbx, models.Agent, 2 ** 70)
    assert ei.value.status_code == 404


# ---------------------------------------------------------- dependencies pinning

def test_dependency_job_never_falls_back_to_other_target(dbx):
    from app.workspace import dependencies as deps
    job = models.DependencyInstallJob(
        status="running", target_distro="debian", target_version="12",
        created_at=datetime.utcnow())
    dbx.add(job); dbx.commit(); dbx.refresh(job)
    deps.run_job(dbx, job)  # pinned sandbox was never installed here
    dbx.refresh(job)
    assert job.status == "failed"
    assert "未安装到其他环境" in (job.log or "")
