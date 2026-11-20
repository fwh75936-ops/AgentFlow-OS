"""Round-4 feature tests: skill package import, workflow condition /
approval / structural nodes, skill + knowledge injection into execution,
MCP config import, system settings + backup/restore, provider model
curation + detect. All against temp SQLite / mocked HTTP — no network.
"""
import io
import json
import os
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.database as database
from app import models
from app.database import Base, get_db


@pytest.fixture()
def dbx(tmp_path, monkeypatch):
    engine_ = create_engine("sqlite:///" + str(tmp_path / "r4.db"),
                            connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine_)
    TestSession = sessionmaker(bind=engine_, autocommit=False, autoflush=False)
    monkeypatch.setattr(database, "SessionLocal", TestSession)
    db = TestSession()
    yield db, tmp_path, TestSession
    db.close()


def client_for(router, TestSession):
    def override_get_db():
        db = TestSession()
        try:
            yield db
        finally:
            db.close()
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = override_get_db
    return TestClient(app)


def mkagent(db, name="员工甲", tools=None, skills=None):
    a = models.Agent(name=name, role="general", tools=tools or [],
                     skills=skills or [], capability_tags=[],
                     capability_scores={}, status="active",
                     system_prompt="你是一位员工。")
    db.add(a); db.commit(); db.refresh(a)
    return a


# ------------------------------------------------------------- skill import

def _skill_md(name, desc, body):
    return f"---\nname: {name}\ndescription: {desc}\n---\n{body}"


def test_skill_package_import_partial(dbx, monkeypatch):
    db, tmp_path, TestSession = dbx
    monkeypatch.setenv("AGENTFLOW_SKILLS_DIR", str(tmp_path / "skill_files"))
    from app.api.routes import router
    client = client_for(router, TestSession)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("good-one/SKILL.md", _skill_md("技能甲", "甲描述", "甲的指令"))
        z.writestr("good-one/scripts/run.py", "print('hi')")
        z.writestr("good-two/SKILL.md", _skill_md("技能乙", "乙描述", "乙的指令"))
        z.writestr("bad/SKILL.md", "---\ndescription: 没有名字\n---\n正文")
        z.writestr("not-a-skill/readme.txt", "没有 SKILL.md")
        z.writestr("../evil.txt", "逃逸")
    r = client.post("/api/skills/import",
                    files={"file": ("pack.zip", buf.getvalue(), "application/zip")})
    assert r.status_code == 200
    data = r.json()
    assert sorted(s["name"] for s in data["imported"]) == ["技能乙", "技能甲"]
    reasons = " ".join(s["reason"] for s in data["skipped"])
    assert "缺少 name" in reasons and "缺少 SKILL.md" in reasons
    assert any("evil" in s["where"] for s in data["skipped"])
    rows = db.query(models.Skill).filter_by(source="import").all()
    assert len(rows) == 2
    one = next(s for s in rows if s.name == "技能甲")
    assert one.instructions == "甲的指令" and one.trusted is False
    assert one.files and one.files[0]["path"] == "scripts/run.py"
    stored = tmp_path / "skill_files" / str(one.id) / "scripts" / "run.py"
    assert stored.read_text() == "print('hi')"


def test_skill_package_rejects_non_zip(dbx):
    db, tmp_path, TestSession = dbx
    from app.api.routes import router
    client = client_for(router, TestSession)
    r = client.post("/api/skills/import",
                    files={"file": ("x.zip", b"not a zip", "application/zip")})
    assert r.status_code == 400


# ------------------------------------------------------- injection into runs

def test_skill_instructions_injected(dbx):
    db, _, _ = dbx
    from app.execution.runner import _effective_system_prompt
    sk = models.Skill(name="报告写作", instructions="先给结论再给细节。",
                      enabled=True)
    db.add(sk); db.commit(); db.refresh(sk)
    agent = mkagent(db, skills=[sk.id])
    prompt = _effective_system_prompt(db, agent)
    assert "报告写作" in prompt and "先给结论再给细节" in prompt
    sk.enabled = False; db.commit()
    assert "报告写作" not in _effective_system_prompt(db, agent)


