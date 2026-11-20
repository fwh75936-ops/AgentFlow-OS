"""回归：员工 JSON 字段为 NULL / 职责为空对象时，接口必须仍返回数组/完整结构。

背景（2026-11-19 实测白屏）：agents 表的 JSON 列可空——SQLAlchemy 的
default=list 只在「未传值」时生效，显式存入 None、旧库/恢复备份的行都
可能是 NULL；总管 ensure_team 建的员工 responsibilities 是 {}（三个
子列表全缺）。前端员工页直接读 capability_tags.length 与
responsibilities.responsible_for.length，null/缺字段会抛
TypeError 让整页白屏。后端契约：这些字段永远以数组/完整结构返回。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
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
    c.engine = engine
    return c


def _seed_null_agent(client):
    """Insert an agent, then force every nullable JSON column to NULL
    with raw SQL — the state a legacy/restored row can actually be in
    (ORM defaults cannot produce it, which is why it slipped through)."""
    db = client.session_factory()
    a = models.Agent(name="存量员工", role="general", status="active")
    db.add(a)
    db.commit()
    aid = a.id
    db.close()
    with client.engine.begin() as conn:
        conn.execute(text(
            "UPDATE agents SET capability_tags=NULL, capability_scores=NULL,"
            " tools=NULL, skills=NULL, responsibilities=NULL,"
            " memory_config=NULL, permissions=NULL WHERE id=:i"), {"i": aid})
    return aid


def test_list_agents_null_json_fields_serialize_as_empty(client):
    aid = _seed_null_agent(client)
    r = client.get("/api/agents")
    assert r.status_code == 200
    row = next(x for x in r.json() if x["id"] == aid)
    assert row["capability_tags"] == []
    assert row["capability_scores"] == {}
    assert row["tools"] == []
    assert row["skills"] == []
    assert row["responsibilities"] == {
        "responsible_for": [], "not_responsible_for": [], "deliverables": []}


def test_get_agent_null_json_fields_serialize_as_empty(client):
    # GET /agents/{id} goes through response_model=AgentOut instead of
    # _agent_out — both paths must honour the same guarantee.
    aid = _seed_null_agent(client)
    r = client.get(f"/api/agents/{aid}")
    assert r.status_code == 200
    row = r.json()
    assert row["capability_tags"] == []
    assert row["tools"] == []
    assert row["responsibilities"]["responsible_for"] == []


def test_empty_responsibilities_object_gets_full_shape(client):
    # ensure_team-created agents store responsibilities={} — the exact
    # row shape that white-screened the desktop Agents page.
    db = client.session_factory()
    a = models.Agent(name="产品经理", role="product", status="active",
                     capability_tags=["product"], responsibilities={})
    db.add(a)
    db.commit()
    aid = a.id
    db.close()
    r = client.get("/api/agents")
    assert r.status_code == 200
    row = next(x for x in r.json() if x["id"] == aid)
    assert row["responsibilities"] == {
        "responsible_for": [], "not_responsible_for": [], "deliverables": []}
    assert row["capability_tags"] == ["product"]


def test_create_agent_with_null_fields_is_coerced(client):
    r = client.post("/api/agents", json={
        "name": "新人", "capability_tags": None, "tools": None,
        "responsibilities": None})
    assert r.status_code == 201
    row = r.json()
    assert row["capability_tags"] == []
    assert row["tools"] == []
    assert row["responsibilities"]["deliverables"] == []
