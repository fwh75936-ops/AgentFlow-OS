#!/usr/bin/env python3
"""AgentFlow OS V3.0 demo — REAL end-to-end run (no LLM, no fake data).

Narrative: chief assembles PM/dev/tester on the ONE shared Linux env ->
SACAS assigns work -> dev writes user_api v1 WITH the permission bug ->
tester verifies (pytest really fails) -> proactive L2 suggestion from the
test_failed event is approved -> dev fixes v2 -> the v1 verification goes
STALE -> tester re-verifies v2 -> current -> chief report.

Run: services/agent-core/.venv/bin/python examples/v3-demo/run_v3_demo.py
Evidence -> examples/v3-demo/evidence/ ; exit 0 only if every step is real.
"""
import json, os, shutil, subprocess, sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
CORE = os.path.abspath(os.path.join(HERE, "..", "..", "services", "agent-core"))
sys.path.insert(0, CORE)
EVIDENCE = os.path.join(HERE, "evidence"); SHARED = os.path.join(HERE, "shared_workspace")
DB_PATH = os.path.join(HERE, "demo.db")
shutil.rmtree(SHARED, ignore_errors=True); os.makedirs(SHARED, exist_ok=True)
os.makedirs(EVIDENCE, exist_ok=True)
if os.path.exists(DB_PATH): os.remove(DB_PATH)
os.environ["AGENTFLOW_SHARED_ROOT"] = SHARED

from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from app import models  # noqa: E402
from app.database import Base  # noqa: E402
from app.chief import service as chief  # noqa: E402
from app.proactive import service as pro  # noqa: E402
from app.resources import service as res  # noqa: E402
from app.scheduler.engine import select_agent  # noqa: E402
from app.verification import state as vs  # noqa: E402
from app.workspace import service as ws  # noqa: E402

LOG = []
def log(step, msg):
    line = f"[{datetime.now().isoformat(timespec='seconds')}] [{step}] {msg}"
    print(line, flush=True); LOG.append(line)