def test_knowledge_auto_retrieval(dbx):
    db, _, _ = dbx
    from app.execution.runner import _knowledge_hits
    db.add(models.MemoryItem(
        title="巡检手册", source="upload", scope="long_term",
        content="共享目录巡检要检查文件版本是否连续，发现冲突先通知负责人，不要直接覆盖。"))
    db.commit()
    task = models.Task(title="共享目录巡检", description="", status="pending")
    db.add(task); db.commit(); db.refresh(task)
    node = models.TaskNode(task_id=task.id, name="巡检要点分析",
                           node_type="agent", status="pending", dependencies=[])
    hits = _knowledge_hits(db, task, node)
    assert hits and hits[0]["item_id"] is not None
    assert hits[0]["score"] > 0


# ------------------------------------------------------------- workflow nodes

def _run_single_node(db, node_type, input_data, deps_outputs=None):
    from app.execution.runner import _run_node
    agent = mkagent(db)
    task = models.Task(title="节点测试", description="", status="running")
    db.add(task); db.commit(); db.refresh(task)
    dep_ids = []
    for i, out in enumerate(deps_outputs or []):
        dn = models.TaskNode(task_id=task.id, name=f"上游{i}", node_type="agent",
                             status="completed", output_data=out, dependencies=[])
        db.add(dn); db.flush()
        dep_ids.append(dn.id)
    node = models.TaskNode(task_id=task.id, name="被测节点", node_type=node_type,
                           status="pending", dependencies=dep_ids,
                           input_data=input_data)
    db.add(node); db.commit(); db.refresh(node)
    ok, et, msg = _run_node(task.id, node.id)
    db.expire_all()
    return db.query(models.TaskNode).get(node.id), ok, et, msg


def test_condition_node_pass_and_block(dbx):
    db, _, _ = dbx
    cfg = json.dumps({"config": {"condition": "count > 0"}})
    node, ok, _, _ = _run_single_node(db, "condition", cfg,
                                      deps_outputs=[json.dumps({"count": 3})])
    assert ok and node.status == "completed"
    assert json.loads(node.output_data)["result"] is True
    node, ok, et, msg = _run_single_node(db, "condition", cfg,
                                         deps_outputs=[json.dumps({"count": 0})])
    assert not ok and et == "CONDITION_NOT_MET" and node.status == "failed"
    cfg2 = json.dumps({"config": {"condition": "count > 0", "on_false": "continue"}})
    node, ok, _, _ = _run_single_node(db, "condition", cfg2,
                                      deps_outputs=[json.dumps({"count": 0})])
    assert ok and json.loads(node.output_data)["result"] is False


def test_approval_node_requires_decision(dbx):
    db, _, _ = dbx
    node, ok, et, _ = _run_single_node(db, "approval", json.dumps({"config": {"note": "确认发布"}}))
    assert not ok and et == "APPROVAL_REQUIRED"
    node, ok, _, _ = _run_single_node(
        db, "approval",
        json.dumps({"approved": True, "approval_note": "可以发布",
                    "config": {"note": "确认发布"}}))
    assert ok and json.loads(node.output_data)["approved"] is True


def test_structural_verify_node(dbx):
    db, _, _ = dbx
    node, ok, _, _ = _run_single_node(
        db, "verify_structural", json.dumps({"config": {}}),
        deps_outputs=[json.dumps({"summary": "好", "result": "完成"})])
    assert ok
    node, ok, et, _ = _run_single_node(
        db, "verify_structural", json.dumps({"config": {}}),
        deps_outputs=[json.dumps({"unrelated": 1})])
    assert not ok and et == "VERIFICATION_ERROR"


