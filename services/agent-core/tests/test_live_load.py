"""Regression: Agent.current_load had no writer anywhere in the codebase,
so it was permanently 0.0 — the scheduler's load component never had any
effect and the UI showed 0% load for agents actively running nodes.
Load is now derived at read/scheduling time from real TaskNode rows in
status='running' (load = n/(n+1)), and /api/stats success_rate is
measured over finished tasks only instead of all tasks ever created."""
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
from app.scheduler.engine import load_from_running, refresh_live_loads, select_agent


@pytest.fixture()
def client(tmp_path):
    engine = create_engine("sqlite:///" + str(tmp_path / "load.db"),
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


def _seed_agents(db):
    busy = models.Agent(name="忙碌者", role="developer", status="active",
                        capability_tags=["coding"],
                        capability_scores={"coding": 0.9},
                        historical_success_rate=0.9)
    idle = models.Agent(name="空闲者", role="developer", status="active",
                        capability_tags=["coding"],
                        capability_scores={"coding": 0.9},
                        historical_success_rate=0.9)
    db.add_all([busy, idle])
    db.commit()
    return busy, idle


def _running_node(db, agent):
    task = models.Task(title="进行中的任务", status="running")
    db.add(task)
    db.commit()
    node = models.TaskNode(task_id=task.id, name="执行", node_type="llm",
                           status="running", assigned_agent_id=agent.id)
    db.add(node)
    db.commit()
    return node


def test_load_formula():
    assert load_from_running(0) == 0.0
    assert load_from_running(-1) == 0.0
    assert load_from_running(1) == 0.5
    assert load_from_running(3) == 0.75


def test_agent_list_and_detail_report_live_load(client):
    db = client.session_factory()
    busy, idle = _seed_agents(db)
    _running_node(db, busy)
    busy_id, idle_id = busy.id, idle.id
    db.close()

    agents = {a["id"]: a for a in client.get("/api/agents").json()}
    assert agents[busy_id]["current_load"] == 0.5
    assert agents[idle_id]["current_load"] == 0.0

    detail = client.get(f"/api/agents/{busy_id}/detail").json()
    assert detail["detail"]["performance"]["current_load"] == 0.5
    assert len(detail["detail"]["current_tasks"]) == 1


def test_scheduler_avoids_busy_agent_after_refresh(client):
    db = client.session_factory()
    busy, idle = _seed_agents(db)
    _running_node(db, busy)
    agents = db.query(models.Agent).all()
    refresh_live_loads(db, agents)
    by_name = {a.name: a for a in agents}
    assert by_name["忙碌者"].current_load == 0.5
    assert by_name["空闲者"].current_load == 0.0
    decision = select_agent(agents, ["coding"], [], priority=5, mode="adaptive")
    assert decision.selected.name == "空闲者"
    db.close()


def test_stats_success_rate_counts_finished_only(client):
    db = client.session_factory()
    db.add_all([
        models.Task(title="成了", status="completed"),
        models.Task(title="败了", status="failed"),
        models.Task(title="还没跑", status="pending"),
        models.Task(title="在跑", status="running"),
    ])
    db.commit()
    db.close()
    s = client.get("/api/stats").json()
    assert s["success_rate"] == 0.5
    assert s["task_count"] == 4


def test_stats_success_rate_none_without_finished(client):
    db = client.session_factory()
    db.add(models.Task(title="还没跑", status="pending"))
    db.commit()
    db.close()
    s = client.get("/api/stats").json()
    assert s["success_rate"] is None
