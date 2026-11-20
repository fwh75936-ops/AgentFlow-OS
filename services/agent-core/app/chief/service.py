"""AI Chief of Staff — RULE-BASED planning (no LLM is called; there is no
provider key configured, and this module never pretends otherwise).

The chief holds NO Linux operation tools: it plans, delegates via the
scheduler, and reports. Management rights are separated from operation
rights and enforced by the Tool Registry (see permission test).
"""
import json, subprocess, sys
from datetime import datetime
from app import models
from app.scheduler.engine import refresh_live_loads, select_agent
from app.seed_faces import backfill_seed_face
from app.tools.registry import execute_tool, ToolError  # noqa: F401  (re-exported for tests)
from app.verification import state as verif_state
from app.workspace import service as ws

CHIEF_TOOLS = []  # deliberately empty: no linux_exec / file / desktop tools

# keyword -> (node name, node_type, required capabilities, required tools)
_RULES = [
    ("需求", "需求分析与规格", "plan", ["product"], []),
    ("设计", "系统设计", "plan", ["product"], []),
    ("开发", "开发实现", "agent", ["coding"], ["linux_file_write", "linux_file_read"]),
    ("实现", "开发实现", "agent", ["coding"], ["linux_file_write", "linux_file_read"]),
    ("测试", "测试验证", "verify", ["testing"], ["linux_file_read", "run_pytest"]),
    ("验证", "测试验证", "verify", ["testing"], ["linux_file_read", "run_pytest"]),
    ("修复", "缺陷修复", "agent", ["coding"], ["linux_file_write", "linux_file_read"]),
    ("汇报", "成果汇总汇报", "plan", ["product"], []),
    ("报告", "成果汇总汇报", "plan", ["product"], []),
]
_DEFAULT_PLAN = [("需求分析与规格", "plan", ["product"], []),
                 ("开发实现", "agent", ["coding"], ["linux_file_write", "linux_file_read"]),
                 ("测试验证", "verify", ["testing"], ["linux_file_read", "run_pytest"]),
                 ("成果汇总汇报", "plan", ["product"], [])]

def plan_goal(goal):
    """Deterministic keyword decomposition. Returns ordered node specs with
    sequential dependencies. llm_called is always False."""
    picked, seen = [], set()
    for kw, name, ntype, caps, tools in _RULES:
        if kw in (goal or "") and name not in seen:
            picked.append((name, ntype, caps, tools)); seen.add(name)
    if len(picked) < 2:
        picked = list(_DEFAULT_PLAN)
    # canonical stage order by node NAME semantics: spec -> develop -> verify -> report
    def _stage(name):
        if "汇报" in name or "报告" in name: return 3
        if "测试" in name or "验证" in name: return 2
        if "开发" in name or "实现" in name or "修复" in name: return 1
        return 0
    picked.sort(key=lambda p: _stage(p[0]))
    nodes, prev = [], None
    for name, ntype, caps, tools in picked:
        nodes.append({"name": name, "node_type": ntype, "required_capabilities": caps,
                      "required_tools": tools, "depends_on_index": prev})
        prev = len(nodes) - 1
    return {"goal": goal, "llm_called": False, "planner": "rule-based-keyword-v1", "nodes": nodes}

def ensure_team(db, chief_name="AI总管"):
    """Find or create chief + PM/dev/tester agents and a team led by the chief.
    Capability checks use the real agents table; existing agents are reused."""
    def find_or_create(name, role, caps, scores, tools):
        a = db.query(models.Agent).filter_by(name=name).first()
        if a is None:
            a = models.Agent(name=name, role=role, capability_tags=caps, capability_scores=scores,
                             tools=tools, status="active")
            db.add(a)
            # 种子三人固定形象（新行与旧行回填共用同一份映射）；
            # 总管不在映射里，不动。
            backfill_seed_face(a)
            db.commit(); db.refresh(a)
        elif backfill_seed_face(a):
            # 旧库行缺头像/形象时幂等补齐；已有配置不覆盖。
            db.commit(); db.refresh(a)
        return a
    chief = find_or_create(chief_name, "chief", ["management"], {"management": 0.95}, list(CHIEF_TOOLS))
    pm = find_or_create("产品经理", "product", ["product"], {"product": 0.9},
                        ["linux_file_read", "linux_file_list"])
    dev = find_or_create("开发工程师", "developer", ["coding"], {"coding": 0.92},
                         ["linux_exec", "linux_file_read", "linux_file_write", "linux_file_list", "run_pytest"])
    tester = find_or_create("测试工程师", "tester", ["testing"], {"testing": 0.9},
                            ["linux_exec", "linux_file_read", "linux_file_list", "run_pytest"])
    team = db.query(models.Team).filter_by(name="V3演示团队").first()
    if team is None:
        team = models.Team(name="V3演示团队", description="总管主导的演示团队",
                           leader_agent_id=chief.id)
        db.add(team); db.commit(); db.refresh(team)
        for ag, r, lead in ((chief, "chief", True), (pm, "product", False),
                            (dev, "developer", False), (tester, "tester", False)):
            db.add(models.TeamMember(team_id=team.id, agent_id=ag.id, role_in_team=r, is_leader=lead))
        db.commit()
    elif team.description == "Chief-led demo team (round 3)":
        # 存量行仍是旧英文描述时幂等改中文；自定义描述不覆盖。
        team.description = "总管主导的演示团队"
        db.commit(); db.refresh(team)
    return chief, team, [pm, dev, tester]

