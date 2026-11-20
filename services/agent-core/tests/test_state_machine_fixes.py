"""Regression tests for the 2026-11-19 backend bug sweep (round 1),
state-machine / lease / verification-freshness batch:

- desktop lease could be released anonymously (agent_id=None)
- chief plan nodes with no matching action were marked completed
  without producing anything （系统设计/成果汇总汇报）
- chief missions could be re-executed; failed missions left pending
  nodes dangling; handoffs accepted nonexistent artifacts
- executing a completed/cancelled task span through zero nodes and
  reported success; workflow graphs could preset approved=true and
  bypass the human approval gate forever
- a completed node could be flipped back to running and re-executed
- verification freshness only compared SharedFileMeta, so terminal
  writes (which never touch the meta row) kept stale verifications
  "current"; reviews of meta-less files were born stale
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import app.database as database
from app import models
from app.database import Base
from app.verification import reviewer, state as vs
from app.workspace import service as ws


@pytest.fixture()
def dbx(tmp_path, monkeypatch):
    root = tmp_path / "shared"
    root.mkdir()
    monkeypatch.setenv("AGENTFLOW_SHARED_ROOT", str(root))
    engine = create_engine("sqlite:///" + str(tmp_path / "sm.db"),
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    monkeypatch.setattr(database, "SessionLocal", factory)
    db = factory()
    yield db, str(root)
    db.close()


def mkagent(db, name, tools=None, caps=None, **kw):
    kw.setdefault("status", "active")
    a = models.Agent(name=name, role="t", tools=tools or [],
                     capability_tags=caps or [], capability_scores={}, **kw)
    db.add(a)
    db.commit()
    db.refresh(a)
    return a


# ---- desktop lease: anonymous release must not work ----------------------
def test_desktop_lease_cannot_be_released_anonymously(dbx):
    db, _root = dbx
    a = mkagent(db, "持有人", tools=["desktop_control"])
    b = mkagent(db, "别人", tools=["desktop_control"])
    ws.acquire_desktop_lease(db, a.id, 300)
    with pytest.raises(ValueError):
        ws.release_desktop_lease(db)  # no agent_id at all
    with pytest.raises(ValueError):
        ws.release_desktop_lease(db, agent_id=b.id)  # not the holder
    assert ws.current_lease(db)["holder_agent_id"] == a.id
    out = ws.release_desktop_lease(db, agent_id=a.id)  # the holder can
    assert out["state"] == "released"


def test_desktop_queue_skips_disabled_agent(dbx):
    db, _root = dbx
    holder = mkagent(db, "当前持有人", tools=["desktop_control"])
    gone = mkagent(db, "已停用", tools=["desktop_control"], status="disabled")
    nxt = mkagent(db, "下一位", tools=["desktop_control"])
    ws.acquire_desktop_lease(db, holder.id, 300)
    ws.acquire_desktop_lease(db, gone.id, 300)  # queued
    ws.acquire_desktop_lease(db, nxt.id, 300)   # queued behind
    out = ws.release_desktop_lease(db, agent_id=holder.id)
    assert out["granted_agent_id"] == nxt.id


# ---- verification freshness probes the real file --------------------------
def test_terminal_write_stales_verification(dbx):
    db, root = dbx
    dev = mkagent(db, "dev")
    ws.write_file(db, dev.id, "proj/a.py", "v1\n")
    rec = vs.record_verification(db, "proj/a.py", True, test_run_id="run-1")
    assert vs.is_verified(db, "proj/a.py")
    # A terminal/session write bypasses write_file: bytes change, the
    # SharedFileMeta row does not. Freshness must still notice.
    with open(os.path.join(root, "proj", "a.py"), "w") as f:
        f.write("tampered\n")
    assert not vs.is_verified(db, "proj/a.py")
    db.refresh(rec)
    assert rec.status == "stale"


def test_new_verification_supersedes_old_current(dbx):
    db, _root = dbx
    dev = mkagent(db, "dev")
    ws.write_file(db, dev.id, "proj/b.py", "x\n")
    first = vs.record_verification(db, "proj/b.py", True, test_run_id="r1")
    vs.record_verification(db, "proj/b.py", False, test_run_id="r2")
    db.refresh(first)
    assert first.status == "superseded"
    currents = (db.query(models.ArtifactVerification)
                .filter_by(artifact_path="proj/b.py", status="current").all())
    assert len(currents) == 1 and currents[0].passed is False


def test_review_of_metaless_file_is_not_born_stale(dbx):
    db, root = dbx
    author = mkagent(db, "作者")
    rev = mkagent(db, "评审", caps=["review"])
    # File placed directly on disk (e.g. by the user): no SharedFileMeta.
    os.makedirs(os.path.join(root, "ext"), exist_ok=True)
    with open(os.path.join(root, "ext", "note.txt"), "w") as f:
        f.write("外部文件内容，足够长。\n")
    review = reviewer.review_artifact(db, "ext/note.txt", rev.id,
                                      author_agent_id=author.id,
                                      checks=[{"type": "nonempty"}])
    assert review.status == "current"
    assert reviewer.current_review(db, "ext/note.txt") is not None
    # ...but a later direct edit still stales it.
    with open(os.path.join(root, "ext", "note.txt"), "w") as f:
        f.write("被改掉了。\n")
    assert reviewer.current_review(db, "ext/note.txt") is None


def test_disabled_agent_cannot_review(dbx):
    db, _root = dbx
    dev = mkagent(db, "dev")
    rev = mkagent(db, "停用评审", caps=["review"], status="disabled")
    ws.write_file(db, dev.id, "proj/c.py", "x\n")
    with pytest.raises(reviewer.ReviewError):
        reviewer.review_artifact(db, "proj/c.py", rev.id)


# ---- chief: plan nodes produce real artifacts; missions run once ---------
def test_chief_design_and_report_nodes_produce_files(dbx):
    db, root = dbx
    from app.chief import service as chief
    m = chief.create_mission(db, "给用户管理系统做系统设计，开发实现后测试验证并汇报")
    assert m.status == "completed", m.report
    names = {n["name"]: n["status"] for n in m.report["nodes"]}
    assert names["系统设计"] == "completed"
    assert names["成果汇总汇报"] == "completed"
    assert os.path.isfile(os.path.join(root, "projects/user-mgmt/DESIGN.md"))
    assert os.path.isfile(os.path.join(root, "projects/user-mgmt/REPORT.md"))


def test_chief_mission_cannot_run_twice(dbx):
    db, _root = dbx
    from app.chief import service as chief
    m = chief.create_mission(db, "开发一个用户管理系统，完成后测试验证并汇报")
    assert m.status == "completed"
    nodes = db.query(models.TaskNode).filter_by(task_id=m.task_id).all()
    with pytest.raises(ValueError):
        chief.run_mission(db, m, nodes, [])


def test_handoff_rejects_missing_artifact(dbx):
    db, _root = dbx
    from app.chief import service as chief
    dev = mkagent(db, "dev")
    tester = mkagent(db, "tester")
    msg = chief.create_handoff(db, None, dev.id, tester.id, "交接",
                               [], ["proj/never-existed.py"], [], [])
    with pytest.raises(ValueError, match="没有登记记录"):
        chief.accept_handoff(db, msg.message_id)


def test_handoff_rejects_content_change_with_same_version(dbx):
    db, root = dbx
    from app.chief import service as chief
    dev = mkagent(db, "dev")
    tester = mkagent(db, "tester")
    ws.write_file(db, dev.id, "proj/h.py", "v1\n")
    msg = chief.create_handoff(db, None, dev.id, tester.id, "交接",
                               ["写了 h.py"], ["proj/h.py"], [], [])
    assert chief.accept_handoff(db, msg.message_id)["accepted"] is True
    with open(os.path.join(root, "proj", "h.py"), "w") as f:
        f.write("tampered\n")  # version untouched, bytes changed
    with pytest.raises(ValueError, match="内容已变化"):
        chief.accept_handoff(db, msg.message_id)


# ---- workflows: a preset approved=true in the graph is not consent -------
def test_workflow_graph_preset_approval_does_not_bypass_gate(dbx):
    db, _root = dbx
    from app.workflows import engine
    mkagent(db, "执行者")
    graph = {"nodes": [
        {"id": "1", "data": {"label": "开始", "kind": "start"}},
        {"id": "2", "data": {"label": "人工审批", "kind": "humanapproval",
                             "approved": True}},  # preset in the saved graph
        {"id": "3", "data": {"label": "结束", "kind": "end"}}],
        "edges": [{"id": "e1", "source": "1", "target": "2"},
                  {"id": "e2", "source": "2", "target": "3"}]}
    wf = models.Workflow(name="带预置审批", graph=graph)
    db.add(wf)
    db.commit()
    run = engine.execute_workflow(db, wf.id)
    assert run.status == "failed"  # gate held: nobody approved THIS run
    node = db.query(models.TaskNode).filter_by(task_id=run.task_id).first()
    assert node.status == "failed" and node.error_type == "APPROVAL_REQUIRED"
    # A real per-run decision still passes the gate.
    run2 = engine.execute_workflow(db, wf.id,
                                   approvals={"2": {"approved": True}})
    assert run2.status == "completed"


# ---- tasks API: terminal states cannot be "executed" ----------------------
def test_execute_terminal_task_is_409(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.api.routes import router as api_router
    from app.database import get_db
    engine_ = create_engine("sqlite:///" + str(tmp_path / "api.db"),
                            connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine_)
    factory = sessionmaker(bind=engine_, autocommit=False, autoflush=False)

    def override_get_db():
        s = factory()
        try:
            yield s
        finally:
            s.close()

    app_ = FastAPI()
    app_.include_router(api_router)
    app_.dependency_overrides[get_db] = override_get_db
    client = TestClient(app_, raise_server_exceptions=False)
    db = factory()
    done = models.Task(title="已完成", status="completed")
    gone = models.Task(title="已取消", status="cancelled")
    db.add_all([done, gone])
    db.commit()
    ids = (done.id, gone.id)
    db.close()
    assert client.post(f"/api/tasks/{ids[0]}/execute").status_code == 409
    assert client.post(f"/api/tasks/{ids[1]}/execute").status_code == 409


# ---- runner: a finished node cannot be re-executed ------------------------
def test_finished_node_cannot_be_reexecuted(dbx):
    db, _root = dbx
    from app.execution import runner
    t = models.Task(title="t", status="running")
    db.add(t)
    db.commit()
    n = models.TaskNode(task_id=t.id, name="执行", node_type="llm",
                        status="completed", output_data="原始产出")
    db.add(n)
    db.commit()
    ok, et, _msg = runner._run_node(t.id, n.id)
    assert ok is False and et == "STATE_ERROR"
    db.refresh(n)
    assert n.status == "completed" and n.output_data == "原始产出"
