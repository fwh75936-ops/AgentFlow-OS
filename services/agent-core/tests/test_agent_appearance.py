"""员工形象配置（appearance）：存取往返、缺省为空、越界值规整。
契约见 docs/APPEARANCE.md：{"gender": "female"|"male", "skin": 1-5,
"outfit": 1-10, "hair": 1-6}；空 dict 表示未设置，界面回退旧头像预设。"""
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
    engine = create_engine("sqlite:///" + str(tmp_path / "appearance.db"),
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


LOOK = {"gender": "male", "skin": 3, "outfit": 10, "hair": 2}


def test_appearance_roundtrip(client):
    r = client.post("/api/agents", json={
        "name": "陈工", "role": "developer", "avatar": "avatar-05",
        "appearance": LOOK})
    assert r.status_code == 201
    assert r.json()["appearance"] == LOOK
    aid = r.json()["id"]

    got = client.get(f"/api/agents/{aid}").json()
    assert got["appearance"] == LOOK
    # The legacy preset id is untouched and still returned alongside.
    assert got["avatar"] == "avatar-05"

    detail = client.get(f"/api/agents/{aid}/detail").json()
    assert detail["appearance"] == LOOK

    new_look = {"gender": "male", "skin": 5, "outfit": 3, "hair": 1}
    r = client.put(f"/api/agents/{aid}", json={
        "name": "陈工", "role": "developer", "appearance": new_look})
    assert r.status_code == 200
    assert r.json()["appearance"] == new_look


def test_appearance_defaults_to_unset(client):
    r = client.post("/api/agents", json={"name": "新人", "role": "general"})
    assert r.status_code == 201
    assert r.json()["appearance"] == {}
    r = client.post("/api/agents", json={
        "name": "路人", "role": "general", "appearance": {}})
    assert r.json()["appearance"] == {}


def test_appearance_values_are_normalized(client):
    r = client.post("/api/agents", json={
        "name": "越界", "role": "general",
        "appearance": {"gender": "x", "skin": 99, "outfit": 0, "hair": "abc"}})
    assert r.status_code == 201
    assert r.json()["appearance"] == {
        "gender": "female", "skin": 2, "outfit": 1, "hair": 1}
    # Partial config: provided fields are kept, missing ones defaulted.
    r = client.post("/api/agents", json={
        "name": "半套", "role": "general", "appearance": {"skin": 5, "outfit": 7}})
    assert r.json()["appearance"] == {
        "gender": "female", "skin": 5, "outfit": 7, "hair": 1}
