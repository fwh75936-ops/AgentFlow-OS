"""Round-2 acceptance: three agents share ONE Linux computer.

产品经理 / 开发工程师 / 测试工程师 — same env, same filesystem.
开发 creates a Python project -> 测试 reads it and runs the tests (fail) ->
测试 notifies 开发 (real AgentMessage row) -> Self-Loop drives repair ->
开发 fixes -> 测试 re-verifies (pass) -> 产品经理 views the final artifacts.

Reuses the existing engines without modification: Scheduler (role assignment),
Verification Engine (real pytest tool verification), Self-Loop (repair cycle).
The developer's fix is scripted by the test harness (no LLM is available in
CI); every execution, file and message in between is real.
"""
import os
import sys

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models
from app.database import Base
from app.scheduler.engine import select_agent
from app.self_loop.engine import SelfLoop
from app.verification.engine import tool_verify_pytest
from app.workspace import config, service

TOOLS = ["linux_exec", "linux_file_read", "linux_file_write", "linux_file_list",
         "desktop_control", "run_pytest"]

BUGGY = "def add(a, b):\n    return a - b  # BUG: should be a + b\n"
FIXED = "def add(a, b):\n    return a + b\n"
TESTS = ("from calc import add\n\n"
         "def test_add_positive():\n    assert add(2, 3) == 5\n\n"
         "def test_add_negative():\n    assert add(-1, -2) == -3\n")