def test_compile_rejects_unconfigured_condition(dbx):
    from app.workflows.engine import compile_graph, WorkflowError
    graph = {"nodes": [
        {"id": "1", "data": {"label": "Start"}},
        {"id": "2", "data": {"label": "Condition：判断"}},
        {"id": "3", "data": {"label": "End"}}],
        "edges": [{"source": "1", "target": "2"}, {"source": "2", "target": "3"}]}
    with pytest.raises(WorkflowError):
        compile_graph(graph)


# ------------------------------------------------------------------ MCP import

def test_mcp_import_config(dbx):
    db, _, TestSession = dbx
    from app.mcp.routes import router
    client = client_for(router, TestSession)
    payload = {"text": json.dumps({"mcpServers": {
        "本地示例": {"command": "python3", "args": ["examples/mcp-server/server.py"]},
        "远程": {"url": "https://mcp.example.com/sse"},
        "坏的": {"args": []},
    }})}
    r = client.post("/api/mcp/import-config", json=payload)
    assert r.status_code == 200
    results = {x["name"]: x for x in r.json()["results"]}
    assert results["本地示例"]["ok"] and results["远程"]["ok"]
    assert not results["坏的"]["ok"]
    r2 = client.post("/api/mcp/import-config", json=payload)
    again = {x["name"]: x for x in r2.json()["results"]}
    assert not again["本地示例"]["ok"]  # duplicate reported, not re-added


# ------------------------------------------------------------- system settings

def test_system_settings_and_backup_restore(dbx, tmp_path, monkeypatch):
    db, _, TestSession = dbx
    from app.system import routes as sysroutes
    client = client_for(sysroutes.router, TestSession)
    r = client.put("/api/system/settings",
                   json={"proxy_mode": "custom", "proxy_url": "socks5://127.0.0.1:1080"})
    assert r.status_code == 200 and r.json()["proxy_mode"] == "custom"
    r = client.put("/api/system/settings", json={"proxy_mode": "bogus"})
    assert r.status_code == 400
    # backup -> restore roundtrip against a temp database file
    live = tmp_path / "live.db"
    engine_ = create_engine("sqlite:///" + str(live))
    Base.metadata.create_all(bind=engine_)
    monkeypatch.setattr(sysroutes.database, "DATABASE_URL", "sqlite:///" + str(live))
    r = client.get("/api/system/backup")
    assert r.status_code == 200
    import zipfile as _zf
    names = _zf.ZipFile(io.BytesIO(r.content)).namelist()
    assert "agentflow.db" in names and "manifest.json" in names
    r = client.post("/api/system/restore",
                    files={"file": ("b.zip", r.content, "application/zip")})
    assert r.status_code == 200 and r.json()["restart_required"] is True
    assert os.path.exists(str(live) + ".restore-staged")
    r = client.post("/api/system/restore",
                    files={"file": ("b.zip", b"junk", "application/zip")})
    assert r.status_code == 400


# ------------------------------------------------------- provider curation

def test_provider_models_and_detect(dbx, monkeypatch):
    db, _, TestSession = dbx
    from app.api.routes import router
    import app.llm.client as llm
    client = client_for(router, TestSession)
    r = client.post("/api/providers", json={
        "name": "测试商", "base_url": "https://api.example.com/v1",
        "api_key": "k", "format": "responses"})
    pid = r.json()["id"]
    assert r.json()["models"] == []
    r = client.put(f"/api/providers/{pid}/models",
                   json={"models": ["m-a", "m-b", "m-a", " "]})
    assert r.json()["models"] == ["m-a", "m-b"]
    monkeypatch.setattr(llm, "chat_completion",
                        lambda p, m, msgs, timeout=30: {"content": "ok",
                                                        "token_usage": 1,
                                                        "prompt_tokens": 1,
                                                        "completion_tokens": 0})
    r = client.post(f"/api/providers/{pid}/detect", json={"model": "m-a"})
    assert r.json()["ok"] is True
    r = client.post(f"/api/providers/{pid}/detect", json={})
    assert r.status_code == 400
