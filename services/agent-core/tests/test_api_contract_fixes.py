"""Regression tests for the 2026-11-19 backend bug sweep (round 1):
frontend/backend field-contract mismatches that silently emptied whole
UI panels, client-input errors surfacing as bare 500s, and honest-error
semantics (an upstream 500 is not a passing connection test, an empty
model reply is not a successful answer, a misspelled chat mode must not
silently execute as a different mode)."""
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models
from app.api.routes import router as api_router
from app.database import Base, get_db
from app.llm import client as llm
from app.mcp.routes import router as mcp_router
from app.memory.routes import router as memory_router
from app.resources.routes import router as resources_router
from app.system.routes import router as system_router
from app.teams.routes import router as teams_router
from app.workspace.routes import router as workspace_router


@pytest.fixture()
def client(tmp_path):
    engine = create_engine("sqlite:///" + str(tmp_path / "contract.db"),
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
    for r in (api_router, system_router, memory_router, resources_router,
              teams_router, workspace_router, mcp_router):
        test_app.include_router(r)
    test_app.dependency_overrides[get_db] = override_get_db
    c = TestClient(test_app, raise_server_exceptions=False)
    c.session_factory = Session
    return c


def _agent(client, name="员工甲", **kw):
    db = client.session_factory()
    a = models.Agent(name=name, status="active", **kw)
    db.add(a)
    db.commit()
    db.refresh(a)
    aid = a.id
    db.close()
    return aid


# ---- 3-2: knowledge paste-import field name ------------------------------
def test_import_text_accepts_frontend_text_field(client):
    r = client.post("/api/knowledge/import-text",
                    json={"title": "粘贴文档", "text": "这是通过粘贴导入的正文内容，用于检索。"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "indexed"


def test_import_text_still_rejects_empty(client):
    r = client.post("/api/knowledge/import-text", json={"title": "空", "text": ""})
    assert r.status_code == 400


# ---- 3-1: system usage shape matches the settings page -------------------
def test_usage_returns_by_lists_the_ui_reads(client):
    db = client.session_factory()
    agent = models.Agent(name="统计员", status="active")
    task = models.Task(title="统计任务", status="completed")
    db.add_all([agent, task])
    db.commit()
    db.add(models.ExecutionSnapshot(
        task_id=task.id,
        state={"node_token_usage": {"agent_id": agent.id, "total_tokens": 42}}))
    db.commit()
    agent_id = agent.id
    db.close()
    u = client.get("/api/system/usage").json()
    assert u["by_agent"] == [{"agent_id": agent_id, "name": "统计员",
                              "tokens": 42, "calls": 1}]
    assert u["by_task"][0]["title"] == "统计任务"
    assert u["by_task"][0]["tokens"] == 42
    assert u["by_task"][0]["calls"] == 1
    assert u["note"] == u["provider_attribution"]


# ---- 3-3: runtimes carry the key/label the UI filters on -----------------
def test_runtimes_have_key_and_label(client):
    rs = client.get("/api/system/runtimes").json()["runtimes"]
    keys = {r["key"] for r in rs}
    assert {"node", "npx", "python3", "uvx"} <= keys
    assert all(r["label"] for r in rs)


# ---- 2-2: settings validation errors are 400, not 500 --------------------
def test_settings_custom_proxy_without_url_is_400(client):
    r = client.put("/api/system/settings",
                   json={"proxy_mode": "custom", "proxy_url": "http://"})
    assert r.status_code == 400, r.text


# ---- 2-1: resource lease routes validate input ---------------------------
def test_release_missing_fields_is_400_not_500(client):
    r = client.post("/api/resources/leases/release", json={})
    assert r.status_code == 400


def test_renew_missing_fields_is_400_not_500(client):
    r = client.post("/api/resources/leases/renew", json={"resource_type": "desktop"})
    assert r.status_code == 400


# ---- 2-3: execute racing the atomic claim is 409, not 500 ----------------
def test_execute_task_runner_race_is_409(client, monkeypatch):
    db = client.session_factory()
    t = models.Task(title="竞态任务", status="pending")
    db.add(t)
    db.commit()
    tid = t.id
    db.close()

    import app.execution.runner as runner_mod

    def boom(db_, task):
        raise RuntimeError("任务正在执行中，不能重复启动")

    monkeypatch.setattr(runner_mod, "run_task", boom)
    r = client.post(f"/api/tasks/{tid}/execute")
    assert r.status_code == 409, r.text


# ---- 2-5: create_team rejects ghost members ------------------------------
def test_create_team_with_ghost_member_is_400(client):
    aid = _agent(client)
    r = client.post("/api/teams", json={
        "name": "幽灵队", "leader_agent_id": aid,
        "member_agent_ids": [aid, 99999]})
    assert r.status_code == 400, r.text
    assert client.get("/api/teams").json() == []


def test_create_team_with_real_members_still_works(client):
    aid = _agent(client)
    r = client.post("/api/teams", json={
        "name": "正常队", "leader_agent_id": aid, "member_agent_ids": [aid]})
    assert r.status_code == 201, r.text
    assert r.json()["members"][0]["agent_id"] == aid


# ---- 1-2: MCP HTTP test must not call an upstream 500 "ok" ---------------
def test_mcp_test_reports_upstream_500_as_not_ok(client, monkeypatch):
    db = client.session_factory()
    s = models.MCPServer(name="坏上游", transport="streamable_http",
                         command_or_url="http://upstream.invalid/mcp",
                         enabled=True, tools=[])
    db.add(s)
    db.commit()
    sid = s.id
    db.close()

    class FakeResp:
        status_code = 500

    class FakeClient:
        def __init__(self, *a, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url):
            return FakeResp()

    monkeypatch.setattr(httpx, "Client", FakeClient)
    r = client.post(f"/api/mcp-servers/{sid}/test")
    assert r.status_code == 200
    assert r.json()["ok"] is False
    assert r.json()["status_code"] == 500


# ---- MCP test errors are Chinese, never raw English -----------------------
def test_mcp_test_stdio_error_is_chinese(client):
    db = client.session_factory()
    s = models.MCPServer(name="本地命令", transport="stdio",
                         command_or_url="npx -y server", enabled=True, tools=[])
    db.add(s)
    db.commit()
    sid = s.id
    db.close()
    r = client.post(f"/api/mcp-servers/{sid}/test")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert "不做静默连接测试" in body["error"]
    assert "stdio" not in body["error"] and "subprocess" not in body["error"]


def test_mcp_test_connect_error_is_chinese(client, monkeypatch):
    db = client.session_factory()
    s = models.MCPServer(name="连不上", transport="streamable_http",
                         command_or_url="http://127.0.0.1:9/mcp",
                         enabled=True, tools=[])
    db.add(s)
    db.commit()
    sid = s.id
    db.close()

    class FailingClient:
        def __init__(self, *a, **kw):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url):
            raise httpx.ConnectError("boom", request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "Client", FailingClient)
    r = client.post(f"/api/mcp-servers/{sid}/test")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert "连接失败" in body["error"] and "boom" not in body["error"]


# ---- 3-6: MCP import-config failures carry "reason" ----------------------
def test_mcp_import_config_reports_reason(client):
    payload = {"text": '{"mcpServers": {"坏配置": {"env": "不是对象"}}}'}
    r = client.post("/api/mcp/import-config", json=payload)
    assert r.status_code == 200, r.text
    result = r.json()["results"][0]
    assert result["ok"] is False
    assert result["reason"] == "env 必须是对象"


# ---- 2-8: misspelled chat mode is an error, not a silent direct dispatch -
def test_chat_invalid_mode_is_400(client):
    db = client.session_factory()
    a = models.Agent(name="队员", status="active")
    team = models.Team(name="群聊队")
    db.add_all([a, team])
    db.commit()
    db.add(models.TeamMember(team_id=team.id, agent_id=a.id))
    db.commit()
    tid = team.id
    db.close()
    r = client.post(f"/api/teams/{tid}/channel/messages",
                    json={"content": "大家好", "mode": "chif"})
    assert r.status_code == 400, r.text


# ---- 4-2: empty chat_completions reply is an error, like other formats ---
def test_chat_completions_empty_content_raises(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "choices": [{"message": {"content": "   "}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 0, "total_tokens": 1}})

    def fake_client(timeout):
        return httpx.Client(transport=httpx.MockTransport(handler), timeout=timeout)

    monkeypatch.setattr(llm, "_client", fake_client)
    provider = SimpleNamespace(base_url="https://upstream.example/v1",
                               api_key="k", format="chat_completions")
    with pytest.raises(llm.LLMError):
        llm.chat_completion(provider, "m", [{"role": "user", "content": "hi"}])


# ---- 2-7: task input ranges are validated at creation --------------------
def test_create_task_rejects_out_of_range_priority(client):
    r = client.post("/api/tasks", json={"title": "x", "priority": 9})
    assert r.status_code == 422


def test_create_task_rejects_unknown_mode(client):
    r = client.post("/api/tasks", json={"title": "x", "scheduler_mode": "bogus"})
    assert r.status_code == 422


def test_create_task_rejects_ghost_team(client):
    r = client.post("/api/tasks", json={"title": "x", "team_id": 424242})
    assert r.status_code == 404


# ---- 2-9: update/copy responses carry the real activity_status -----------
def test_update_agent_response_has_real_activity_status(client):
    # An agent mid-run whose profile is edited must not come back as
    # "idle" in the PUT response while the very next GET says it is
    # calling the model. (Status itself is toggled via /toggle, not PUT:
    # AgentIn has no status field by design.)
    db = client.session_factory()
    a = models.Agent(name="员工甲", status="active", runtime_status="Running")
    db.add(a)
    db.commit()
    t = models.Task(title="在跑的任务", status="running")
    db.add(t)
    db.commit()
    db.add(models.TaskNode(task_id=t.id, name="执行", node_type="llm",
                           status="running", assigned_agent_id=a.id))
    db.commit()
    aid = a.id
    db.close()
    r = client.put(f"/api/agents/{aid}", json={"name": "员工甲改"})
    assert r.status_code == 200, r.text
    assert r.json()["activity_status"] == "model_calling"
    assert client.get(f"/api/agents/{aid}").json()["activity_status"] == "model_calling"


# ---- copy: a clone must not inherit the original's track record ----------
def test_copy_agent_starts_with_fresh_performance(client):
    aid = _agent(client, execution_count=7, success_count=6,
                 historical_success_rate=0.95, average_latency=3.0,
                 runtime_status="Running")
    r = client.post(f"/api/agents/{aid}/copy")
    assert r.status_code == 201, r.text
    clone = r.json()
    assert clone["execution_count"] == 0
    assert clone["success_count"] == 0
    # No executions yet -> no measured rate (None = 暂无数据), not the
    # original's 0.95 and not the scheduler's 0.5 prior either.
    assert clone["historical_success_rate"] is None
    assert clone["runtime_status"] == "Idle"


# ---- 3-8: the scheduler's neutral prior is not a measured success rate ---
def test_success_rate_is_none_until_first_execution(client):
    fresh = _agent(client, "新人", execution_count=0,
                   historical_success_rate=0.5)
    veteran = _agent(client, "老手", execution_count=4, success_count=3,
                     historical_success_rate=0.75)
    agents = {a["id"]: a for a in client.get("/api/agents").json()}
    assert agents[fresh]["historical_success_rate"] is None
    assert agents[veteran]["historical_success_rate"] == 0.75
    detail = client.get(f"/api/agents/{fresh}/detail").json()
    assert detail["detail"]["performance"]["historical_success_rate"] is None


# ---- 2-4: sub-resource lists 404 for a missing parent --------------------
def test_task_messages_missing_task_is_404(client):
    assert client.get("/api/tasks/987654/messages").status_code == 404
    assert client.get("/api/tasks/987654/snapshots").status_code == 404


def test_session_logs_missing_session_is_404(client):
    assert client.get("/api/workspace/sessions/987654/logs").status_code == 404
