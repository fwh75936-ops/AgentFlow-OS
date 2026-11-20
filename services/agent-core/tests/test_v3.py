"""Round-3 V3.0 tests: resource leases, SACAS, state-aware verification,
chief (incl. permission separation + handoff), computer-use gate, proactive.
All against real temp SQLite + real shared dir + real pytest subprocess."""
import os
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models
from app.database import Base
from app.resources import service as res
from app.scheduler.engine import select_agent
from app.tools.registry import execute_tool, ToolError
from app.verification import state as vs
from app.workspace import service as ws


@pytest.fixture()
def dbx(tmp_path, monkeypatch):
    root = tmp_path / "shared"; root.mkdir()
    monkeypatch.setenv("AGENTFLOW_SHARED_ROOT", str(root))
    engine = create_engine("sqlite:///" + str(tmp_path / "v3.db"),
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    db = sessionmaker(bind=engine, autocommit=False, autoflush=False)()
    yield db
    db.close()


def mkagent(db, name, tools=None, caps=None, scores=None, **kw):
    a = models.Agent(name=name, role="t", tools=tools or [], capability_tags=caps or [],
                     capability_scores=scores or {}, status="active", **kw)
    db.add(a); db.commit(); db.refresh(a); return a


def sagent(**kw):
    base = dict(id=1, status="active", availability=True, tools=["linux_file_write"],
                capability_tags=["coding"], capability_scores={"coding": 0.9},
                historical_success_rate=0.8, average_cost=1.0, average_latency=2.0,
                current_load=0.1, execution_count=0, success_count=0)
    base.update(kw); return SimpleNamespace(**base)


# ---- 1. resource leases ----
def test_resource_lease_conflict_expiry_release(dbx):
    a = mkagent(dbx, "A"); b = mkagent(dbx, "B")
    lease, _ = res.acquire(dbx, "git", "repo:main", a.id, ttl_seconds=300)
    assert lease.status == "held"
    with pytest.raises(res.ResourceConflict):
        res.acquire(dbx, "git", "repo:main", b.id)
    assert res.conflict_risk(dbx, b.id, "git", "repo:main") == 1.0
    assert res.conflict_risk(dbx, a.id, "git", "repo:main") == 0.0
    # different key: no interference; desktop lease table untouched
    res.acquire(dbx, "port", "8000", b.id)
    assert dbx.query(models.DesktopLease).count() == 0
    res.release(dbx, "git", "repo:main", a.id)
    lease2, _ = res.acquire(dbx, "git", "repo:main", b.id)
    assert lease2.holder_agent_id == b.id
    with pytest.raises(ValueError):
        res.acquire(dbx, "nope", "x", a.id)


def test_resource_lease_expires(dbx):
    a = mkagent(dbx, "A"); b = mkagent(dbx, "B")
    lease_a, _ = res.acquire(dbx, "browser", "session-1", a.id, ttl_seconds=60)
    # Backdate the lease so it is already expired (ttl_seconds <= 0 is
    # rejected by validation now, so expiry is simulated on the record).
    from datetime import datetime, timedelta
    lease_a.expires_at = datetime.utcnow() - timedelta(seconds=1)
    dbx.commit()
    lease, _ = res.acquire(dbx, "browser", "session-1", b.id)
    assert lease.holder_agent_id == b.id


# ---- 2. SACAS ----
def test_sacas_conflict_penalty_flips_choice():
    strong_conflicted = sagent(id=1, capability_scores={"coding": 0.95}, historical_success_rate=0.95)
    clean = sagent(id=2, capability_scores={"coding": 0.90}, historical_success_rate=0.90)
    d_adaptive = select_agent([strong_conflicted, clean], ["coding"], [], mode="adaptive")
    assert d_adaptive.selected.id == 1  # adaptive ignores resources
    d = select_agent([strong_conflicted, clean], ["coding"], [], mode="sacas",
                     resource_state={1: 1.0, 2: 0.0})
    assert d.selected.id == 2
    comp = {c["agent_id"]: c["components"] for c in d.candidates}
    assert comp[1]["resource_conflict_risk"] == 1.0 and comp[2]["resource_conflict_risk"] == 0.0
    assert "env_readiness" in comp[1] and "dependency_readiness" in comp[1]


def test_sacas_no_conflict_parallel_unaffected():
    a = sagent(id=1, capability_scores={"coding": 0.95}, historical_success_rate=0.95)
    b = sagent(id=2, capability_scores={"coding": 0.90}, historical_success_rate=0.90)
    d = select_agent([a, b], ["coding"], [], mode="sacas", resource_state={1: 0.0, 2: 0.0})
    assert d.selected.id == 1  # best agent still wins when nobody conflicts


def test_sacas_dependency_not_ready_selects_nobody():
    d = select_agent([sagent(id=1), sagent(id=2)], ["coding"], [], mode="sacas",
                     dependencies_ready=False)
    assert d.selected is None and "dependencies" in d.reason


def test_sacas_held_by_resource_state_shape():
    a = sagent(id=1, capability_scores={"coding": 0.95}, historical_success_rate=0.95)
    b = sagent(id=2, capability_scores={"coding": 0.90}, historical_success_rate=0.90)
    d = select_agent([a, b], ["coding"], [], mode="sacas",
                     resource_state={"held_by": {"file:x.py": 1}, "resource_key": "file:x.py"})
    # holder (agent 1) has risk 0; the other candidate has risk 1 and is penalised
    comp = {c["agent_id"]: c["components"] for c in d.candidates}
    assert comp[1]["resource_conflict_risk"] == 0.0 and comp[2]["resource_conflict_risk"] == 1.0
    assert d.selected.id == 1


# ---- 3. state-aware verification (real file writes) ----
def test_verification_goes_stale_on_v2_and_reverify(dbx):
    dev = mkagent(dbx, "dev")
    ws.write_file(dbx, dev.id, "proj/a.py", "x = 1\n")
    rec = vs.record_verification(dbx, "proj/a.py", True, test_run_id="run-1")
    assert rec.artifact_version == 1 and vs.is_verified(dbx, "proj/a.py")
    ws.write_file(dbx, dev.id, "proj/a.py", "x = 2\n")  # v2
    assert vs.check_freshness(dbx) >= 1
    dbx.refresh(rec); assert rec.status == "stale"
    assert not vs.is_verified(dbx, "proj/a.py")  # v2 must NOT be reported verified
    assert vs.current_verification(dbx, "proj/a.py") is None
    rec2 = vs.record_verification(dbx, "proj/a.py", True, test_run_id="run-2")
    assert rec2.artifact_version == 2 and rec2.status == "current"
    assert vs.is_verified(dbx, "proj/a.py")


# ---- 4. chief ----
def test_chief_mission_end_to_end(dbx):
    from app.chief import service as chief
    m = chief.create_mission(dbx, "开发一个用户管理系统，完成后测试验证并汇报")
    assert m.status == "completed" and m.llm_called is False
    assert m.report["artifacts"]["projects/user-mgmt/user_api.py"]["verified_current"] is True
    assert all(n["status"] == "completed" for n in m.report["nodes"])
    assert dbx.query(models.SchedulerDecision).filter_by(mode="sacas").count() >= 1


def test_chief_has_no_linux_exec_permission(dbx):
    from app.chief import service as chief
    chief_agent, _team, _members = chief.ensure_team(dbx)
    assert "linux_exec" not in (chief_agent.tools or [])
    with pytest.raises(ToolError) as ei:
        execute_tool("linux_exec", {"agent_id": chief_agent.id, "command": "echo hi"},
                     chief_agent.tools or [], approved=True)
    assert ei.value.error_type == "PERMISSION_ERROR"


def test_handoff_version_check(dbx):
    from app.chief import service as chief
    dev = mkagent(dbx, "dev"); tester = mkagent(dbx, "tester")
    ws.write_file(dbx, dev.id, "proj/h.py", "v1\n")
    msg = chief.create_handoff(dbx, None, dev.id, tester.id, "继续测试",
                               ["写了 h.py"], ["proj/h.py"], [], ["pytest 通过"])
    assert msg.meta["environmentVersion"]["proj/h.py"]["version"] == 1
    assert chief.accept_handoff(dbx, msg.message_id)["accepted"] is True
    ws.write_file(dbx, dev.id, "proj/h.py", "v2\n")
    with pytest.raises(ValueError, match="版本已变化"):
        chief.accept_handoff(dbx, msg.message_id)


# ---- 5. computer use gate ----
def test_computer_use_requires_lease_and_takeover_blocks(dbx):
    from app.computer_use import service as cu
    a = mkagent(dbx, "A")
    r = cu.act(dbx, a.id, "click")
    assert r["status"] == "rejected" and r["executed"] is False  # no lease
    ws.acquire_desktop_lease(dbx, a.id, ttl_seconds=300)
    r2 = cu.act(dbx, a.id, "click")
    # lease held: gate passes; xdotool absent on this machine -> honest unavailable
    assert r2["status"] in ("unavailable", "executed")
    if r2["status"] == "unavailable":
        assert r2["executed"] is False
    ws.force_takeover_desktop(dbx)
    r3 = cu.act(dbx, a.id, "click")
    assert r3["status"] == "rejected" and "用户接管" in r3["reason"]
    obs = cu.observe(dbx)
    assert "tools" in obs and "lease" in obs


# ---- 6. proactive ----
def test_proactive_test_failed_flow(dbx):
    from app.proactive import service as pro
    ws.emit_event(dbx, "test_failed", detail="pytest: 1 failed (test_admin_stats)")
    created = pro.scan_events(dbx)
    l2 = [s for s in created if s.event_type == "test_failed" and s.level == "L2"]
    assert l2 and l2[0].status == "pending"
    with pytest.raises(PermissionError):
        pro.execute_suggestion(dbx, l2[0].id)  # not approved -> no execution
    pro.approve(dbx, l2[0].id)
    out = pro.execute_suggestion(dbx, l2[0].id)
    assert out["status"] == "executed"
    s2 = pro.scan_events(dbx)  # dedupe: same event must not re-suggest
    assert all(s.source_event_id != l2[0].source_event_id for s in s2)