def save(name, obj):
    with open(os.path.join(EVIDENCE, name), "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2, default=str)

engine = create_engine("sqlite:///" + DB_PATH, connect_args={"check_same_thread": False})
Base.metadata.create_all(bind=engine)
db = sessionmaker(bind=engine, autocommit=False, autoflush=False)()

ART = "projects/user-mgmt/user_api.py"; TEST = "projects/user-mgmt/test_user_api.py"
BUGGY = '''USERS = {1: {"id": 1, "name": "Alice", "role": "admin"}, 2: {"id": 2, "name": "Bob", "role": "user"}}
def get_profile(user_id):
    return USERS[user_id]
def admin_stats(requesting_user):
    # BUG: missing role check — any user can read admin stats.
    return {"total_users": len(USERS)}
'''
FIXED = '''USERS = {1: {"id": 1, "name": "Alice", "role": "admin"}, 2: {"id": 2, "name": "Bob", "role": "user"}}
def get_profile(user_id):
    return USERS[user_id]
def admin_stats(requesting_user):
    if requesting_user.get("role") != "admin":
        raise PermissionError("admin only")
    return {"total_users": len(USERS)}
'''
TESTS = '''import os, sys
sys.path.insert(0, os.path.dirname(__file__))
from user_api import get_profile, admin_stats
def test_profile():
    assert get_profile(1)["name"] == "Alice"
def test_admin_stats_rejects_non_admin():
    try:
        admin_stats({"id": 2, "role": "user"})
    except PermissionError:
        return
    raise AssertionError("non-admin must be rejected")
'''

def run_pytest():
    target = os.path.join(SHARED, "projects", "user-mgmt")
    p = subprocess.run([sys.executable, "-m", "pytest", "-q"], cwd=target,
                       capture_output=True, text=True, timeout=120)
    return p.returncode, (p.stdout + p.stderr)[-1500:]

summary = {"llm_called": False, "planner": "rule-based-keyword-v1 (no LLM)",
           "runtime": "local-process", "env_id": "shared-linux-1", "steps": {}}
try:
    chief_agent, team, members = chief.ensure_team(db)
    pm, dev, tester = members
    log("team", f"chief={chief_agent.id} (tools={chief_agent.tools}) team={team.id} pm={pm.id} dev={dev.id} tester={tester.id}")
    assert "linux_exec" not in (chief_agent.tools or [])
    mission = chief.create_mission(db, "开发一个用户管理系统，完成后测试验证并汇报", run=False)
    plan = mission.plan
    log("plan", f"mission={mission.id} nodes={[n['name'] for n in plan['nodes']]}")
    summary["steps"]["team_and_plan"] = {"chief_id": chief_agent.id, "team_id": team.id, "plan": plan}

    # SACAS with a real resource lease: dev holds the git lease for the repo.
    lease, _ = res.acquire(db, "git", "repo:user-mgmt", dev.id, task_id=mission.task_id)
    risk = {a.id: res.conflict_risk(db, a.id, "git", "repo:user-mgmt") for a in (dev, tester)}
    decision = select_agent([dev, tester], ["coding"], ["linux_file_write"], mode="sacas",
                            resource_state=risk)
    db.add(models.SchedulerDecision(task_id=mission.task_id, node_id=None, mode="sacas",
           candidates=decision.candidates, selected_agent_id=decision.selected.id, reason=decision.reason))
    db.commit()
    log("sacas", decision.reason)
    assert decision.selected.id == dev.id
    summary["steps"]["sacas"] = {"reason": decision.reason, "candidates": decision.candidates,
                                 "git_lease_id": lease.id, "conflict_risk": risk}
    res.release(db, "git", "repo:user-mgmt", dev.id)

    ws.write_file(db, pm.id, "projects/user-mgmt/SPEC.md",
                  "# 规格\n- get_profile(user_id)\n- admin_stats 仅管理员可调用\n", task_id=mission.task_id)
    ws.write_file(db, dev.id, ART, BUGGY, task_id=mission.task_id)
    ws.write_file(db, dev.id, TEST, TESTS, task_id=mission.task_id)
    meta1 = db.query(models.SharedFileMeta).get(ART)
    log("dev-v1", f"wrote {ART} v{meta1.version} sha={meta1.content_hash[:12]}")

    code1, out1 = run_pytest()
    log("test-v1", f"pytest exit={code1}")
    assert code1 != 0, "buggy v1 must fail the permission test"
    rec1 = vs.record_verification(db, ART, False, test_run_id="v3demo-pytest-1",
                                  task_id=mission.task_id, evidence=out1[-400:])
    ws.emit_event(db, "test_failed", agent_id=tester.id, task_id=mission.task_id, path=ART,
                  detail="pytest exit=1: test_admin_stats_rejects_non_admin failed (permission bug)")
    summary["steps"]["v1_test"] = {"pytest_exit": code1, "verification_id": rec1.id,
                                   "artifact_version": rec1.artifact_version, "passed": False}

    created = pro.scan_events(db)
    sug = [s for s in created if s.event_type == "test_failed" and s.level == "L2"]
    assert sug, "expected an L2 proactive suggestion"
    try:
        pro.execute_suggestion(db, sug[0].id); raise SystemExit("execution before approval must be blocked")
    except PermissionError:
        log("proactive", f"suggestion #{sug[0].id} execution blocked before approval (correct)")
    pro.approve(db, sug[0].id); executed = pro.execute_suggestion(db, sug[0].id)
    log("proactive", f"suggestion #{sug[0].id} approved -> {executed['status']}")
    summary["steps"]["proactive"] = {"suggestion_id": sug[0].id, "level": sug[0].level,
                                     "blocked_before_approval": True, "after_approval": executed["status"]}

    handoff = chief.create_handoff(db, mission.task_id, tester.id, dev.id,
        "修复 admin_stats 权限校验 Bug", ["v1 测试失败并定位权限 Bug"], [ART], ["权限 Bug 未修复"], ["pytest 全部通过"])
    log("handoff", f"handoff {handoff.message_id} envVersion={handoff.meta['environmentVersion']}")

    ws.write_file(db, dev.id, ART, FIXED, task_id=mission.task_id)
    meta2 = db.query(models.SharedFileMeta).get(ART)
    log("dev-v2", f"wrote {ART} v{meta2.version} sha={meta2.content_hash[:12]}")
    flipped = vs.check_freshness(db, ART)
    db.refresh(rec1)
    log("stale", f"v1 verification status={rec1.status} after v2 write (flipped={flipped})")
    assert rec1.status == "stale" and not vs.is_verified(db, ART)
    summary["steps"]["stale"] = {"flipped_to_stale": flipped, "v1_verification_status": rec1.status,
                                 "v2_reported_verified_before_reverify": False}

    code2, out2 = run_pytest()
    log("test-v2", f"pytest exit={code2}")
    assert code2 == 0
    rec2 = vs.record_verification(db, ART, True, test_run_id="v3demo-pytest-2",
                                  task_id=mission.task_id, evidence=out2[-400:])
    assert vs.is_verified(db, ART)
    summary["steps"]["v2_test"] = {"pytest_exit": code2, "verification_id": rec2.id,
                                   "artifact_version": rec2.artifact_version, "status": rec2.status}

    mission.status = "completed"; db.commit()
    report = chief.build_report(db, mission, evidence=LOG)
    report["proactive"] = summary["steps"]["proactive"]; report["sacas"] = summary["steps"]["sacas"]
    mission.report = report; db.commit()
    save("chief_report.json", report); save("summary.json", {**summary, "report_status": report["status"]})
    save("execution_log.json", LOG)
    log("report", f"mission report status={report['status']} verified_current={report['artifacts'][ART]['verified_current']}")
    print("V3 DEMO OK"); sys.exit(0)
except SystemExit:
    raise
except Exception as e:
    summary["error"] = str(e); save("summary.json", summary); save("execution_log.json", LOG)
    print(f"V3 DEMO FAILED: {e}"); sys.exit(1)
