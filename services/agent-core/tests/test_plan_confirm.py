"""Plan confirmation: the pre-execution plan preview runs the real
scheduler without persisting anything; a user pin is stored on the node
and honored by the runner (recorded in the SchedulerDecision audit row).
Agents have no model provider here, so execution itself fails honestly
with MODEL_ERROR — assignment happens before the model call, which is
exactly what these tests assert."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models
from app.api.routes import router
from app.database import Base, get_db


@pytest.fixture()
def client(tmp_path):
    engine = create_engine("sqlite:///" + str(tmp_path / "plan.db"),
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    def override_get_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    test_app = FastAPI()
    test_app.include_router(router)
    test_app.dependency_overrides[get_db] = override_get_db
    c = TestClient(test_app)
    c.session_factory = Session
    return c


def _seed_agents(client):
    db = client.session_factory()
    specs = [
        ("林晓", ["planning", "research"], {"planning": 0.95, "research": 0.9}),
        ("陈工", ["coding", "general"], {"coding": 0.95, "general": 0.8}),
        ("苏晴", ["review"], {"review": 0.92}),
    ]
    ids = {}
    for name, caps, scores in specs:
        a = models.Agent(name=name, role="test", tools=[], capability_tags=caps,
                         capability_scores=scores, status="active")
        db.add(a)
        db.commit()
        db.refresh(a)
        ids[name] = a.id
    db.close()
    return ids


def _make_task(client):
    r = client.post("/api/tasks", json={"title": "计划确认测试任务", "scheduler_mode": "adaptive"})
    assert r.status_code == 201
    return r.json()


def test_plan_preview_uses_real_scheduler_scores(client):
    ids = _seed_agents(client)
    task = _make_task(client)
    r = client.get(f"/api/tasks/{task['id']}/plan")
    assert r.status_code == 200
    plan = r.json()
    assert plan["task_id"] == task["id"] and len(plan["nodes"]) == 4
    by_name = {n["name"]: n for n in plan["nodes"]}
    # The scheduler really scored candidates: the coding step selects 陈工.
    execute = by_name["execute"]
    assert execute["selected_agent_id"] == ids["陈工"]
    cand = {c["agent_id"]: c for c in execute["candidates"]}
    assert cand[ids["陈工"]]["eligible"] is True
    assert cand[ids["陈工"]]["total"] is not None
    assert cand[ids["林晓"]]["eligible"] is False  # lacks coding capability
    # Preview persisted nothing: no decisions recorded yet.
    assert client.get("/api/scheduler/decisions", params={"task_id": task["id"]}).json() == []


def test_assign_pin_validation(client):
    ids = _seed_agents(client)
    task = _make_task(client)
    node = task["nodes"][0]
    r = client.put(f"/api/tasks/{task['id']}/nodes/{node['id']}/assign",
                   json={"agent_id": 9999})
    assert r.status_code == 404
    r = client.put(f"/api/tasks/{task['id']}/nodes/9999/assign",
                   json={"agent_id": ids["林晓"]})
    assert r.status_code == 404
    r = client.put(f"/api/tasks/{task['id']}/nodes/{node['id']}/assign",
                   json={"agent_id": ids["苏晴"]})
    assert r.status_code == 200
    plan = r.json()
    pinned = {n["node_id"]: n for n in plan["nodes"]}[node["id"]]
    assert pinned["pinned_agent_id"] == ids["苏晴"]
    assert pinned["selected_agent_id"] == ids["苏晴"]


def test_runner_honors_confirmed_pin(client, monkeypatch):
    # The runner opens its own sessions from app.database.SessionLocal;
    # point it at the test database so execution stays in the fixture.
    import app.database as database
    monkeypatch.setattr(database, "SessionLocal", client.session_factory)
    ids = _seed_agents(client)
    task = _make_task(client)
    # The plan node runs first (no dependencies); later nodes never get
    # scheduled in this fixture because plan fails honestly with
    # MODEL_ERROR, so the pin is asserted on the plan node itself.
    plan_node = next(n for n in task["nodes"] if n["name"] == "plan")
    # Scheduler would pick 林晓 for planning; the user pins 陈工 instead.
    r = client.put(f"/api/tasks/{task['id']}/nodes/{plan_node['id']}/assign",
                   json={"agent_id": ids["陈工"]})
    assert r.status_code == 200
    r = client.post(f"/api/tasks/{task['id']}/execute")
    assert r.status_code == 200
    decisions = client.get("/api/scheduler/decisions",
                           params={"task_id": task["id"]}).json()
    mine = [d for d in decisions if d["node_id"] == plan_node["id"]]
    assert mine, "执行后应有该节点的调度决策记录"
    assert mine[0]["selected_agent_id"] == ids["陈工"]
    assert "用户指派" in mine[0]["reason"]
    # After execution started, reassigning is refused.
    r = client.put(f"/api/tasks/{task['id']}/nodes/{plan_node['id']}/assign",
                   json={"agent_id": ids["林晓"]})
    assert r.status_code == 400


def test_stats_include_real_telemetry_fields(client):
    _seed_agents(client)
    r = client.get("/api/stats")
    assert r.status_code == 200
    s = r.json()
    assert s["executed_steps"] == 0  # nothing executed yet
    assert s["avg_tool_latency_ms"] is None  # no command logs: honest null
