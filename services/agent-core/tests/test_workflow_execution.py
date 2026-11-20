"""Workflow graph -> DAG -> real execution tests.

The graph is compiled to Task + TaskNodes and executed by the standard
runner: tool nodes really execute through the Tool Registry (files land
on disk), LLM nodes without a provider fail honestly with MODEL_ERROR,
and the WorkflowRun record mirrors the Task outcome.
"""
import os

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.database as database
from app import models
from app.database import Base
from app.tools import registry
from app.workflows import engine


@pytest.fixture()
def dbx(tmp_path, monkeypatch):
    engine_ = create_engine("sqlite:///" + str(tmp_path / "wf.db"),
                            connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine_)
    TestSession = sessionmaker(bind=engine_, autocommit=False, autoflush=False)
    monkeypatch.setattr(database, "SessionLocal", TestSession)
    monkeypatch.setattr(registry, "WORKSPACE_ROOT", str(tmp_path / "wsroot"))
    db = TestSession()
    yield db, tmp_path
    db.close()


def mkagent(db, name, tools):
    a = models.Agent(name=name, role="t", tools=tools, capability_tags=[],
                     capability_scores={}, status="active")
    db.add(a); db.commit(); db.refresh(a); return a


def save_wf(db, name, graph):
    wf = models.Workflow(name=name, graph=graph)
    db.add(wf); db.commit(); db.refresh(wf); return wf


TOOL_GRAPH = {
    "nodes": [
        {"id": "1", "position": {"x": 0, "y": 0}, "data": {"label": "Start"}},
        {"id": "2", "position": {"x": 1, "y": 0},
         "data": {"label": "Tool：写文件", "tool_id": "file_write",
                  "tool_args": {"path": "wf/hello.txt", "content": "workflow-ran"}}},
        {"id": "3", "position": {"x": 2, "y": 0},
         "data": {"label": "Tool：读文件", "tool_id": "file_read",
                  "tool_args": {"path": "wf/hello.txt"}}},
        {"id": "4", "position": {"x": 3, "y": 0}, "data": {"label": "End"}},
    ],
    "edges": [{"id": "e1", "source": "1", "target": "2"},
              {"id": "e2", "source": "2", "target": "3"},
              {"id": "e3", "source": "3", "target": "4"}],
}


def test_tool_workflow_executes_for_real(dbx):
    db, tmp_path = dbx
    mkagent(db, "worker", ["file_write", "file_read"])
    wf = save_wf(db, "写读工作流", TOOL_GRAPH)
    run = engine.execute_workflow(db, wf.id)
    assert run.status == "completed", run.error
    assert set(run.node_map) == {"2", "3"}
    task = db.query(models.Task).get(run.task_id)
    assert task.status == "completed"
    nodes = db.query(models.TaskNode).filter_by(task_id=task.id).all()
    assert len(nodes) == 2 and all(n.status == "completed" for n in nodes)
    read_node = next(n for n in nodes if "读文件" in n.name)
    assert "workflow-ran" in (read_node.output_data or "")
    written = tmp_path / "wsroot" / "wf" / "hello.txt"
    assert written.read_text(encoding="utf-8") == "workflow-ran"
    runs = db.query(models.WorkflowRun).filter_by(workflow_id=wf.id).all()
    assert len(runs) == 1 and runs[0].finished_at is not None


def test_llm_node_without_provider_fails_honestly(dbx):
    db, _tmp = dbx
    mkagent(db, "thinker", [])
    graph = {"nodes": [
        {"id": "1", "data": {"label": "Start"}},
        {"id": "2", "data": {"label": "Agent：任务规划"}},
        {"id": "3", "data": {"label": "End"}}],
        "edges": [{"id": "e1", "source": "1", "target": "2"},
                  {"id": "e2", "source": "2", "target": "3"}]}
    wf = save_wf(db, "LLM工作流", graph)
    run = engine.execute_workflow(db, wf.id)
    assert run.status == "failed"
    task = db.query(models.Task).get(run.task_id)
    assert task.error_type == "MODEL_ERROR"
    node = db.query(models.TaskNode).filter_by(task_id=task.id).first()
    assert node.status == "failed" and node.error_type == "MODEL_ERROR"


FAILING_LLM_GRAPH = {"nodes": [
    {"id": "1", "data": {"label": "Start"}},
    {"id": "2", "data": {"label": "Agent：任务规划"}},
    {"id": "3", "data": {"label": "End"}}],
    "edges": [{"id": "e1", "source": "1", "target": "2"},
              {"id": "e2", "source": "2", "target": "3"}]}