@pytest.fixture()
def ws(tmp_path, monkeypatch):
    root = tmp_path / "shared"
    root.mkdir()
    monkeypatch.setenv("AGENTFLOW_SHARED_ROOT", str(root))
    engine = create_engine("sqlite:///" + str(tmp_path / "acc.db"),
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    db = sessionmaker(bind=engine, autocommit=False, autoflush=False)()
    yield db, str(root)
    db.close()


def _agent(db, name, role, caps, scores):
    a = models.Agent(name=name, role=role, tools=TOOLS, capability_tags=caps,
                     capability_scores=scores, status="active")
    db.add(a)
    db.commit()
    db.refresh(a)
    return a


def test_three_agents_one_linux_computer(ws):
    db, root = ws
    pm = _agent(db, "产品经理", "product_manager", ["planning", "review"],
                {"planning": 0.9, "review": 0.85})
    dev = _agent(db, "开发工程师", "developer", ["coding", "general"],
                 {"coding": 0.95, "general": 0.8})
    tester = _agent(db, "测试工程师", "tester", ["review", "research"],
                    {"review": 0.92, "research": 0.8})
    team = models.Team(name="验收团队", leader_agent_id=pm.id)
    db.add(team)
    db.commit()
    task = models.Task(title="开发 calc 项目并通过测试", team_id=team.id,
                       scheduler_mode="adaptive", status="running")
    db.add(task)
    db.commit()
    db.refresh(task)

    # --- Existing Scheduler assigns the work (hard filter + adaptive score) ---
    d_code = select_agent([pm, dev, tester], ["coding"], [], mode="adaptive")
    assert d_code.selected.id == dev.id
    d_review = select_agent([pm, dev, tester], ["review"], [], mode="adaptive")
    assert d_review.selected.id == tester.id
    db.add(models.SchedulerDecision(task_id=task.id, mode="adaptive",
                                    candidates=d_code.candidates,
                                    selected_agent_id=dev.id, reason=d_code.reason))
    db.commit()

    # --- All three connect to the SAME Linux environment ---
    s_pm = service.create_session(db, pm.id, task_id=task.id)
    s_dev = service.create_session(db, dev.id, task_id=task.id)
    s_tester = service.create_session(db, tester.id, task_id=task.id)
    assert {s_pm.env_id, s_dev.env_id, s_tester.env_id} == {config.ENV_ID}

    # --- 开发工程师 creates the Python project in the shared filesystem ---
    service.write_file(db, dev.id, "calc/calc.py", BUGGY, task_id=task.id)
    service.write_file(db, dev.id, "calc/test_calc.py", TESTS, task_id=task.id)
    r = service.exec_in_session(db, s_dev, "ls calc", task_id=task.id)
    assert r["exit_code"] == 0 and "calc.py" in r["stdout"]
    assert os.path.isfile(os.path.join(root, "calc", "calc.py"))  # really on disk

    # --- 测试工程师 reads the project from the same filesystem and tests it ---
    seen = service.read_file(db, "calc/calc.py")
    assert "return a - b" in seen["content"]
    proj_dir = os.path.join(root, "calc")
    service.exec_in_session(db, s_tester, "cd calc", task_id=task.id)
    first = service.exec_in_session(db, s_tester, f"{sys.executable} -m pytest -q",
                                    task_id=task.id)
    assert first["exit_code"] not in (0, None)  # tests really fail
    fail_log = [l for l in service.list_command_logs(db, session_id=s_tester.id)
                if "pytest" in l.command][0]
    assert fail_log.agent_id == tester.id and fail_log.exit_code not in (0, None)

    # --- Failure -> notify 开发 -> Self-Loop repair -> re-verify ---
    state = {"repairs": 0, "notified": False}

    def execute(attempt):
        res = tool_verify_pytest(proj_dir)
        return res.evidence

    def verify(_output):
        return tool_verify_pytest(proj_dir)

    def repair(errors, attempt):
        state["repairs"] += 1
        # 测试工程师 notifies 开发工程师 with the real failure evidence.
        db.add(models.AgentMessage(
            team_id=team.id, task_id=task.id, sender_agent_id=tester.id,
            receiver_agent_id=dev.id, message_type="ERROR_REPORT",
            content=f"calc 测试失败: {'; '.join(errors)}", meta={}))
        db.commit()
        state["notified"] = True
        # 开发工程师 fixes the code in the shared filesystem.
        service.write_file(db, dev.id, "calc/calc.py", FIXED, task_id=task.id)

    loop = SelfLoop(max_retries=2, max_seconds=300)
    final, _out, history = loop.run(execute, verify, repair)
    assert final == "COMPLETE", history
    assert state["repairs"] >= 1 and state["notified"]
    msgs = db.query(models.AgentMessage).filter_by(task_id=task.id,
                                                   message_type="ERROR_REPORT").all()
    assert msgs and msgs[0].receiver_agent_id == dev.id

    # Record the verification outcome in the existing VerificationRecord table.
    passed = tool_verify_pytest(proj_dir)
    db.add(models.VerificationRecord(task_id=task.id, verification_type="tool",
                                     passed=passed.passed, score=passed.score,
                                     evidence=passed.evidence, errors=passed.errors))
    db.commit()
    assert passed.passed

    # 测试工程师 re-runs in the terminal: now green, and writes the report.
    again = service.exec_in_session(db, s_tester, f"{sys.executable} -m pytest -q",
                                    task_id=task.id)
    assert again["exit_code"] == 0
    service.write_file(db, tester.id, "calc/test-report.txt",
                       "pytest: 2 passed (after fix)\n", task_id=task.id)

    # --- 产品经理 views the final artifacts in the same workspace ---
    final_code = service.read_file(db, "calc/calc.py")
    assert "return a + b" in final_code["content"]
    report = service.read_file(db, "calc/test-report.txt")
    assert "2 passed" in report["content"]
    files = {f["path"] for f in service.list_files(db)}
    assert {"calc/calc.py", "calc/test_calc.py", "calc/test-report.txt"} <= files

    # --- Cross-cutting checks from the spec ---
    # Terminal sessions did not interfere: tester is still in calc/, dev at root.
    assert (s_tester.cwd or "") == "calc"
    assert (s_dev.cwd or "") == ""
    # Desktop control never conflicts.
    assert service.acquire_desktop_lease(db, dev.id, ttl_seconds=30)["state"] == "held"
    assert service.acquire_desktop_lease(db, tester.id, ttl_seconds=30)["state"] == "waiting"
    service.release_desktop_lease(db, agent_id=dev.id)
    assert service.current_lease(db)["holder_agent_id"] == tester.id
    # Collaboration events were emitted for the whole flow.
    types = [e.event_type for e in service.list_events(db, limit=100)]
    for expected in ("session_created", "file_created", "command_executed",
                     "desktop_lease_acquired"):
        assert expected in types, expected
    task.status = "completed"
    db.commit()
