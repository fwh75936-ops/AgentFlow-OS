"""AI 员工模块完善：结构化岗位职责的存取、员工详情聚合接口
（当前任务/历史/绩效全部来自真实记录）、计划确认的职责匹配点。
聚合口径：Token 来自执行器写入的 node_token_usage 快照，耗时来自
任务节点的起止时间，验证结果来自该任务最近一条验证记录。"""
import os
import sys
from datetime import datetime, timedelta

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
    engine = create_engine("sqlite:///" + str(tmp_path / "agents.db"),
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


DUTIES = {
    "responsible_for": ["后端功能开发", "接口实现"],
    "not_responsible_for": ["需求定义"],
    "deliverables": ["可运行代码", "接口文档"],
}


def _seed(client):
    db = client.session_factory()
    dev = models.Agent(
        name="陈工", role="developer", tools=["file_write", "run_command"],
        capability_tags=["coding"], capability_scores={"coding": 0.95},
        responsibilities=DUTIES, avatar="avatar-05", status="active",
        execution_count=3, success_count=2)
    idle = models.Agent(name="新人", role="general", status="active")
    db.add_all([dev, idle])
    db.commit()
    db.refresh(dev)
    db.refresh(idle)
    team = models.Team(name="交付团队", leader_agent_id=dev.id)
    db.add(team)
    db.commit()
    db.refresh(team)
    db.add(models.TeamMember(team_id=team.id, agent_id=dev.id,
                             role_in_team="member", is_leader=True))
    skill = models.Skill(name="接口开发", instructions="...")
    db.add(skill)
    db.commit()
    db.refresh(skill)
    dev.skills = [skill.id]
    db.commit()

    base = datetime(2026, 11, 17, 10, 0, 0)
    t1 = models.Task(title="用户接口开发", status="completed")
    t2 = models.Task(title="正在进行的任务", status="running")
    db.add_all([t1, t2])
    db.commit()
    db.refresh(t1)
    db.refresh(t2)
    n1 = models.TaskNode(task_id=t1.id, name="开发登录接口", node_type="agent",
                         assigned_agent_id=dev.id, status="completed",
                         started_at=base, finished_at=base + timedelta(seconds=90))
    n2 = models.TaskNode(task_id=t1.id, name="补测试", node_type="agent",
                         assigned_agent_id=dev.id, status="failed",
                         started_at=base, finished_at=base + timedelta(seconds=30))
    n3 = models.TaskNode(task_id=t2.id, name="开发分页接口", node_type="agent",
                         assigned_agent_id=dev.id, status="running",
                         started_at=base + timedelta(hours=1))
    db.add_all([n1, n2, n3])
    db.add(models.VerificationRecord(
        task_id=t1.id, verification_type="structural", passed=True, score=0.9))
    db.add(models.ExecutionSnapshot(task_id=t1.id, state={
        "node_token_usage": {"node": "开发登录接口", "agent_id": dev.id,
                             "total_tokens": 1200}}))
    db.add(models.ExecutionSnapshot(task_id=t1.id, state={
        "node_token_usage": {"node": "补测试", "agent_id": dev.id,
                             "total_tokens": 300}}))
    db.add(models.ExecutionSnapshot(task_id=t1.id, state={
        "node_token_usage": {"node": "别人的节点", "agent_id": 999,
                             "total_tokens": 7777}}))
    db.commit()
    ids = {"dev": dev.id, "idle": idle.id, "t1": t1.id, "t2": t2.id}
    db.close()
    return ids


def test_responsibilities_roundtrip(client):
    r = client.post("/api/agents", json={
        "name": "苏晴", "role": "tester", "responsibilities": DUTIES,
        "avatar": "avatar-02"})
    assert r.status_code == 201
    aid = r.json()["id"]
    got = client.get(f"/api/agents/{aid}").json()
    assert got["responsibilities"] == DUTIES
    assert got["avatar"] == "avatar-02"
    r = client.put(f"/api/agents/{aid}", json={
        "name": "苏晴", "role": "tester",
        "responsibilities": {"responsible_for": ["测试执行"],
                             "not_responsible_for": [],
                             "deliverables": ["测试结果"]}})
    assert r.status_code == 200
    assert r.json()["responsibilities"]["deliverables"] == ["测试结果"]


def test_detail_aggregates_real_records(client):
    ids = _seed(client)
    d = client.get(f"/api/agents/{ids['dev']}/detail").json()
    detail = d["detail"]
    assert d["responsibilities"] == DUTIES
    assert [t["name"] for t in detail["teams"]] == ["交付团队"]
    assert detail["teams"][0]["is_leader"] is True
    assert [c["task_id"] for c in detail["current_tasks"]] == [ids["t2"]]
    history = {h["task_id"]: h for h in detail["history"]}
    h1 = history[ids["t1"]]
    assert h1["task_status"] == "completed"
    assert h1["duration_seconds"] == 120
    assert h1["verification"]["passed"] is True
    assert h1["verification"]["score"] == 0.9
    perf = detail["performance"]
    assert perf["completed_nodes"] == 1
    assert perf["failed_nodes"] == 1
    assert perf["node_success_rate"] == 0.5
    assert perf["average_duration_seconds"] == 60
    assert perf["total_tokens"] == 1500
    assert detail["skills_resolved"] == [{"id": 1, "name": "接口开发"}]


def test_detail_empty_states_are_honest(client):
    ids = _seed(client)
    detail = client.get(f"/api/agents/{ids['idle']}/detail").json()["detail"]
    assert detail["teams"] == []
    assert detail["current_tasks"] == []
    assert detail["history"] == []
    perf = detail["performance"]
    assert perf["completed_nodes"] == 0
    assert perf["node_success_rate"] is None
    assert perf["average_duration_seconds"] is None
    assert perf["total_tokens"] is None


def test_detail_404(client):
    assert client.get("/api/agents/424242/detail").status_code == 404


def test_plan_includes_duty_match(client):
    ids = _seed(client)
    r = client.post("/api/tasks", json={"title": "开发任务", "scheduler_mode": "fixed"})
    tid = r.json()["id"]
    db = client.session_factory()
    node = (db.query(models.TaskNode).filter_by(task_id=tid)
            .order_by(models.TaskNode.id).first())
    node.required_capabilities = ["coding"]
    node.required_tools = ["file_write"]
    node.name = "开发用户接口"
    nid = node.id
    db.commit()
    db.close()
    # Pin the developer so the duty match is deterministic.
    plan = client.put(f"/api/tasks/{tid}/nodes/{nid}/assign",
                      json={"agent_id": ids["dev"]}).json()
    node_plan = next(n for n in plan["nodes"] if n["node_id"] == nid)
    assert node_plan["selected_agent_id"] == ids["dev"]
    duty = node_plan["duty_match"]
    assert duty["capabilities"] == ["coding"]
    assert duty["tools"] == ["file_write"]
    assert duty["deliverables"] == ["可运行代码", "接口文档"]