APP_CODE = '''"""user_api: tiny user-management core (real demo artifact)."""
USERS = {1: {"id": 1, "name": "Alice", "role": "admin"},
         2: {"id": 2, "name": "Bob", "role": "user"}}

def get_profile(user_id):
    return USERS[user_id]

def admin_stats(requesting_user):
    if requesting_user.get("role") != "admin":
        raise PermissionError("admin only")
    return {"total_users": len(USERS)}
'''
TEST_CODE = '''import sys, os
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

def create_mission(db, goal, run=True):
    chief, team, members = ensure_team(db)
    plan = plan_goal(goal)
    # Task + mission + nodes are created in ONE transaction: a failure
    # midway must not leave a half-built task in the database.
    try:
        task = models.Task(title=goal[:120] or "chief mission", description=goal,
                           team_id=team.id, scheduler_mode="sacas", status="pending")
        db.add(task); db.flush()
        mission = models.ChiefMission(goal=goal, team_id=team.id, task_id=task.id,
                                      chief_agent_id=chief.id, status="planned", plan=plan, llm_called=False)
        db.add(mission); db.flush()
        node_rows, id_by_index = [], {}
        for i, spec in enumerate(plan["nodes"]):
            deps = [id_by_index[spec["depends_on_index"]]] if spec["depends_on_index"] is not None else []
            n = models.TaskNode(task_id=task.id, name=spec["name"], node_type=spec["node_type"],
                                required_capabilities=spec["required_capabilities"],
                                required_tools=spec["required_tools"], dependencies=deps, status="pending")
            db.add(n); db.flush()
            node_rows.append(n); id_by_index[i] = n.id
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(mission)
    if run:
        run_mission(db, mission, node_rows, members)
    db.refresh(mission)
    return mission

def run_mission(db, mission, node_rows, members):
    # Atomic claim, same discipline as run_task: a mission may only be
    # executed once, from "planned". Re-running a finished mission
    # would re-execute completed nodes, bump artifact versions (making
    # fresh verifications stale) and duplicate scheduling decisions.
    claimed = (db.query(models.ChiefMission)
               .filter(models.ChiefMission.id == mission.id,
                       models.ChiefMission.status == "planned")
               .update({"status": "running"}, synchronize_session=False))
    db.commit()
    if claimed != 1:
        db.refresh(mission)
        raise ValueError(f"任务组当前状态为 {mission.status}，不能重复执行")
    db.refresh(mission)
    task = db.query(models.Task).get(mission.task_id)
    if task is None:
        mission.status = "failed"
        mission.report = {"mission_id": mission.id, "status": "failed",
                          "evidence": [f"任务不存在：{mission.task_id}"]}
        mission.finished_at = datetime.utcnow(); db.commit()
        return mission
    task.status = "running"; db.commit()
    agents = db.query(models.Agent).all()
    artifact = "projects/user-mgmt/user_api.py"
    test_rel = "projects/user-mgmt/test_user_api.py"
    evidence = []
    try:
        for node in node_rows:
            pool = [a for a in agents if a.id != mission.chief_agent_id]
            dep_ready = all(
                (lambda dn: dn is not None and dn.status == "completed")(
                    db.query(models.TaskNode).get(d))
                for d in (node.dependencies or []))
            refresh_live_loads(db, pool)
            decision = select_agent(pool, node.required_capabilities, node.required_tools,
                                    priority=task.priority, mode="sacas",
                                    dependencies_ready=dep_ready)
            db.add(models.SchedulerDecision(task_id=task.id, node_id=node.id, mode="sacas",
                candidates=decision.candidates, selected_agent_id=(
                    decision.selected.id if decision.selected else None), reason=decision.reason))
            db.commit()
            if decision.selected is None:
                node.status = "failed"; node.error_message = decision.reason
                node.finished_at = datetime.utcnow(); db.commit()
                raise RuntimeError(decision.reason)
            if not dep_ready:
                # SACAS treats dependency readiness as a hard gate, not a
                # scoring hint: running this node now would consume work
                # whose inputs do not exist yet.
                node.status = "failed"; node.error_type = "DEPENDENCY_ERROR"
                node.error_message = "依赖节点尚未完成，不能执行"
                node.finished_at = datetime.utcnow(); db.commit()
                raise RuntimeError("依赖节点尚未完成，不能执行")
            node.assigned_agent_id = decision.selected.id; node.status = "running"
            node.started_at = datetime.utcnow(); db.commit()
            agent = decision.selected
            if node.node_type == "plan" and ("规格" in node.name or "需求" in node.name):
                ws.write_file(db, agent.id, "projects/user-mgmt/SPEC.md",
                              "# 用户管理系统规格\n- get_profile(user_id)\n- admin_stats 仅管理员\n",
                              task_id=task.id)
                evidence.append(f"节点 {node.id}：产品经理写入 SPEC.md")
            elif node.node_type == "plan" and "设计" in node.name:
                ws.write_file(db, agent.id, "projects/user-mgmt/DESIGN.md",
                              "# 系统设计\n- 模块：user_api 提供 get_profile 与 admin_stats\n"
                              "- 约束：admin_stats 仅管理员可调用，其余接口公开读取\n"
                              "- 验证：test_user_api.py 覆盖普通用户与管理员两条路径\n",
                              task_id=task.id)
                evidence.append(f"节点 {node.id}：产品经理写入 DESIGN.md")
            elif node.node_type == "plan" and ("汇报" in node.name or "报告" in node.name
                                                or "汇总" in node.name):
                report_lines = [f"# 成果汇总汇报", f"目标：{task.title}", "",
                                "## 执行证据"] + [f"- {e}" for e in evidence]
                ws.write_file(db, agent.id, "projects/user-mgmt/REPORT.md",
                              "\n".join(report_lines) + "\n",
                              task_id=task.id)
                evidence.append(f"节点 {node.id}：产品经理写入 REPORT.md")
            elif node.node_type == "agent":
                ws.write_file(db, agent.id, artifact, APP_CODE, task_id=task.id)
                ws.write_file(db, agent.id, test_rel, TEST_CODE, task_id=task.id)
                evidence.append(f"节点 {node.id}：开发写入 {artifact}")
            elif node.node_type == "verify":
                from app.workspace import config
                target = os.path.join(config.get_shared_root(), "projects", "user-mgmt")
                proc = subprocess.run([sys.executable, "-m", "pytest", "-q"], cwd=target,
                                      capture_output=True, text=True, timeout=120)
                passed = proc.returncode == 0
                rec = verif_state.record_verification(db, artifact, passed,
                    test_run_id=f"pytest-{node.id}", task_id=task.id, node_id=node.id,
                    evidence=(proc.stdout or "")[-500:])
                evidence.append(f"节点 {node.id}：测试退出码 {proc.returncode}，"
                                f"成果验证 #{rec.id} 版本 {rec.artifact_version}，{'通过' if passed else '未通过'}")
                if not passed:
                    node.status = "failed"; node.error_message = "测试未通过"
                    node.finished_at = datetime.utcnow(); db.commit()
                    raise RuntimeError("验证未通过：测试失败")
            else:
                # No branch above produced anything for this node: marking
                # it completed would fabricate work that never happened.
                node.status = "failed"
                node.error_message = f"节点「{node.name}」类型 {node.node_type} 没有对应的执行动作"
                node.finished_at = datetime.utcnow(); db.commit()
                raise RuntimeError(node.error_message)
            node.status = "completed"; node.finished_at = datetime.utcnow(); db.commit()
        task.status = "completed"; db.commit()
        mission.status = "completed"
    except Exception as e:
        # Roll back FIRST: if the original failure was a broken
        # transaction (failed commit, SQLite lock), writing the failure
        # state on the same session would raise PendingRollbackError and
        # strand the mission in "running" forever.
        db.rollback()
        task = db.query(models.Task).get(mission.task_id)
        if task is not None:
            task.status = "failed"; task.error_message = str(e)[:500]
        for n in node_rows:
            if n.status in ("running", "pending"):
                # Pending nodes must not dangle forever under a failed
                # task: they will never run, so say so in their state.
                was = n.status
                n.status = "failed"
                n.error_message = (str(e)[:500] if was == "running"
                                   else "任务失败，这个节点没有执行")
                n.finished_at = datetime.utcnow()
        mission.status = "failed"; evidence.append(f"mission failed: {e}")
        db.commit()
    db.commit()  # mission status is durable before the report is built
    try:
        mission.report = build_report(db, mission, evidence)
    except Exception as report_error:
        # A broken report must not strand the mission's final state.
        db.rollback()
        mission.report = {"mission_id": mission.id, "status": mission.status,
                          "evidence": evidence,
                          "report_error": str(report_error)[:500]}
    mission.finished_at = datetime.utcnow(); db.commit()
    return mission

def assess_stagnation(outcomes, threshold=2):
    """Stagnation monitor (idea adapted from Magentic-One's stall counter,
    original implementation): count consecutive no-progress outcomes at
    the tail of a mission; reaching the threshold flags that the chief
    should re-plan instead of pushing on. Pure signal — it changes no
    scheduling or execution behaviour by itself.
    outcomes: iterable of bool (True = progress made)."""
    consecutive = 0
    for ok in reversed(list(outcomes)):
        if ok:
            break
        consecutive += 1
    stagnant = consecutive >= threshold
    return {"stagnant": stagnant, "consecutive_no_progress": consecutive,
            "threshold": threshold,
            "action": "replan_required" if stagnant else "continue"}


def build_report(db, mission, evidence=None):
    task = db.query(models.Task).get(mission.task_id)
    nodes = db.query(models.TaskNode).filter_by(task_id=mission.task_id).order_by(models.TaskNode.id).all()
    artifacts = {}
    for path in ("projects/user-mgmt/user_api.py", "projects/user-mgmt/SPEC.md"):
        cur = verif_state.current_verification(db, path)
        meta = db.query(models.SharedFileMeta).get(path)
        if meta or cur:
            artifacts[path] = {"version": meta.version if meta else None,
                               "hash": meta.content_hash if meta else None,
                               "verification": verif_state.out(cur),
                               "verified_current": bool(cur and cur.passed)}
    return {"mission_id": mission.id, "goal": mission.goal, "status": mission.status,
            "llm_called": False, "planner": "rule-based-keyword-v1",
            "team_id": mission.team_id, "chief_agent_id": mission.chief_agent_id,
            "task_id": mission.task_id,
            "nodes": [{"id": n.id, "name": n.name, "status": n.status,
                       "assigned_agent_id": n.assigned_agent_id} for n in nodes],
            "stagnation": assess_stagnation(
                [n.status == "completed" for n in nodes if n.status in ("completed", "failed")]),
            "artifacts": artifacts, "evidence": evidence or [],
            "task_status": task.status if task else None}

# ---------------- structured handoff (AgentHandoff) ----------------
def create_handoff(db, task_id, from_agent_id, to_agent_id, objective, completed_work,
                   artifact_paths, pending_issues=None, verification_requirements=None):
    versions = {}
    for p in artifact_paths:
        meta = db.query(models.SharedFileMeta).get(p)
        # Snapshot version AND content hash: version alone misses edits
        # that bypass write_file (terminal writes never bump it).
        versions[p] = ({"version": meta.version, "hash": meta.content_hash}
                       if meta else None)
    meta_payload = {"objective": objective, "completedWork": list(completed_work),
                    "artifactPaths": list(artifact_paths), "environmentVersion": versions,
                    "pendingIssues": list(pending_issues or []),
                    "verificationRequirements": list(verification_requirements or [])}
    msg = models.AgentMessage(team_id=None, task_id=task_id, sender_agent_id=from_agent_id,
                              receiver_agent_id=to_agent_id, message_type="HANDOFF",
                              content=objective, meta=meta_payload)
    db.add(msg); db.commit(); db.refresh(msg)
    return msg

def accept_handoff(db, message_id):
    """Receiver-side check: every artifact's current SharedFileMeta version
    must equal the version recorded at handoff time, else reject."""
    msg = db.query(models.AgentMessage).get(message_id)
    if msg is None or msg.message_type != "HANDOFF":
        raise ValueError("交接单不存在")
    recorded = (msg.meta or {}).get("environmentVersion", {})
    from app.workspace import service as ws_service
    for path, snap in recorded.items():
        # Legacy records stored a bare version int; new ones store
        # {"version": v, "hash": h}. Both must prove the artifact
        # actually exists — a missing artifact used to "match" its own
        # recorded None and be accepted.
        ver = snap.get("version") if isinstance(snap, dict) else snap
        want_hash = snap.get("hash") if isinstance(snap, dict) else None
        meta = db.query(models.SharedFileMeta).get(path)
        if meta is None or ver is None:
            raise ValueError(f"交接单验收失败：{path} 没有登记记录，无法验收")
        if meta.version != ver:
            raise ValueError(f"交接单验收失败：{path} 版本已变化（交接时版本 {ver}，当前版本 {meta.version}）")
        if want_hash is not None and ws_service.file_hash_on_disk(path) != want_hash:
            raise ValueError(f"交接单验收失败：{path} 内容已变化（版本未变但校验和不一致）")
    return {"accepted": True, "message_id": message_id, "meta": msg.meta}
import os  # noqa: E402  (used by run_mission)