def test_self_loop_history_detail_has_no_error_code_prefix(dbx):
    """失败原因进自循环历史时不得带 "MODEL_ERROR: ..." 这类机器码前缀：
    历史是给人看的执行记录，错误类型已单独存在 error_type 字段里。"""
    db, _tmp = dbx
    mkagent(db, "thinker", [])
    wf = save_wf(db, "失败历史工作流", FAILING_LLM_GRAPH)
    run = engine.execute_workflow(db, wf.id)
    assert run.status == "failed"
    task = db.query(models.Task).get(run.task_id)
    history = [h for snap in db.query(models.ExecutionSnapshot).filter_by(task_id=task.id).all()
               for h in (snap.state or {}).get("self_loop_history", [])]
    details = [h.get("detail", "") for h in history if h.get("state") == "ANALYZE"]
    assert details, "no ANALYZE step recorded"
    assert all("MODEL_ERROR" not in d and "DEPENDENCY_ERROR" not in d
               for d in details), details


def test_workflow_default_retries_trigger_self_loop(dbx):
    """Regression: workflow executions used to default max_retries=0 (route
    and engine), so a failing node never entered the Self-Loop repair path —
    unlike tasks from the task center, where run_task defaults to 2. With
    the aligned default, an honestly failing node must be retried: the
    repair hook runs (task.retry_count grows) and the Self-Loop history
    records a REPAIR step before the run finally fails."""
    import inspect
    assert inspect.signature(engine.execute_workflow).parameters["max_retries"].default == 2
    db, _tmp = dbx
    mkagent(db, "thinker", [])
    wf = save_wf(db, "默认重试工作流", FAILING_LLM_GRAPH)
    run = engine.execute_workflow(db, wf.id)  # no max_retries passed
    assert run.status == "failed"
    task = db.query(models.Task).get(run.task_id)
    assert task.retry_count >= 1, "Self-Loop repair never ran: default retries still 0"
    states = [h.get("state")
              for snap in db.query(models.ExecutionSnapshot).filter_by(task_id=task.id).all()
              for h in (snap.state or {}).get("self_loop_history", [])]
    assert "REPAIR" in states


