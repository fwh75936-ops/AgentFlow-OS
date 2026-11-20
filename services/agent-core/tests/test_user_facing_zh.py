"""Regression tests for the 2026-11-19 round-9 backend sweep: error
messages that reach the user must be Chinese, not bare English codes.
Every test below failed (English detail in the response) before its
fix and passes after."""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models
from app.chief.routes import router as chief_router
from app.database import Base, get_db
from app.proactive.routes import router as proactive_router


@pytest.fixture()
def client(tmp_path, monkeypatch):
    engine = create_engine("sqlite:///" + str(tmp_path / "zh.db"),
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    import app.database as database
    monkeypatch.setattr(database, "SessionLocal", Session)

    def override_get_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    test_app = FastAPI()
    for r in (chief_router, proactive_router):
        test_app.include_router(r)
    test_app.dependency_overrides[get_db] = override_get_db
    c = TestClient(test_app, raise_server_exceptions=False)
    c.session_factory = Session
    return c


def _pending_suggestion(client):
    db = client.session_factory()
    s = models.ProactiveSuggestion(rule_id=None, source_event_id=None,
                                   event_type="test_failed", level="L2",
                                   title="测试失败", detail="", action="",
                                   status="pending")
    db.add(s)
    db.commit()
    sid = s.id
    db.close()
    return sid


def test_chief_empty_goal_error_is_chinese(client):
    r = client.post("/api/chief/missions", json={"goal": "   "})
    assert r.status_code == 400, r.text
    assert "不能为空" in r.json()["detail"]
    assert "goal required" not in r.text


def test_chief_missing_mission_report_is_chinese(client):
    r = client.get("/api/chief/missions/999999/report")
    assert r.status_code == 404, r.text
    assert "总管任务不存在" in r.json()["detail"]
    assert "mission not found" not in r.text


def test_proactive_missing_rule_error_is_chinese(client):
    r = client.patch("/api/proactive/rules/999999", json={"enabled": False})
    assert r.status_code == 404, r.text
    assert "规则不存在" in r.json()["detail"]
    r = client.delete("/api/proactive/rules/999999")
    assert r.status_code == 404, r.text
    assert "rule not found" not in r.text


def test_proactive_double_approve_error_is_chinese(client):
    sid = _pending_suggestion(client)
    r = client.post(f"/api/proactive/suggestions/{sid}/approve")
    assert r.status_code == 200, r.text
    r = client.post(f"/api/proactive/suggestions/{sid}/approve")
    assert r.status_code == 400, r.text
    assert "已批准" in r.json()["detail"]
    assert "suggestion is" not in r.text


def test_proactive_execute_unapproved_error_is_chinese(client):
    sid = _pending_suggestion(client)
    r = client.post(f"/api/proactive/suggestions/{sid}/execute")
    assert r.status_code == 403, r.text
    assert "需要先批准" in r.json()["detail"]
    assert "requires approval" not in r.text


def test_accept_missing_handoff_error_is_chinese(client):
    r = client.post("/api/chief/handoffs/does-not-exist/accept")
    assert r.status_code == 409, r.text
    assert "交接单不存在" in r.json()["detail"]
    assert "handoff not found" not in r.text
