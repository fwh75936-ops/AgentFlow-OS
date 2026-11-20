#!/usr/bin/env python3
"""End-to-end three-employee demo on the Shared Linux Workspace (round-2 acceptance).

Narrative (every step goes through real code paths — workspace service,
Tool Registry permission check, real bash subprocesses, real pytest):

  1. Create 产品经理 / 开发工程师 / 测试工程师. The PM intentionally has NO
     linux_exec tool; the Tool Registry must refuse its exec attempt.
  2. All three open independent terminal sessions in the SAME shared Linux
     environment (env_id=shared-linux-1); session cwds are independent.
  3. 开发工程师 creates a real Python project `user_api` in the shared
     filesystem, with a deliberately injected bug: the admin endpoint is
     missing its role check, so a regular user can read admin stats.
  4. 测试工程师 reads the same shared files in its own session and runs
     pytest -> the permission test really fails. The failure is reported to
     开发工程师 through a real AgentMessage row (ERROR_REPORT).
  5. Self-Loop (existing engine, unmodified) drives verify -> analyze ->
     repair -> reverify; the repair step is 开发工程师 rewriting the file
     with the role check (the fix content is scripted by this harness —
     NO LLM is involved anywhere in this demo, there is no API key).
  6. 测试工程师 re-runs pytest in its session -> all green, and a
     VerificationRecord + test report file are persisted.
  7. 产品经理 (read-only tools) views the final artifacts; SHA-256 hashes
     are recorded as evidence.
  Cross-cutting: the shared desktop lease is acquired / queued / released
  with no conflict, and every command lands in the audit log with
  executor / task / exit code.

Run it (from services/agent-core, using the project venv):

    ./.venv/bin/python ../../examples/e2e-demo/run_e2e_demo.py

Outputs land in examples/e2e-demo/evidence/ (overwritten each run, and the
demo DB + shared workspace are recreated from scratch, so re-runs are
reproducible). Exit code 0 only if every step really succeeded.
"""
import hashlib
import json
import os
import shutil
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
CORE = os.path.abspath(os.path.join(HERE, "..", "..", "services", "agent-core"))
sys.path.insert(0, CORE)

EVIDENCE = os.path.join(HERE, "evidence")
SHARED = os.path.join(HERE, "shared_workspace")
DB_PATH = os.path.join(HERE, "demo.db")

# Clean slate for reproducibility.
shutil.rmtree(SHARED, ignore_errors=True)
os.makedirs(SHARED, exist_ok=True)
os.makedirs(EVIDENCE, exist_ok=True)
if os.path.exists(DB_PATH):
    os.remove(DB_PATH)
os.environ["AGENTFLOW_SHARED_ROOT"] = SHARED

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app import models  # noqa: E402
from app.database import Base  # noqa: E402
from app.scheduler.engine import select_agent  # noqa: E402
from app.self_loop.engine import SelfLoop  # noqa: E402
from app.tools.registry import ToolError, execute_tool  # noqa: E402
from app.verification.engine import tool_verify_pytest  # noqa: E402
from app.workspace import config, service  # noqa: E402

LOG_LINES = []


def log(step, msg):
    line = f"[{datetime.now().isoformat(timespec='seconds')}] [{step}] {msg}"
    print(line, flush=True)
    LOG_LINES.append(line)


def save(name, content):
    with open(os.path.join(EVIDENCE, name), "w", encoding="utf-8") as f:
        f.write(content)


BUGGY = '''"""Tiny user-management API core (demo project, stdlib only)."""
USERS = {
    1: {"id": 1, "name": "Alice", "role": "admin"},
    2: {"id": 2, "name": "Bob", "role": "user"},
}


def get_profile(user_id):
    return USERS[user_id]


def admin_stats(requesting_user):
    # BUG (injected for the demo): the role check is missing, so ANY
    # authenticated user can read the admin statistics.
    return {"total_users": len(USERS),
            "admins": sum(1 for u in USERS.values() if u["role"] == "admin")}
'''

FIXED = '''"""Tiny user-management API core (demo project, stdlib only)."""
USERS = {
    1: {"id": 1, "name": "Alice", "role": "admin"},
    2: {"id": 2, "name": "Bob", "role": "user"},
}


def get_profile(user_id):
    return USERS[user_id]


def admin_stats(requesting_user):
    # FIX: only admins may read the admin statistics.
    if requesting_user.get("role") != "admin":
        raise PermissionError("admin role required")
    return {"total_users": len(USERS),
            "admins": sum(1 for u in USERS.values() if u["role"] == "admin")}
'''

