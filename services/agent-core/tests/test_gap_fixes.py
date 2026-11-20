"""Gap fixes (2026-11-17): evaluation run/delete endpoints and the
agent tool-permission endpoint. All against a temp SQLite via a minimal
app mounting only the real API router. The evaluation run test executes
the real repository benchmark (production engines, real pytest
subprocesses) — it takes a few seconds, like the standalone script.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api.routes import router
from app.database import Base, get_db


@pytest.fixture()
def client(tmp_path):
    engine = create_engine("sqlite:///" + str(tmp_path / "gap.db"),
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
    return TestClient(test_app)


def test_evaluation_run_produces_real_metrics_and_delete(client):
    r = client.post("/api/evaluations", json={"name": "gap-test", "mode": "adaptive"})
    assert r.status_code == 201
    ev = r.json()
    assert ev["status"] == "created" and ev["metrics"] is None

    r = client.post(f"/api/evaluations/{ev['id']}/run")
    assert r.status_code == 200
    done = r.json()
    assert done["status"] == "completed"
    m = done["metrics"]
    # Same numbers the standalone benchmark publishes for adaptive.
    assert m["success_rate"] == 1.0
    assert m["recovery_rate"] == 1.0
    assert m["scheduler"]["runs"] == 6 and m["scheduler"]["successes"] == 6
    assert m["self_loop"]["initially_failed"] == 3 and m["self_loop"]["repaired"] == 3
    assert m["token_usage"] == 0 and m["llm_called"] is False

    listed = client.get("/api/evaluations").json()
    assert any(x["id"] == ev["id"] and x["metrics"] for x in listed)

    r = client.delete(f"/api/evaluations/{ev['id']}")
    assert r.status_code == 200
    assert all(x["id"] != ev["id"] for x in client.get("/api/evaluations").json())
    assert client.delete(f"/api/evaluations/{ev['id']}").status_code == 404


def test_evaluation_run_unknown_mode_recorded_as_failed(client):
    r = client.post("/api/evaluations", json={"name": "bad", "mode": "bogus"})
    ev = r.json()
    r = client.post(f"/api/evaluations/{ev['id']}/run")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "failed" and "error" in body["metrics"]


def test_agent_tools_grant_revoke_and_validation(client):
    r = client.post("/api/agents", json={"name": "权限测试", "tools": ["file_read"]})
    assert r.status_code == 201
    aid = r.json()["id"]

    r = client.put(f"/api/agents/{aid}/tools",
                   json={"tools": ["file_read", "desktop_control"]})
    assert r.status_code == 200
    assert "desktop_control" in r.json()["tools"]
    got = client.get(f"/api/agents/{aid}").json()
    assert "desktop_control" in got["tools"]

    r = client.put(f"/api/agents/{aid}/tools", json={"tools": ["file_read"]})
    assert r.status_code == 200
    assert client.get(f"/api/agents/{aid}").json()["tools"] == ["file_read"]

    # Invented tool ids are rejected; legacy ids already held round-trip.
    r = client.put(f"/api/agents/{aid}/tools", json={"tools": ["nope_tool"]})
    assert r.status_code == 400
    r = client.put(f"/api/agents/{aid}/tools", json={"tools": ["terminal"]})
    assert r.status_code == 400  # not in registry and not currently held
    assert client.put("/api/agents/99999/tools",
                      json={"tools": []}).status_code == 404