def test_workflow_execute_route_defaults_to_retries(dbx):
    """Same regression through the real HTTP route: POSTing an execute
    request without max_retries must still retry (route default aligned
    with the task path), not silently run with 0 retries."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy.orm import sessionmaker
    from app.database import get_db
    from app.workflows.routes import router as workflow_router

    db, _tmp = dbx
    mkagent(db, "thinker", [])
    wf = save_wf(db, "路由默认重试工作流", FAILING_LLM_GRAPH)
    Session = sessionmaker(bind=db.get_bind(), autocommit=False, autoflush=False)

    def override_get_db():
        s = Session()
        try:
            yield s
        finally:
            s.close()

    test_app = FastAPI()
    test_app.include_router(workflow_router)
    test_app.dependency_overrides[get_db] = override_get_db
    client = TestClient(test_app)
    r = client.post(f"/api/workflows/{wf.id}/execute", json={})
    assert r.status_code == 201, r.text
    assert r.json()["status"] == "failed"
    task = db.query(models.Task).get(r.json()["task_id"])
    assert task.retry_count >= 1, "route default still suppresses Self-Loop retries"


def test_cycle_and_unsupported_kind_rejected(dbx):
    db, _tmp = dbx
    mkagent(db, "worker", ["file_write"])
    cyclic = {"nodes": [
        {"id": "1", "data": {"label": "Tool：甲", "tool_id": "file_write",
                             "tool_args": {"path": "a.txt", "content": "x"}}},
        {"id": "2", "data": {"label": "Tool：乙", "tool_id": "file_write",
                             "tool_args": {"path": "b.txt", "content": "y"}}}],
        "edges": [{"id": "e1", "source": "1", "target": "2"},
                  {"id": "e2", "source": "2", "target": "1"}]}
    wf = save_wf(db, "循环图", cyclic)
    with pytest.raises(engine.WorkflowError):
        engine.execute_workflow(db, wf.id)
    loop = {"nodes": [{"id": "1", "data": {"label": "Loop"}}], "edges": []}
    wf2 = save_wf(db, "不支持节点", loop)
    with pytest.raises(engine.WorkflowError):
        engine.execute_workflow(db, wf2.id)


def test_runner_sessions_stay_on_task_database(tmp_path, monkeypatch):
    """Regression: a background runner thread must not hop databases.

    run_task opens fresh sessions for its DAG passes and node threads. It
    used to resolve app.database.SessionLocal lazily at those points, so a
    thread started under one database (e.g. team-chat dispatch) that was
    still running when another test rebound the global silently executed
    the OTHER database's nodes: fresh databases collide on task id 1, the
    foreign pass claimed our node and held it in "running", and our own
    pass reported it as an incomplete node — a false DEPENDENCY_ERROR
    (flaky: test_llm_node_without_provider_fails_honestly failed ~1 in 3
    full-suite runs whenever a _run_direct_task thread was still alive).
    The runner now derives its session factory from the task session's
    own engine, so the pass can only ever touch the task's own database.
    """
    from app.execution.runner import run_task

    def _fresh(name):
        eng = create_engine("sqlite:///" + str(tmp_path / name),
                            connect_args={"check_same_thread": False})
        Base.metadata.create_all(bind=eng)
        return sessionmaker(bind=eng, autocommit=False, autoflush=False)

    SessionA, SessionB = _fresh("a.db"), _fresh("b.db")
    # Database B is what the global points at while the run executes:
    # exactly the situation of a lingering thread after a rebind.
    monkeypatch.setattr(database, "SessionLocal", SessionB)
    db_a, db_b = SessionA(), SessionB()
    try:
        # Same ids in both databases (fresh autoincrement): the collision
        # the old code tripped over.
        mkagent(db_a, "thinker", [])
        task_a = models.Task(title="t", description="d",
                             scheduler_mode="adaptive", status="pending")
        db_a.add(task_a); db_a.flush()
        node_a = models.TaskNode(task_id=task_a.id, name="执行",
                                 node_type="agent", required_capabilities=[],
                                 required_tools=[], dependencies=[],
                                 status="pending")
        db_a.add(node_a); db_a.commit()
        task_b = models.Task(title="t", description="d",
                             scheduler_mode="adaptive", status="pending")
        db_b.add(task_b); db_b.flush()
        node_b = models.TaskNode(task_id=task_b.id, name="执行",
                                 node_type="agent", required_capabilities=[],
                                 required_tools=[], dependencies=[],
                                 status="pending")
        db_b.add(node_b); db_b.commit()
        assert task_a.id == task_b.id and node_a.id == node_b.id

        run_task(db_a, task_a, required_output_fields=["执行"])
        db_a.refresh(task_a); db_b.refresh(task_b)
        db_a.refresh(node_a); db_b.refresh(node_b)
        assert task_a.status == "failed"
        assert task_a.error_type == "MODEL_ERROR", task_a.error_message
        assert node_a.status == "failed" and node_a.error_type == "MODEL_ERROR"
        # Database B must be completely untouched: its node never claimed.
        assert task_b.status == "pending"
        assert node_b.status == "pending", "runner hopped to the other database"
    finally:
        db_a.close(); db_b.close()


def test_workflow_task_description_and_messages_are_chinese(dbx):
    """工作流触发的任务描述与执行过程消息不得含英文模板句：
    描述曾是 "Executed from workflow #N (...)"，节点消息曾是
    "Node 'X' completed by ..." / "assigned to ..."。"""
    db, _tmp = dbx
    mkagent(db, "worker", ["file_write", "file_read"])
    wf = save_wf(db, "读写工作流", TOOL_GRAPH)
    run = engine.execute_workflow(db, wf.id)
    assert run.status == "completed"
    task = db.query(models.Task).get(run.task_id)
    assert "由工作流" in (task.description or ""), task.description
    assert "Executed" not in (task.description or "")
    msgs = db.query(models.AgentMessage).filter_by(task_id=task.id).all()
    assert msgs, "no task messages recorded"
    banned = ["Node '", "Tool node", "assigned to", "completed by",
              "executed ", "failed:"]
    for m in msgs:
        assert not any(b in m.content for b in banned), m.content
    joined = "\n".join(m.content for m in msgs)
    assert "已指派给" in joined, joined
    assert "已由" in joined and ("完成" in joined or "执行" in joined), joined


def test_failed_node_message_is_chinese(dbx):
    """无模型节点失败时，任务消息是中文句式而非 "Node 'X' failed: ..."。"""
    db, _tmp = dbx
    mkagent(db, "thinker", [])
    wf = save_wf(db, "失败消息工作流", FAILING_LLM_GRAPH)
    run = engine.execute_workflow(db, wf.id)
    assert run.status == "failed"
    task = db.query(models.Task).get(run.task_id)
    msgs = db.query(models.AgentMessage).filter_by(task_id=task.id).all()
    assert msgs, "no task messages recorded"
    joined = "\n".join(m.content for m in msgs)
    assert "Node '" not in joined and "failed:" not in joined, joined
    assert "失败" in joined, joined