TESTS = '''import pytest

from user_api import USERS, admin_stats, get_profile


def test_regular_user_cannot_access_admin_stats():
    with pytest.raises(PermissionError):
        admin_stats(USERS[2])  # Bob is a regular user


def test_admin_can_access_admin_stats():
    stats = admin_stats(USERS[1])  # Alice is an admin
    assert stats["total_users"] == 2
    assert stats["admins"] == 1


def test_get_profile_returns_user():
    assert get_profile(2)["name"] == "Bob"
'''

PM_TOOLS = ["linux_file_read", "linux_file_list"]  # NOTE: no linux_exec, by design
FULL_TOOLS = ["linux_exec", "linux_file_read", "linux_file_write",
              "linux_file_list", "desktop_control", "run_pytest"]


def main():
    engine = create_engine("sqlite:///" + DB_PATH,
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    db = sessionmaker(bind=engine, autocommit=False, autoflush=False)()

    # ---- Step 1: three employees with different permissions -----------------
    pm = models.Agent(name="产品经理", role="product_manager", tools=PM_TOOLS,
                      capability_tags=["planning", "review"],
                      capability_scores={"planning": 0.9, "review": 0.85},
                      status="active")
    dev = models.Agent(name="开发工程师", role="developer", tools=FULL_TOOLS,
                       capability_tags=["coding", "general"],
                       capability_scores={"coding": 0.95, "general": 0.8},
                       status="active")
    tester = models.Agent(name="测试工程师", role="tester", tools=FULL_TOOLS,
                          capability_tags=["review", "research"],
                          capability_scores={"review": 0.92, "research": 0.8},
                          status="active")
    db.add_all([pm, dev, tester])
    db.commit()
    for a in (pm, dev, tester):
        db.refresh(a)
    team = models.Team(name="用户管理 API 演示团队", leader_agent_id=pm.id)
    db.add(team)
    db.commit()
    db.refresh(team)
    task = models.Task(title="开发 user_api 并通过权限测试", team_id=team.id,
                       scheduler_mode="adaptive", status="running")
    db.add(task)
    db.commit()
    db.refresh(task)
    log("1-agents", f"created pm#{pm.id} dev#{dev.id} tester#{tester.id} "
                    f"team#{team.id} task#{task.id}; pm tools={PM_TOOLS} (no linux_exec)")

    # Scheduler (existing engine) assigns coding->dev, review->tester.
    d_code = select_agent([pm, dev, tester], ["coding"], [], mode="adaptive")
    d_review = select_agent([pm, dev, tester], ["review"], [], mode="adaptive")
    assert d_code.selected.id == dev.id, d_code.reason
    assert d_review.selected.id == tester.id, d_review.reason
    for d, chosen in ((d_code, dev), (d_review, tester)):
        db.add(models.SchedulerDecision(task_id=task.id, mode="adaptive",
                                        candidates=d.candidates,
                                        selected_agent_id=chosen.id, reason=d.reason))
    db.commit()
    log("1-scheduler", f"adaptive: coding->{dev.name} ({d_code.reason}); "
                       f"review->{tester.name} ({d_review.reason})")

    # PM tries to execute a command: Tool Registry must refuse (no linux_exec).
    try:
        execute_tool("linux_exec", {"agent_id": pm.id, "command": "ls",
                                    "task_id": task.id},
                     pm.tools, approved=True)
        raise AssertionError("PM exec was NOT refused — permission check broken")
    except ToolError as e:
        assert e.error_type == "PERMISSION_ERROR", e.error_type
        log("1-pm-permission", f"PM linux_exec refused by Tool Registry: "
                               f"{e.error_type}: {e}")
        pm_denied = f"{e.error_type}: {e}"

    # ---- Step 2: one shared Linux env, three independent sessions -----------
    s_pm = service.create_session(db, pm.id, task_id=task.id)
    s_dev = service.create_session(db, dev.id, task_id=task.id)
    s_tester = service.create_session(db, tester.id, task_id=task.id)
    assert {s_pm.env_id, s_dev.env_id, s_tester.env_id} == {config.ENV_ID}
    log("2-sessions", f"all sessions on env {config.ENV_ID}: "
                      f"pm session#{s_pm.id}, dev session#{s_dev.id}, "
                      f"tester session#{s_tester.id}")

    # ---- Step 3: dev creates the project (with injected bug) ----------------
    w1 = service.write_file(db, dev.id, "user_api/user_api.py", BUGGY, task_id=task.id)
    w2 = service.write_file(db, dev.id, "user_api/test_user_api.py", TESTS, task_id=task.id)
    r = service.exec_in_session(db, s_dev, "ls -la user_api", task_id=task.id)
    assert r["exit_code"] == 0 and "user_api.py" in r["stdout"]
    log("3-dev-create", f"wrote user_api/user_api.py v{w1['version']} "
                        f"sha256={w1['content_hash'][:12]}…, "
                        f"user_api/test_user_api.py v{w2['version']}; "
                        f"`ls -la user_api` rc=0")
    save("dev_ls_output.txt", r["stdout"])

    # ---- Step 4: tester runs the tests in its own session -> real failure ---
    seen = service.read_file(db, "user_api/user_api.py")
    assert "BUG (injected for the demo)" in seen["content"]
    service.exec_in_session(db, s_tester, "cd user_api", task_id=task.id)
    first = service.exec_in_session(db, s_tester, f"{sys.executable} -m pytest -q",
                                    task_id=task.id)
    save("pytest_before_fix.txt",
         f"$ (tester session#{s_tester.id}, cwd=user_api) {sys.executable} -m pytest -q\n"
         f"exit_code={first['exit_code']}\n--- stdout ---\n{first['stdout']}\n"
         f"--- stderr ---\n{first['stderr']}\n")
    assert first["exit_code"] not in (0, None), "tests must really fail before the fix"
    log("4-tester-fail", f"tester pytest exit_code={first['exit_code']} (real failure); "
                         f"tester cwd={s_tester.cwd!r}, dev cwd={s_dev.cwd!r} (isolated)")

    # ---- Step 5: Self-Loop verify -> repair (notify + fix) -> reverify ------
    proj_dir = os.path.join(SHARED, "user_api")
    state = {"notified": False}

    def repair(errors, attempt):
        # Tester notifies dev through a real AgentMessage row.
        db.add(models.AgentMessage(
            team_id=team.id, task_id=task.id, sender_agent_id=tester.id,
            receiver_agent_id=dev.id, message_type="ERROR_REPORT",
            content=(f"user_api 权限测试失败: {'; '.join(errors)}; "
                     f"普通用户可访问 admin_stats，缺少角色校验"), meta={}))
        db.commit()
        state["notified"] = True
        # Dev applies the fix in the shared filesystem (scripted, no LLM).
        service.write_file(db, dev.id, "user_api/user_api.py", FIXED, task_id=task.id)
        log("5-repair", f"attempt {attempt}: tester->dev ERROR_REPORT saved; "
                        f"dev rewrote user_api.py with role check")

    loop = SelfLoop(max_retries=2, max_seconds=300)
    final, _out, history = loop.run(
        lambda attempt: tool_verify_pytest(proj_dir).evidence,
        lambda _output: tool_verify_pytest(proj_dir),
        repair)
    save("self_loop_history.json", json.dumps(history, ensure_ascii=False, indent=2))
    assert final == "COMPLETE", history
    assert state["notified"]
    msgs = db.query(models.AgentMessage).filter_by(
        task_id=task.id, message_type="ERROR_REPORT").all()
    assert msgs and msgs[0].receiver_agent_id == dev.id
    log("5-self-loop", f"Self-Loop final={final}, history states="
                       f"{[h['state'] for h in history]}; ERROR_REPORT rows={len(msgs)}")

    # ---- Step 6: tester re-runs -> green; verification record persisted -----
    passed = tool_verify_pytest(proj_dir)
    db.add(models.VerificationRecord(task_id=task.id, verification_type="tool",
                                     passed=passed.passed, score=passed.score,
                                     evidence=passed.evidence, errors=passed.errors))
    db.commit()
    assert passed.passed
    again = service.exec_in_session(db, s_tester, f"{sys.executable} -m pytest -q",
                                    task_id=task.id)
    save("pytest_after_fix.txt",
         f"$ (tester session#{s_tester.id}, cwd=user_api) {sys.executable} -m pytest -q\n"
         f"exit_code={again['exit_code']}\n--- stdout ---\n{again['stdout']}\n"
         f"--- stderr ---\n{again['stderr']}\n")
    assert again["exit_code"] == 0
    summary_line = [ln for ln in again["stdout"].splitlines() if "passed" in ln]
    report_text = (f"user_api test report (tester session#{s_tester.id})\n"
                   f"command: {sys.executable} -m pytest -q (cwd=user_api)\n"
                   f"exit_code: 0\nresult: {summary_line[-1].strip() if summary_line else 'passed'}\n")
    service.write_file(db, tester.id, "user_api/test-report.txt", report_text,
                       task_id=task.id)
    log("6-tester-pass", f"tester re-run exit_code=0; VerificationRecord saved "
                         f"(passed={passed.passed}, score={passed.score}); "
                         f"test-report.txt written")

    # ---- Cross-cutting: desktop lease, no conflict --------------------------
    l1 = service.acquire_desktop_lease(db, tester.id, ttl_seconds=30)
    l2 = service.acquire_desktop_lease(db, dev.id, ttl_seconds=30)
    assert l1["state"] == "held" and l2["state"] == "waiting", (l1, l2)
    service.release_desktop_lease(db, agent_id=tester.id)
    cur = service.current_lease(db)
    assert cur["holder_agent_id"] == dev.id, cur
    service.release_desktop_lease(db, agent_id=dev.id)
    log("x-desktop-lease", f"tester held lease#{l1['lease_id']}; dev queued "
                           f"(position {l2['position']}); tester released -> "
                           f"dev granted; dev released. No conflict.")

    # ---- Step 7: PM views final artifacts (read-only) -----------------------
    final_code = service.read_file(db, "user_api/user_api.py")
    assert 'requesting_user.get("role") != "admin"' in final_code["content"]
    report = service.read_file(db, "user_api/test-report.txt")
    assert "exit_code: 0" in report["content"]
    files = service.list_files(db)
    artifacts = []
    for f in files:
        if f["is_dir"] or ".pytest_cache" in f["path"]:
            continue
        full = os.path.join(SHARED, f["path"])
        with open(full, "rb") as fh:
            digest = hashlib.sha256(fh.read()).hexdigest()
        artifacts.append({"path": f["path"], "size": f["size"],
                          "version": f["version"], "sha256": digest,
                          "last_modified_by_agent_id": f["last_modified_by_agent_id"]})
    save("final_artifacts.json", json.dumps(
        {"viewed_by": f"{pm.name} (agent#{pm.id}, read-only tools)",
         "artifacts": artifacts}, ensure_ascii=False, indent=2))
    log("7-pm-view", f"PM read user_api.py v{final_code['version']} "
                     f"sha256={final_code['content_hash'][:12]}… and test-report.txt; "
                     f"{len(artifacts)} artifact files listed with hashes")

    # ---- Cross-cutting: audit log completeness ------------------------------
    logs = service.list_command_logs(db, task_id=task.id, limit=200)
    assert logs, "no audit logs"
    for row in logs:
        assert row.agent_id is not None and row.started_at is not None
        assert row.status in ("completed", "failed", "blocked", "timeout")
        if row.status in ("completed", "failed"):
            assert row.exit_code is not None
    names = {a.id: a.name for a in (pm, dev, tester)}
    save("audit_log.json", json.dumps([
        {"log_id": r.id, "agent": names.get(r.agent_id), "agent_id": r.agent_id,
         "session_id": r.session_id, "task_id": r.task_id, "command": r.command,
         "cwd": r.cwd, "exit_code": r.exit_code, "status": r.status,
         "started_at": r.started_at.isoformat() if r.started_at else None,
         "duration_ms": r.duration_ms}
        for r in reversed(logs)], ensure_ascii=False, indent=2))
    events = service.list_events(db, limit=200)
    save("workspace_events.json", json.dumps([
        {"id": e.id, "type": e.event_type, "agent_id": e.agent_id,
         "path": e.path, "detail": e.detail,
         "at": e.created_at.isoformat() if e.created_at else None}
        for e in reversed(events)], ensure_ascii=False, indent=2))
    etypes = {e.event_type for e in events}
    for expected in ("session_created", "file_created", "command_executed",
                     "desktop_lease_acquired", "desktop_lease_released"):
        assert expected in etypes, expected
    log("x-audit", f"{len(logs)} command audit rows (executor/task/exit_code all "
                   f"present); {len(events)} workspace events incl. "
                   f"{sorted(etype for etype in etypes if 'desktop' in etype)}")

    task.status = "completed"
    db.commit()
    save("summary.json", json.dumps({
        "env_id": config.ENV_ID, "runtime_kind": config.runtime_kind(),
        "task_id": task.id, "team_id": team.id,
        "agents": {a.name: a.id for a in (pm, dev, tester)},
        "sessions": {pm.name: s_pm.id, dev.name: s_dev.id, tester.name: s_tester.id},
        "pm_permission_denied": pm_denied,
        "pytest_before_fix_exit_code": first["exit_code"],
        "pytest_after_fix_exit_code": again["exit_code"],
        "self_loop_final": final,
        "audit_rows": len(logs), "event_rows": len(events),
        "llm_called": False,
        "note": "No LLM was called: no model provider/API key is configured. "
                "Employee decisions in this harness are scripted; every "
                "command, file, test, message and lease is real.",
    }, ensure_ascii=False, indent=2))
    save("execution_log.txt", "\n".join(LOG_LINES) + "\n")
    log("done", "E2E demo COMPLETE — all steps verified against real state")
    save("execution_log.txt", "\n".join(LOG_LINES) + "\n")
    db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
