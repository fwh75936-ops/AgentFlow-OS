"""Regression tests for the 2026-11-19 backend bug sweep (round 2):
error paths that answered malformed input with a bare 500 or a fake
success, and one read path (GET /agents/{id}) that returned stored
stale values where list/detail return values derived from real records.
Every test below failed (500 / wrong status / wrong payload) before its
fix and passes after."""
import os

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models
from app.api.routes import router as api_router
from app.chief.routes import router as chief_router
from app.database import Base, get_db
from app.proactive.routes import router as proactive_router
from app.resources.routes import router as resources_router
from app.verification.routes import router as verification_router
from app.workflows.routes import router as workflows_router
from app.workspace.routes import router as workspace_router


@pytest.fixture()
def client(tmp_path, monkeypatch):
    root = tmp_path / "shared"
    root.mkdir()
    monkeypatch.setenv("AGENTFLOW_SHARED_ROOT", str(root))
    engine = create_engine("sqlite:///" + str(tmp_path / "sweep.db"),
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    # The runner executes nodes in worker threads through
    # app.database.SessionLocal (not the request session): without this
    # the workflow tests below would run against the real dev database.
    import app.database as database
    monkeypatch.setattr(database, "SessionLocal", Session)

    def override_get_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    test_app = FastAPI()
    for r in (api_router, chief_router, workflows_router, verification_router,
              resources_router, proactive_router, workspace_router):
        test_app.include_router(r)
    test_app.dependency_overrides[get_db] = override_get_db
    c = TestClient(test_app, raise_server_exceptions=False)
    c.session_factory = Session
    c.shared_root = str(root)
    return c


def _agent(client, name="员工甲", **kw):
    body = {"name": name}
    body.update(kw)
    r = client.post("/api/agents", json=body)
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _task(client, title="任务甲"):
    r = client.post("/api/tasks", json={"title": title})
    assert r.status_code == 201, r.text
    return r.json()["id"]


# ---- Fix 1: GET /agents/{id} must derive load/success-rate like list/detail
def test_get_agent_returns_derived_load_and_success_rate(client):
    aid = _agent(client, "新人")
    row = client.get(f"/api/agents/{aid}").json()
    # No executions yet: the stored 0.5 is a scheduler prior, not a
    # measured rate — the single GET must say so (None), like the list.
    assert row["historical_success_rate"] is None
    assert row["current_load"] == 0.0
    db = client.session_factory()
    t = models.Task(title="跑着的任务", status="running")
    db.add(t)
    db.commit()
    db.add(models.TaskNode(task_id=t.id, name="执行", node_type="agent",
                           assigned_agent_id=aid, status="running"))
    # The runner sets both together: a running node and runtime_status
    # "Running" on the agent. A running node alone (without the status)
    # cannot occur through real execution.
    db.query(models.Agent).filter_by(id=aid).update(
        {"runtime_status": "Running"})
    db.commit()
    db.close()
    row = client.get(f"/api/agents/{aid}").json()
    assert row["current_load"] == 0.5
    assert row["activity_status"] == "model_calling"
    listed = next(x for x in client.get("/api/agents").json() if x["id"] == aid)
    assert listed["current_load"] == row["current_load"]
    assert listed["historical_success_rate"] == row["historical_success_rate"]


# ---- Fix 2: team membership is a set; the leader is a member ------------
def test_create_team_dedupes_members_and_includes_leader(client):
    a1, a2 = _agent(client, "甲"), _agent(client, "乙")
    r = client.post("/api/teams", json={
        "name": "去重队", "leader_agent_id": a1,
        "member_agent_ids": [a1, a1, a2]})
    assert r.status_code == 201, r.text
    members = r.json()["members"]
    assert sorted(m["agent_id"] for m in members) == sorted([a1, a2])
    leader = next(m for m in members if m["agent_id"] == a1)
    assert leader["is_leader"] is True
    # Leader named but not listed as a member is still a member row:
    # the member count and the chat member list must agree.
    r = client.post("/api/teams", json={
        "name": "负责人队", "leader_agent_id": a1,
        "member_agent_ids": [a2]})
    assert r.status_code == 201, r.text
    members = r.json()["members"]
    assert sorted(m["agent_id"] for m in members) == sorted([a1, a2])
    assert next(m for m in members if m["agent_id"] == a1)["is_leader"] is True


def test_add_member_twice_is_409(client):
    a1, a2 = _agent(client, "甲"), _agent(client, "乙")
    tid = client.post("/api/teams", json={
        "name": "重复队", "leader_agent_id": a1,
        "member_agent_ids": [a1]}).json()["id"]
    r = client.post(f"/api/teams/{tid}/members", json={"agent_id": a2})
    assert r.status_code == 200, r.text
    assert sorted(m["agent_id"] for m in r.json()["members"]) == sorted([a1, a2])
    r = client.post(f"/api/teams/{tid}/members", json={"agent_id": a2})
    assert r.status_code == 409, r.text
    assert len(client.get("/api/teams").json()[0]["members"]) == 2


# ---- Fix 3: chief handoffs validate types + existence --------------------
def _handoff_setup(client):
    a1, a2 = _agent(client, "甲"), _agent(client, "乙")
    tid = _task(client)
    return a1, a2, tid


def test_handoff_rejects_null_or_string_list_fields(client):
    a1, a2, tid = _handoff_setup(client)
    base = {"from_agent_id": a1, "to_agent_id": a2, "task_id": tid,
            "objective": "交接", "completedWork": ["做了A"],
            "artifactPaths": []}
    # Explicit null means "not provided" (same convention as
    # provider_set_models / approvals): it must not crash the post.
    r = client.post("/api/chief/handoffs", json={**base, "completedWork": None})
    assert r.status_code == 201, r.text
    assert r.json()["meta"]["completedWork"] == []
    r = client.post("/api/chief/handoffs", json={**base, "completedWork": "做了A"})
    assert r.status_code == 422, r.text
    r = client.post("/api/chief/handoffs", json={**base, "artifactPaths": "x.py"})
    assert r.status_code == 422, r.text


def test_handoff_rejects_ghost_agents_task_and_bad_types(client):
    a1, a2, tid = _handoff_setup(client)
    base = {"from_agent_id": a1, "to_agent_id": a2, "task_id": tid,
            "objective": "交接", "completedWork": [], "artifactPaths": []}
    r = client.post("/api/chief/handoffs",
                    json={**base, "from_agent_id": 999999})
    assert r.status_code == 404, r.text
    r = client.post("/api/chief/handoffs",
                    json={**base, "to_agent_id": "x"})
    assert r.status_code == 422, r.text
    r = client.post("/api/chief/handoffs", json={**base, "task_id": 999999})
    assert r.status_code == 404, r.text
    r = client.post("/api/chief/handoffs", json={**base, "task_id": "x"})
    assert r.status_code == 422, r.text
    r = client.post("/api/chief/handoffs", json={**base, "objective": 123})
    assert r.status_code == 422, r.text


def test_handoff_happy_path_still_works(client):
    a1, a2, tid = _handoff_setup(client)
    r = client.post("/api/chief/handoffs", json={
        "from_agent_id": a1, "to_agent_id": a2, "task_id": tid,
        "objective": "交接", "completedWork": ["做了A"],
        "artifactPaths": ["hello.txt"]})
    assert r.status_code == 201, r.text
    assert r.json()["meta"]["completedWork"] == ["做了A"]
    assert r.json()["meta"]["artifactPaths"] == ["hello.txt"]


# ---- Fix 4: malformed workflow graphs / approvals are 400, not 500 -------
def _save_workflow(client, graph):
    r = client.post("/api/workflows", json={"name": "w", "graph": graph})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_workflow_execute_rejects_malformed_graph(client):
    wid = _save_workflow(client, {"nodes": "abc", "edges": []})
    assert client.post(f"/api/workflows/{wid}/execute", json={}).status_code == 400
    wid = _save_workflow(client, {"nodes": [123], "edges": []})
    assert client.post(f"/api/workflows/{wid}/execute", json={}).status_code == 400
    wid = _save_workflow(client, {
        "nodes": [{"id": "n1", "kind": "agent", "label": "执行"}],
        "edges": "x"})
    assert client.post(f"/api/workflows/{wid}/execute", json={}).status_code == 400
    wid = _save_workflow(client, {
        "nodes": [{"id": "n1", "kind": "agent", "label": "执行"}],
        "edges": [{"source": "n1", "target": "n2"}]})
    assert client.post(f"/api/workflows/{wid}/execute", json={}).status_code == 400


def test_workflow_execute_rejects_non_dict_approval_and_bad_title(client):
    _agent(client, "审批员")
    wid = _save_workflow(client, {
        "nodes": [{"id": "a1", "kind": "humanapproval", "label": "审批"}],
        "edges": []})
    r = client.post(f"/api/workflows/{wid}/execute",
                    json={"approvals": {"a1": "yes"}})
    assert r.status_code == 400, r.text
    r = client.post(f"/api/workflows/{wid}/execute", json={"title": 123})
    assert r.status_code == 422, r.text
    # A well-formed approvals dict still runs the approval node.
    r = client.post(f"/api/workflows/{wid}/execute",
                    json={"approvals": {"a1": {"approved": True}}})
    assert r.status_code == 201, r.text
    assert r.json()["status"] == "completed"


# ---- Fix 5: reviewer + verification record input validation --------------
def _review_setup(client):
    writer = _agent(client, "写手", tools=["linux_file_write"])
    reviewer = _agent(client, "评审", capability_tags=["review"])
    r = client.post("/api/workspace/files/write", json={
        "agent_id": writer, "path": "hello.txt", "content": "hello world"})
    assert r.status_code == 200, r.text
    return reviewer


def test_review_rejects_malformed_checks_and_author(client):
    reviewer = _review_setup(client)
    base = {"artifact_path": "hello.txt", "reviewer_agent_id": reviewer}
    r = client.post("/api/artifact-verifications/reviews",
                    json={**base, "checks": "abc"})
    assert r.status_code == 400, r.text
    r = client.post("/api/artifact-verifications/reviews",
                    json={**base, "checks": [123]})
    assert r.status_code == 400, r.text
    r = client.post("/api/artifact-verifications/reviews",
                    json={**base, "author_agent_id": "abc"})
    assert r.status_code == 400, r.text
    r = client.post("/api/artifact-verifications/reviews",
                    json={**base, "task_id": "x"})
    assert r.status_code == 400, r.text


def test_review_happy_path_still_works(client):
    reviewer = _review_setup(client)
    r = client.post("/api/artifact-verifications/reviews", json={
        "artifact_path": "hello.txt", "reviewer_agent_id": reviewer,
        "checks": [{"type": "contains", "value": "hello"}]})
    assert r.status_code == 201, r.text
    assert r.json()["passed"] is True


def test_verification_record_rejects_bad_field_types(client):
    _review_setup(client)
    base = {"artifact_path": "hello.txt", "passed": True}
    r = client.post("/api/artifact-verifications",
                    json={**base, "evidence": {"a": 1}})
    assert r.status_code == 422, r.text
    r = client.post("/api/artifact-verifications",
                    json={**base, "task_id": "x"})
    assert r.status_code == 422, r.text
    r = client.post("/api/artifact-verifications",
                    json={**base, "artifact_path": 123})
    assert r.status_code == 422, r.text
    r = client.post("/api/artifact-verifications",
                    json={**base, "evidence": "pytest 通过"})
    assert r.status_code == 201, r.text
    assert r.json()["passed"] is True


# ---- Fix 6: resource leases need a real agent holder ----------------------
def test_resource_lease_acquire_rejects_ghost_agent(client):
    r = client.post("/api/resources/leases/acquire", json={
        "resource_type": "desktop", "resource_key": "desk-1",
        "agent_id": 999999})
    assert r.status_code == 404, r.text
    aid = _agent(client, "持有人")
    r = client.post("/api/resources/leases/acquire", json={
        "resource_type": "desktop", "resource_key": "desk-1",
        "agent_id": aid})
    assert r.status_code == 201, r.text
    assert r.json()["holder_agent_id"] == aid


# ---- Fix 7: missing proactive suggestion is 404, not 400 -----------------
def test_proactive_missing_suggestion_is_404(client):
    for action in ("approve", "reject", "execute"):
        r = client.post(f"/api/proactive/suggestions/999999/{action}")
        assert r.status_code == 404, r.text
    db = client.session_factory()
    s = models.ProactiveSuggestion(rule_id=None, source_event_id=None,
                                   event_type="test_failed", level="L2",
                                   title="测试失败", detail="", action="",
                                   status="pending")
    db.add(s)
    db.commit()
    sid = s.id
    db.close()
    r = client.post(f"/api/proactive/suggestions/{sid}/approve")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "approved"
    r = client.post(f"/api/proactive/suggestions/{sid}/approve")
    assert r.status_code == 400, r.text


# ---- Fix 8: string "false" for a boolean column is 422, not 500 ----------
def test_update_mcp_rejects_string_bool(client):
    r = client.post("/api/mcp-servers", json={
        "name": "s", "transport": "stdio", "command_or_url": ""})
    assert r.status_code == 201, r.text
    sid = r.json()["id"]
    r = client.put(f"/api/mcp-servers/{sid}", json={"enabled": "false"})
    assert r.status_code == 422, r.text
    r = client.put(f"/api/mcp-servers/{sid}", json={"enabled": False})
    assert r.status_code == 200, r.text
    assert r.json()["enabled"] is False


def test_proactive_rule_rejects_string_bool_and_bad_level(client):
    r = client.post("/api/proactive/rules", json={
        "event_type": "test_failed", "enabled": "false"})
    assert r.status_code == 422, r.text
    r = client.post("/api/proactive/rules", json={
        "event_type": "test_failed", "level": "L9"})
    assert r.status_code == 422, r.text
    r = client.post("/api/proactive/rules", json={
        "event_type": "test_failed", "level": "L2", "enabled": True})
    assert r.status_code == 201, r.text
    rid = r.json()["id"]
    r = client.patch(f"/api/proactive/rules/{rid}", json={"enabled": "false"})
    assert r.status_code == 422, r.text
    r = client.patch(f"/api/proactive/rules/{rid}", json={"level": "L9"})
    assert r.status_code == 422, r.text
    r = client.patch(f"/api/proactive/rules/{rid}", json={"level": "L1"})
    assert r.status_code == 200, r.text


# ---- Fix 9: staged review must not read a huge original unbounded --------
def test_stage_change_rejects_oversized_original(client):
    from app.workspace import review
    db = client.session_factory()
    a = models.Agent(name="写手", status="active")
    db.add(a)
    db.commit()
    aid = a.id
    with open(os.path.join(client.shared_root, "big.bin"), "wb") as f:
        f.write(b"x" * (10 * 1024 * 1024 + 1))
    with pytest.raises(ValueError):
        review.stage_change(db, aid, "big.bin", "new content")
    with open(os.path.join(client.shared_root, "small.txt"), "w") as f:
        f.write("hi")
    row = review.stage_change(db, aid, "small.txt", "hello")
    assert row["status"] == "pending"
    db.close()
