"""Provider API formats: chat_completions / responses / anthropic.

The HTTP layer is mocked with httpx.MockTransport — the tests assert the
exact request shape each protocol produces (URL, auth headers, translated
body) and that each protocol's response shape is translated back into
the internal {content, token_usage, prompt_tokens, completion_tokens}
form. No network, no real keys.
"""
import json
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.api.routes import router
from app.database import Base, get_db
from app.llm import client as llm

MSGS = [{"role": "system", "content": "你是一位助手"},
        {"role": "user", "content": "你好"}]


def provider(fmt, base_url="https://upstream.example/v1", api_key="test-key"):
    return SimpleNamespace(base_url=base_url, api_key=api_key, format=fmt)


@pytest.fixture()
def captured(monkeypatch):
    """Route every client call through a MockTransport that records the
    request and answers with a per-protocol canned (but shape-real)
    response."""
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["headers"] = dict(request.headers)
        seen["body"] = json.loads(request.content) if request.content else None
        url = str(request.url)
        if url.endswith("/chat/completions"):
            return httpx.Response(200, json={
                "choices": [{"message": {"content": "来自 Chat Completions"}}],
                "usage": {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18}})
        if url.endswith("/responses"):
            return httpx.Response(200, json={
                "output": [{"type": "message", "content": [
                    {"type": "output_text", "text": "来自 "},
                    {"type": "output_text", "text": "Responses"}]}],
                "usage": {"input_tokens": 13, "output_tokens": 5, "total_tokens": 18}})
        if url.endswith("/v1/messages"):
            return httpx.Response(200, json={
                "content": [{"type": "text", "text": "来自 "},
                            {"type": "text", "text": "Anthropic"}],
                "usage": {"input_tokens": 9, "output_tokens": 4}})
        if url.endswith("/models") or url.endswith("/v1/models"):
            return httpx.Response(200, json={"data": [{"id": "m-one"}, {"id": "m-two"}]})
        return httpx.Response(404, json={"error": "no route"})

    def fake_client(timeout):
        return httpx.Client(transport=httpx.MockTransport(handler), timeout=timeout)

    monkeypatch.setattr(llm, "_client", fake_client)
    return seen


# ------------------------------------------------------------ chat_completions

def test_chat_completions_chat(captured):
    out = llm.chat_completion(provider("chat_completions"), "m-one", MSGS)
    assert captured["url"].endswith("/v1/chat/completions")
    assert captured["headers"]["authorization"] == "Bearer test-key"
    assert captured["body"]["messages"] == MSGS
    assert out == {"content": "来自 Chat Completions", "token_usage": 18,
                   "prompt_tokens": 11, "completion_tokens": 7}


def test_chat_completions_models(captured):
    assert llm.fetch_models(provider("chat_completions")) == ["m-one", "m-two"]
    assert captured["url"].endswith("/v1/models")


# ------------------------------------------------------------------- responses

def test_responses_chat_translation(captured):
    out = llm.chat_completion(provider("responses"), "m-one", MSGS)
    assert captured["url"].endswith("/v1/responses")
    assert captured["headers"]["authorization"] == "Bearer test-key"
    body = captured["body"]
    assert body["instructions"] == "你是一位助手"
    assert body["input"] == [{"role": "user", "content": [
        {"type": "input_text", "text": "你好"}]}]
    assert "messages" not in body
    assert out == {"content": "来自 Responses", "token_usage": 18,
                   "prompt_tokens": 13, "completion_tokens": 5}


def test_responses_models_still_openai_style(captured):
    assert llm.fetch_models(provider("responses")) == ["m-one", "m-two"]
    assert captured["url"].endswith("/v1/models")


# ------------------------------------------------------------------- anthropic

def test_anthropic_chat_translation(captured):
    p = provider("anthropic", base_url="https://api.anthropic.com")
    out = llm.chat_completion(p, "claude-x", MSGS)
    assert captured["url"] == "https://api.anthropic.com/v1/messages"
    headers = captured["headers"]
    assert headers["x-api-key"] == "test-key"
    assert headers["anthropic-version"] == "2023-06-01"
    assert "authorization" not in headers
    body = captured["body"]
    assert body["system"] == "你是一位助手"
    assert body["messages"] == [{"role": "user", "content": "你好"}]
    assert body["max_tokens"] > 0
    assert out == {"content": "来自 Anthropic", "token_usage": 13,
                   "prompt_tokens": 9, "completion_tokens": 4}


def test_anthropic_base_with_v1_not_doubled(captured):
    p = provider("anthropic", base_url="https://proxy.example/v1")
    llm.fetch_models(p)
    assert captured["url"] == "https://proxy.example/v1/models"


def test_anthropic_models(captured):
    p = provider("anthropic", base_url="https://api.anthropic.com")
    assert llm.fetch_models(p) == ["m-one", "m-two"]
    assert captured["url"] == "https://api.anthropic.com/v1/models"


def test_unknown_format_falls_back_to_chat_completions(captured):
    llm.chat_completion(provider("something-else"), "m-one", MSGS)
    assert captured["url"].endswith("/v1/chat/completions")


# -------------------------------------------------------------- API + migration

@pytest.fixture()
def api_client(tmp_path):
    engine = create_engine("sqlite:///" + str(tmp_path / "fmt.db"),
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


def test_api_format_default_and_validation(api_client):
    r = api_client.post("/api/providers", json={
        "name": "甲", "base_url": "https://a.example/v1", "api_key": "k"})
    assert r.status_code == 201
    assert r.json()["format"] == "chat_completions"

    r = api_client.post("/api/providers", json={
        "name": "乙", "base_url": "https://api.openai.com/v1",
        "api_key": "k", "format": "responses"})
    assert r.status_code == 201
    assert r.json()["format"] == "responses"
    pid = r.json()["id"]

    r = api_client.post("/api/providers", json={
        "name": "丙", "base_url": "https://x.example", "format": "gemini"})
    assert r.status_code == 400

    r = api_client.put(f"/api/providers/{pid}", json={
        "name": "乙", "base_url": "https://api.anthropic.com",
        "format": "anthropic", "enabled": True})
    assert r.status_code == 200
    assert r.json()["format"] == "anthropic"


def test_format_column_migration_defaults_existing_rows(tmp_path, monkeypatch):
    """A pre-format database gains the column with existing rows reading
    as chat_completions (the protocol they were always called with)."""
    import app.database as database

    engine = create_engine("sqlite:///" + str(tmp_path / "old.db"),
                           connect_args={"check_same_thread": False})
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE model_providers (id INTEGER PRIMARY KEY, "
            "name VARCHAR(120) NOT NULL, base_url VARCHAR(500) NOT NULL, "
            "api_key TEXT, enabled BOOLEAN, created_at DATETIME, updated_at DATETIME)"))
        conn.execute(text(
            "INSERT INTO model_providers (name, base_url, api_key, enabled) "
            "VALUES ('DeepSeek', 'https://api.deepseek.com', 'k', 1)"))
    monkeypatch.setattr(database, "engine", engine)
    database._ensure_added_columns()
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT name, format FROM model_providers")).fetchall()
    assert rows == [("DeepSeek", "chat_completions")]
