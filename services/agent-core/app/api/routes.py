"""REST API. Every list reads the real DB; empty DB -> empty list (no fake data)."""
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app import models, schemas
from app.database import get_db
from app.llm.client import fetch_models, LLMError
from app.tools.registry import list_tools
from app.evaluation import runner as evaluation_runner
from app.teams.dispatch import task_status_zh

router = APIRouter(prefix="/api")

from datetime import datetime as _datetime
_dt_min = _datetime.min

def _get_or_404(db, model, obj_id):
    # SQLite INTEGER is int64: an id outside that range cannot exist,
    # and binding it would raise OverflowError (a 500) instead of 404.
    if isinstance(obj_id, int) and not (-(2 ** 63) <= obj_id < 2 ** 63):
        raise HTTPException(404, f"编号为 {obj_id} 的记录不存在")
    obj = db.query(model).get(obj_id)
    if obj is None:
        raise HTTPException(404, f"编号为 {obj_id} 的记录不存在")
    return obj

def _provider_out(p):
    return {"id": p.id, "name": p.name, "base_url": p.base_url,
            "format": getattr(p, "format", None) or "chat_completions",
            "models": getattr(p, "models", None) or [],
            "enabled": p.enabled, "has_api_key": bool(p.api_key)}

def _check_format(fmt: str) -> None:
    from app.llm.client import FORMATS
    if fmt not in FORMATS:
        raise HTTPException(400, f"不支持的接口格式：{fmt}")

# ---- Model providers ----
@router.get("/providers")
def list_providers(db: Session = Depends(get_db)):
    return [_provider_out(p) for p in db.query(models.ModelProvider).all()]

@router.post("/providers", status_code=201)
def create_provider(body: schemas.ProviderIn, db: Session = Depends(get_db)):
    _check_format(body.format)
    p = models.ModelProvider(**body.model_dump())
    db.add(p); db.commit(); db.refresh(p)
    return _provider_out(p)

@router.put("/providers/{pid}")
def update_provider(pid: int, body: schemas.ProviderIn, db: Session = Depends(get_db)):
    _check_format(body.format)
    p = _get_or_404(db, models.ModelProvider, pid)
    for k, v in body.model_dump().items():
        if k == "api_key" and v is None:
            continue
        setattr(p, k, v)
    db.commit(); db.refresh(p)
    return _provider_out(p)

@router.delete("/providers/{pid}")
def delete_provider(pid: int, db: Session = Depends(get_db)):
    p = _get_or_404(db, models.ModelProvider, pid)
    db.delete(p); db.commit()
    return {"deleted": pid}

@router.post("/providers/{pid}/fetch-models")
def provider_fetch_models(pid: int, db: Session = Depends(get_db)):
    """Real call to the provider's model-list endpoint (shape depends on
    the provider's format). Failures are returned as 502 with the upstream
    error — never a fabricated model list."""
    p = _get_or_404(db, models.ModelProvider, pid)
    try:
        found = fetch_models(p)
        return {"provider_id": pid, "models": found}
    except LLMError as e:
        raise HTTPException(502, str(e))

@router.post("/providers/{pid}/test")
def provider_test(pid: int, db: Session = Depends(get_db)):
    p = _get_or_404(db, models.ModelProvider, pid)
    try:
        found = fetch_models(p)
        return {"ok": True, "model_count": len(found)}
    except LLMError as e:
        return {"ok": False, "error": str(e)}

@router.put("/providers/{pid}/models")
def provider_set_models(pid: int, body: dict, db: Session = Depends(get_db)):
    """Save the user-curated model list. Only these models appear in the
    agent-binding pickers; fetching candidates never changes this list
    by itself."""
    p = _get_or_404(db, models.ModelProvider, pid)
    raw = body.get("models")
    if raw is not None and not isinstance(raw, list):
        # Iterating a bare string would store its single characters as
        # model names; the payload must be a JSON array.
        raise HTTPException(422, "models 必须是字符串数组")
    seen: list[str] = []
    for m in raw or []:
        if not isinstance(m, str):
            continue
        name = m.strip()
        if name and name not in seen:
            seen.append(name)
    p.models = seen
    db.commit(); db.refresh(p)
    return _provider_out(p)

@router.post("/providers/{pid}/detect")
def provider_detect(pid: int, body: dict, db: Session = Depends(get_db)):
    """Connectivity detection that goes one step past listing models:
    it sends one real minimal chat request with the chosen model and
    reports the honest outcome (ok + latency, or the failure reason)."""
    import time as _time
    from app.llm.client import chat_completion
    p = _get_or_404(db, models.ModelProvider, pid)
    model = str(body.get("model") or "").strip()
    if not model:
        raise HTTPException(400, "先选择要检测的模型")
    started = _time.perf_counter()  # wall clock can jump; latency must not go negative
    try:
        res = chat_completion(p, model,
                              [{"role": "user", "content": "只回复：ok"}],
                              timeout=30)
        reply = str(res.get("content", ""))[:60]
        return {"ok": True, "model": model,
                "latency_ms": int((_time.perf_counter() - started) * 1000),
                "reply": reply, "reply_preview": reply}
    except LLMError as e:
        return {"ok": False, "model": model,
                "latency_ms": int((_time.perf_counter() - started) * 1000),
                "error": str(e)}

# ---- Agents ----
def _agent_activity(db: Session, agent) -> str:
    """Derive a fine-grained, honest activity state from real records.

    Sources (no state is invented):
    - agent.status == "disabled"                      -> "disabled"
    - agent.runtime_status == "Failed" (set by the    -> "failed"
      runner when the agent's last node failed)
    - agent.runtime_status == "Running" (set by the runner while a
      TaskNode executes) plus that running node's type:
        node_type == "tool"  -> "tool_calling" (the runner is inside the
                                Tool Registry call for this node)
        otherwise            -> "model_calling" (the runner is inside the
                                LLM chat_completion call for this node)
      Running with no running node found (teardown race) -> "running"
    - anything else                                   -> "idle"
    """
    if agent.status == "disabled":
        return "disabled"
    if agent.runtime_status == "Failed":
        return "failed"
    if agent.runtime_status == "Running":
        node = (db.query(models.TaskNode)
                .filter_by(assigned_agent_id=agent.id, status="running")
                .first())
        if node is not None:
            return "tool_calling" if node.node_type == "tool" else "model_calling"
        return "running"
    return "idle"


def _agent_out(db: Session, a):
    out = schemas.AgentOut.model_validate(a).model_dump()
    out["activity_status"] = _agent_activity(db, a)
    # current_load is derived at read time from real running nodes, like
    # activity_status: the stored column has no writer and is always 0,
    # which made the UI show 0% load for agents actively running work.
    from app.scheduler.engine import load_from_running
    running = (db.query(models.TaskNode)
               .filter_by(assigned_agent_id=a.id, status="running").count())
    out["current_load"] = load_from_running(running)
    # With zero executions there is no measured success rate: the stored
    # 0.5 is the scheduler's neutral prior. Report None so the UI says
    # 暂无数据 instead of presenting the prior as a real 50%.
    if not a.execution_count:
        out["historical_success_rate"] = None
    return out


@router.get("/agents")
def list_agents(db: Session = Depends(get_db)):
    return [_agent_out(db, a) for a in db.query(models.Agent).all()]

@router.post("/agents", status_code=201, response_model=schemas.AgentOut)
def create_agent(body: schemas.AgentIn, db: Session = Depends(get_db)):
    a = models.Agent(**body.model_dump())
    db.add(a); db.commit(); db.refresh(a)
    return _agent_out(db, a)

@router.get("/agents/{aid}", response_model=schemas.AgentOut)
def get_agent(aid: int, db: Session = Depends(get_db)):
    a = _get_or_404(db, models.Agent, aid)
    # Same derived shape as list/detail: the bare ORM row serializes the
    # stored current_load (a column with no writer, always 0) and the
    # stored 0.5 success-rate prior as if they were measured values.
    return _agent_out(db, a)

@router.put("/agents/{aid}", response_model=schemas.AgentOut)
def update_agent(aid: int, body: schemas.AgentIn, db: Session = Depends(get_db)):
    a = _get_or_404(db, models.Agent, aid)
    for k, v in body.model_dump().items():
        setattr(a, k, v)
    a.version = (a.version or 1) + 1
    db.commit(); db.refresh(a)
    # Return the same shape as list/detail: the bare ORM row serializes
    # activity_status as its schema default "idle" even for an agent
    # that was just disabled, contradicting the very next GET.
    return _agent_out(db, a)

@router.delete("/agents/{aid}")
def delete_agent(aid: int, db: Session = Depends(get_db)):
    a = _get_or_404(db, models.Agent, aid)
    db.delete(a); db.commit()
    return {"deleted": aid}

@router.post("/agents/{aid}/copy", status_code=201, response_model=schemas.AgentOut)
def copy_agent(aid: int, db: Session = Depends(get_db)):
    a = _get_or_404(db, models.Agent, aid)
    data = {c.name: getattr(a, c.name) for c in models.Agent.__table__.columns
            if c.name not in ("id", "created_at", "updated_at")}
    data["name"] = a.name + " (copy)"
    # A copy is a new employee: it inherits configuration (duties, skills,
    # capability profile) but NOT the original's track record. Copying
    # execution counts / success rate verbatim would hand the clone a
    # fabricated history that immediately skews scheduler scoring and
    # the profile page, so performance and runtime fields start fresh.
    data.update({"execution_count": 0, "success_count": 0,
                 "historical_success_rate": 0.5, "average_latency": 0.0,
                 "average_cost": 0.0, "current_load": 0.0,
                 "runtime_status": "Idle", "version": 1})
    clone = models.Agent(**data)
    db.add(clone); db.commit(); db.refresh(clone)
    return _agent_out(db, clone)

@router.post("/agents/{aid}/toggle")
def toggle_agent(aid: int, db: Session = Depends(get_db)):
    a = _get_or_404(db, models.Agent, aid)
    a.status = "disabled" if a.status == "active" else "active"
    db.commit(); db.refresh(a)
    return {"id": a.id, "status": a.status}

@router.put("/agents/{aid}/tools")
def update_agent_tools(aid: int, body: schemas.AgentToolsIn, db: Session = Depends(get_db)):
    """Replace one agent's granted tool list (the UI tool-permission editor).

    Grantable ids come from the real Tool Registry; ids the agent already
    holds are also accepted so legacy grants (e.g. terminal, web_search)
    round-trip instead of being silently stripped. Anything else is a
    400 — inventing a tool id must not create a phantom permission.
    """
    a = _get_or_404(db, models.Agent, aid)
    known = {t["id"] for t in list_tools()}
    allowed = known | set(a.tools or [])
    invalid = [t for t in body.tools if t not in allowed]
    if invalid:
        raise HTTPException(400, f"未知的工具编号：{', '.join(invalid)}")
    deduped = []
    for t in body.tools:
        if t not in deduped:
            deduped.append(t)
    a.tools = deduped
    db.commit(); db.refresh(a)
    return _agent_out(db, a)

# Keywords that tie a node's kind/name to the deliverable wording agents
# use in their structured duties. Only wording actually present in the
# agent's own deliverables list can match — nothing is inferred.
_DUTY_NODE_KEYWORDS = {
    "verify": ("测试", "验证", "验收", "审核", "评审"),
    "plan": ("规划", "需求", "方案", "标准"),
}
_DUTY_NAME_KEYWORDS = (
    (("规划", "计划", "需求"), ("规划", "需求", "方案")),
    (("文档", "编写", "撰写"), ("文档", "报告", "说明")),
    (("开发", "编码", "接口", "实现"), ("代码", "接口", "程序")),
    (("测试", "验证", "审核", "评审"), ("测试", "验证", "验收", "审核")),
    (("调研", "研究", "分析"), ("调研", "分析", "报告")),
)

def _duty_match(agent, node):
    """Where one agent's structured duties line up with one node.

    Purely descriptive (the scheduler's choice is unchanged): capability
    tags and tools the node requires that the agent actually has, plus
    deliverables whose wording matches the node's kind/name. Returns
    three lists; empty means no documented overlap."""
    if agent is None:
        return {"capabilities": [], "tools": [], "deliverables": []}
    tags = set(agent.capability_tags or [])
    tools = set(agent.tools or [])
    caps = [c for c in (node.required_capabilities or []) if c in tags]
    tls = [t for t in (node.required_tools or []) if t in tools]
    resp = agent.responsibilities or {}
    deliverables = resp.get("deliverables") or []
    matched = []
    keys = set(_DUTY_NODE_KEYWORDS.get(node.node_type or "", ()))
    name = node.name or ""
    for name_keys, deliv_keys in _DUTY_NAME_KEYWORDS:
        if any(k in name for k in name_keys):
            keys.update(deliv_keys)
    for d in deliverables:
        if any(k in str(d) for k in keys):
            matched.append(d)
    return {"capabilities": caps, "tools": tls, "deliverables": matched}

def _agent_detail(db: Session, a):
    """Profile aggregate for one agent, built only from real records:
    task nodes it executed, verification records of those tasks, and the
    per-node token snapshots the runner stored. Anything with no records
    comes back as None/empty so the UI can say 暂无数据 honestly."""
    out = _agent_out(db, a)
    nodes = (db.query(models.TaskNode)
             .filter_by(assigned_agent_id=a.id).all())
    # Current work: nodes running right now, with their task titles.
    current = []
    for n in nodes:
        if n.status == "running":
            t = db.query(models.Task).get(n.task_id)
            current.append({
                "task_id": n.task_id,
                "task_title": t.title if t else "",
                "node_id": n.id, "node_name": n.name,
                "node_type": n.node_type,
                "started_at": n.started_at.isoformat() if n.started_at else None})
    # History: tasks this agent contributed nodes to, most recent first.
    by_task = {}
    for n in nodes:
        by_task.setdefault(n.task_id, []).append(n)
    history = []
    for tid, tns in by_task.items():
        t = db.query(models.Task).get(tid)
        if t is None:
            continue
        durations = [(n.finished_at - n.started_at).total_seconds()
                     for n in tns if n.started_at and n.finished_at]
        ver = (db.query(models.VerificationRecord)
               .filter_by(task_id=tid)
               .order_by(models.VerificationRecord.timestamp.desc())
               .first())
        stamps = [s for n in tns for s in (n.started_at, n.finished_at) if s]
        history.append({
            "task_id": tid, "title": t.title, "task_status": t.status,
            "nodes": [{"node_id": n.id, "name": n.name,
                       "node_type": n.node_type, "status": n.status,
                       "started_at": n.started_at.isoformat() if n.started_at else None,
                       "finished_at": n.finished_at.isoformat() if n.finished_at else None,
                       "duration_seconds": ((n.finished_at - n.started_at).total_seconds()
                                            if n.started_at and n.finished_at else None)}
                      for n in sorted(tns, key=lambda x: x.id)],
            "duration_seconds": sum(durations) if durations else None,
            "verification": ({"passed": bool(ver.passed), "score": ver.score,
                              "verification_type": ver.verification_type}
                             if ver is not None else None),
            "_sort": max(stamps) if stamps else t.created_at})
    history.sort(key=lambda h: h["_sort"] or _dt_min, reverse=True)
    for h in history:
        h.pop("_sort", None)
    history = history[:20]
    # Performance from the same records (never from invented numbers).
    completed_nodes = sum(1 for n in nodes if n.status == "completed")
    failed_nodes = sum(1 for n in nodes if n.status == "failed")
    node_durations = [(n.finished_at - n.started_at).total_seconds()
                      for n in nodes if n.started_at and n.finished_at]
    total_tokens = 0
    token_records = 0
    for snap in db.query(models.ExecutionSnapshot).all():
        usage = (snap.state or {}).get("node_token_usage") or {}
        if usage.get("agent_id") == a.id:
            total_tokens += int(usage.get("total_tokens") or 0)
            token_records += 1
    memberships = (db.query(models.TeamMember, models.Team)
                   .join(models.Team, models.Team.id == models.TeamMember.team_id)
                   .filter(models.TeamMember.agent_id == a.id).all())
    skills = []
    for entry in a.skills or []:
        skill = None
        skill_id = None
        if isinstance(entry, int):
            skill_id = entry
        elif isinstance(entry, str) and entry.isdigit():
            try:
                skill_id = int(entry)
            except ValueError:
                skill_id = None  # e.g. "²".isdigit() is True but int() rejects it
        if skill_id is not None and -(2 ** 63) <= skill_id < 2 ** 63:
            skill = db.query(models.Skill).get(skill_id)
        if skill is not None:
            skills.append({"id": skill.id, "name": skill.name})
        else:
            skills.append({"id": None, "name": str(entry)})
    out["detail"] = {
        "teams": [{"id": t.id, "name": t.name,
                   "role_in_team": m.role_in_team,
                   "is_leader": bool(m.is_leader)} for m, t in memberships],
        "current_tasks": current,
        "history": history,
        "skills_resolved": skills,
        "performance": {
            "execution_count": a.execution_count,
            "success_count": a.success_count,
            "historical_success_rate": out["historical_success_rate"],
            "completed_nodes": completed_nodes,
            "failed_nodes": failed_nodes,
            "node_success_rate": (completed_nodes / (completed_nodes + failed_nodes)
                                  if (completed_nodes + failed_nodes) else None),
            "average_duration_seconds": (sum(node_durations) / len(node_durations)
                                         if node_durations else None),
            "average_latency": a.average_latency,
            "total_tokens": total_tokens if token_records else None,
            "current_load": out["current_load"],
        },
    }
    return out

@router.get("/agents/{aid}/detail")
def get_agent_detail(aid: int, db: Session = Depends(get_db)):
    return _agent_detail(db, _get_or_404(db, models.Agent, aid))

# ---- Teams ----
def _team_out(db, t):
    members = db.query(models.TeamMember).filter_by(team_id=t.id).all()
    return {"id": t.id, "name": t.name, "description": t.description,
            "leader_agent_id": t.leader_agent_id,
            "members": [{"agent_id": m.agent_id, "role_in_team": m.role_in_team,
                         "is_leader": m.is_leader} for m in members]}

@router.get("/teams")
def list_teams(db: Session = Depends(get_db)):
    return [_team_out(db, t) for t in db.query(models.Team).all()]

@router.post("/teams", status_code=201)
def create_team(body: schemas.TeamIn, db: Session = Depends(get_db)):
    # Validate membership up front (add_member does the same): SQLite
    # foreign keys are not enforced here, so unchecked ids would insert
    # ghost members that _team_out then echoes back as real.
    wanted = list(body.member_agent_ids or [])
    if body.leader_agent_id is not None and body.leader_agent_id not in wanted:
        wanted.append(body.leader_agent_id)
    if wanted:
        existing = {a.id for a in db.query(models.Agent)
                    .filter(models.Agent.id.in_(wanted)).all()}
        missing = [mid for mid in wanted if mid not in existing]
        if missing:
            raise HTTPException(400, f"员工不存在：{missing}")
    t = models.Team(name=body.name, description=body.description,
                    leader_agent_id=body.leader_agent_id)
    # Team + members in ONE transaction: committing the team first left
    # an empty team behind when a member insert failed.
    db.add(t)
    db.flush()
    # Membership is a set: a duplicated id must not insert a second row
    # (the member list would show the same person twice), and the leader
    # is a member too — otherwise the member count undercounts and the
    # team view disagrees with the chat member list, which already
    # treats the leader as an implicit member.
    seen = []
    for mid in wanted:
        if mid not in seen:
            seen.append(mid)
    for mid in seen:
        db.add(models.TeamMember(team_id=t.id, agent_id=mid,
                                 is_leader=(mid == body.leader_agent_id)))
    db.commit()
    db.refresh(t)
    return _team_out(db, t)

@router.delete("/teams/{tid}")
def delete_team(tid: int, db: Session = Depends(get_db)):
    t = _get_or_404(db, models.Team, tid)
    db.query(models.TeamMember).filter_by(team_id=tid).delete()
    db.delete(t); db.commit()
    return {"deleted": tid}

@router.post("/teams/{tid}/members")
def add_member(tid: int, payload: dict, db: Session = Depends(get_db)):
    _get_or_404(db, models.Team, tid)
    agent_id = payload.get("agent_id")
    if not isinstance(agent_id, int):
        raise HTTPException(422, "agent_id 必须是整数")
    _get_or_404(db, models.Agent, agent_id)
    # team_members has no (team_id, agent_id) uniqueness: without this
    # check the same person could be added twice and listed twice.
    if db.query(models.TeamMember).filter_by(team_id=tid, agent_id=agent_id).first():
        raise HTTPException(409, f"员工 {agent_id} 已经是团队成员")
    m = models.TeamMember(team_id=tid, agent_id=agent_id,
                          role_in_team=payload.get("role_in_team", "member"),
                          is_leader=bool(payload.get("is_leader", False)))
    db.add(m); db.commit()
    return _team_out(db, db.query(models.Team).get(tid))

@router.delete("/teams/{tid}/members/{agent_id}")
def remove_member(tid: int, agent_id: int, db: Session = Depends(get_db)):
    db.query(models.TeamMember).filter_by(team_id=tid, agent_id=agent_id).delete()
    db.commit()
    return _team_out(db, _get_or_404(db, models.Team, tid))

# ---- Tasks ----
def _task_out(db, t):
    nodes = db.query(models.TaskNode).filter_by(task_id=t.id).all()
    return {"id": t.id, "title": t.title, "description": t.description,
            "team_id": t.team_id, "priority": t.priority,
            "scheduler_mode": t.scheduler_mode, "status": t.status,
            "result": t.result, "error_type": t.error_type,
            "error_message": t.error_message, "token_usage": t.token_usage,
            "retry_count": t.retry_count,
            "created_at": t.created_at.isoformat() if t.created_at else None,
            "nodes": [{"id": n.id, "name": n.name, "node_type": n.node_type,
                       "required_capabilities": n.required_capabilities,
                       "dependencies": n.dependencies, "status": n.status,
                       # Derived display state (exposure only; the stored
                       # status vocabulary is untouched): a verification
                       # node (node_type == "verify", compiled from a
                       # workflow Verification node or the built-in DAG)
                       # that is currently running IS in the verification
                       # phase, so expose it as "verifying".
                       "display_status": ("verifying" if (n.node_type == "verify" and n.status == "running") else n.status),
                       "assigned_agent_id": n.assigned_agent_id,
                       "output_data": n.output_data, "error_type": n.error_type,
                       "started_at": n.started_at.isoformat() if n.started_at else None,
                       "finished_at": n.finished_at.isoformat() if n.finished_at else None,
                       "error_message": n.error_message} for n in nodes]}

@router.get("/tasks")
def list_tasks(db: Session = Depends(get_db)):
    return [_task_out(db, t) for t in db.query(models.Task).order_by(models.Task.id.desc()).all()]

@router.post("/tasks", status_code=201)
def create_task(body: schemas.TaskIn, db: Session = Depends(get_db)):
    if body.team_id is not None:
        _get_or_404(db, models.Team, body.team_id)
    t = models.Task(**body.model_dump())
    # No commit before build_dag: build_dag commits the task together
    # with its nodes. Committing here first stranded an orphan task
    # (no nodes, never runnable) whenever build_dag failed.
    db.add(t); db.flush()
    from app.execution.runner import build_dag
    build_dag(db, t)
    db.refresh(t)
    return _task_out(db, t)

@router.get("/tasks/{tid}")
def get_task(tid: int, db: Session = Depends(get_db)):
    return _task_out(db, _get_or_404(db, models.Task, tid))

def _plan_preview(db, task):
    """Pre-execution plan: for every node, run the real scheduler in
    preview (nothing persisted, no assignment written) and report its
    candidates/scores, plus the user's pinned assignee when one exists.
    This is the same select_agent the runner calls at execution time."""
    from app.scheduler.engine import refresh_live_loads, select_agent
    agents = db.query(models.Agent).all()
    refresh_live_loads(db, agents)
    nodes = (db.query(models.TaskNode).filter_by(task_id=task.id)
             .order_by(models.TaskNode.id).all())
    agents_by_id = {a.id: a for a in agents}
    out = []
    for idx, n in enumerate(nodes):
        decision = select_agent(agents, n.required_capabilities, n.required_tools,
                                priority=task.priority, mode=task.scheduler_mode,
                                rotation_index=idx)
        selected_id = (n.assigned_agent_id if n.assigned_agent_id
                       else (decision.selected.id if decision.selected else None))
        out.append({"node_id": n.id, "name": n.name, "node_type": n.node_type,
                    "required_capabilities": n.required_capabilities,
                    "required_tools": n.required_tools,
                    "dependencies": n.dependencies,
                    "status": n.status,
                    "pinned_agent_id": n.assigned_agent_id,
                    "selected_agent_id": selected_id,
                    # Descriptive only: where the selected agent's
                    # structured duties meet this node's needs. The
                    # scheduler's decision above is untouched.
                    "duty_match": _duty_match(agents_by_id.get(selected_id), n),
                    "candidates": decision.candidates,
                    "reason": decision.reason})
    return {"task_id": task.id, "status": task.status,
            "mode": task.scheduler_mode, "nodes": out}

@router.get("/tasks/{tid}/plan")
def task_plan(tid: int, db: Session = Depends(get_db)):
    return _plan_preview(db, _get_or_404(db, models.Task, tid))

@router.put("/tasks/{tid}/nodes/{nid}/assign")
def assign_node(tid: int, nid: int, body: schemas.NodeAssignIn, db: Session = Depends(get_db)):
    """Pin (or unpin, with agent_id=null) one node's assignee before
    execution. The runner honors an active pinned agent and records the
    decision for audit; execution itself is unchanged."""
    task = _get_or_404(db, models.Task, tid)
    if task.status != "pending":
        raise HTTPException(400, "任务已经开始或结束，不能再改派")
    node = db.query(models.TaskNode).filter_by(id=nid, task_id=tid).first()
    if node is None:
        raise HTTPException(404, f"任务 {tid} 没有节点 {nid}")
    if body.agent_id is not None:
        _get_or_404(db, models.Agent, body.agent_id)
    node.assigned_agent_id = body.agent_id
    db.commit()
    return _plan_preview(db, task)

@router.post("/tasks/{tid}/execute")
def execute_task(tid: int, db: Session = Depends(get_db)):
    """Runs the real pipeline. With no usable model provider the task fails
    honestly with MODEL_ERROR recorded — output is never fabricated."""
    t = _get_or_404(db, models.Task, tid)
    if t.status == "running":
        raise HTTPException(409, "任务正在执行中")
    if t.status in ("completed", "cancelled"):
        # Executing a completed task would spin through zero pending
        # nodes and re-report success without running anything; a
        # cancelled task must stay cancelled. Re-running either is what
        # /retry (explicit reset) is for.
        raise HTTPException(409, f"任务已{'完成' if t.status == 'completed' else '取消'}，"
                                 "如需再次执行请用「重新执行」")
    from app.execution.runner import run_task
    try:
        run_task(db, t)
    except RuntimeError as e:
        # The runner's atomic claim lost a race against the status check
        # above: the same "already running" fact must get the same 409,
        # not a bare 500.
        db.rollback()
        raise HTTPException(409, str(e))
    db.refresh(t)
    return _task_out(db, t)

@router.post("/tasks/{tid}/cancel")
def cancel_task(tid: int, db: Session = Depends(get_db)):
    t = _get_or_404(db, models.Task, tid)
    # Only a not-yet-started task can be cancelled: a running task is
    # owned by the runner (which would overwrite "cancelled" as it
    # converges), and a finished task's outcome is history.
    if t.status != "pending":
        raise HTTPException(409, f"任务当前状态是 {task_status_zh(t.status)}，只有待执行的任务可以取消")
    t.status = "cancelled"; db.commit()
    return _task_out(db, t)

@router.post("/tasks/{tid}/retry")
def retry_task(tid: int, db: Session = Depends(get_db)):
    t = _get_or_404(db, models.Task, tid)
    if t.status == "running":
        raise HTTPException(409, "任务正在执行中，不能重置")
    for n in db.query(models.TaskNode).filter_by(task_id=tid).all():
        n.status = "pending"; n.error_type = None; n.error_message = None
        n.output_data = None; n.started_at = None; n.finished_at = None
    t.status = "pending"; t.error_type = None; t.error_message = None
    t.result = None
    db.commit()
    return _task_out(db, t)

@router.get("/tasks/{tid}/messages")
def task_messages(tid: int, db: Session = Depends(get_db)):
    _get_or_404(db, models.Task, tid)  # a missing task is 404, not an empty list
    rows = db.query(models.AgentMessage).filter_by(task_id=tid).order_by(models.AgentMessage.timestamp).all()
    return [{"message_id": m.message_id, "sender_agent_id": m.sender_agent_id,
             "receiver_agent_id": m.receiver_agent_id, "message_type": m.message_type,
             "content": m.content,
             "timestamp": m.timestamp.isoformat() if m.timestamp else None} for m in rows]

@router.get("/tasks/{tid}/snapshots")
def task_snapshots(tid: int, db: Session = Depends(get_db)):
    _get_or_404(db, models.Task, tid)
    rows = db.query(models.ExecutionSnapshot).filter_by(task_id=tid).all()
    return [{"id": s.id, "state": s.state,
             "created_at": s.created_at.isoformat() if s.created_at else None} for s in rows]

# ---- Scheduler / verification queries ----
@router.get("/scheduler/decisions")
def list_decisions(task_id: int = None, db: Session = Depends(get_db)):
    q = db.query(models.SchedulerDecision)
    if task_id:
        q = q.filter_by(task_id=task_id)
    return [{"id": d.id, "task_id": d.task_id, "node_id": d.node_id, "mode": d.mode,
             "candidates": d.candidates, "selected_agent_id": d.selected_agent_id,
             "reason": d.reason,
             "created_at": d.created_at.isoformat() if d.created_at else None}
            for d in q.order_by(models.SchedulerDecision.id.desc()).all()]

@router.get("/verifications")
def list_verifications(task_id: int = None, db: Session = Depends(get_db)):
    q = db.query(models.VerificationRecord)
    if task_id:
        q = q.filter_by(task_id=task_id)
    return [{"verification_id": v.verification_id, "task_id": v.task_id, "node_id": v.node_id,
             "verification_type": v.verification_type, "passed": v.passed, "score": v.score,
             "evidence": v.evidence, "errors": v.errors,
             "timestamp": v.timestamp.isoformat() if v.timestamp else None}
            for v in q.order_by(models.VerificationRecord.timestamp.desc()).all()]

# ---- Tools ----
@router.get("/tools")
def tools():
    return list_tools()

# ---- Skills ----
@router.get("/skills")
def list_skills(db: Session = Depends(get_db)):
    return db.query(models.Skill).all()

@router.post("/skills", status_code=201)
def create_skill(body: schemas.SkillIn, db: Session = Depends(get_db)):
    s = models.Skill(**body.model_dump())
    db.add(s); db.commit(); db.refresh(s)
    return s

@router.put("/skills/{sid}")
def update_skill(sid: int, body: schemas.SkillIn, db: Session = Depends(get_db)):
    s = _get_or_404(db, models.Skill, sid)
    for k, v in body.model_dump().items():
        if k in ("source", "files"):
            continue  # provenance + bundled files are import-managed
        setattr(s, k, v)
    db.commit(); db.refresh(s)
    return s

@router.post("/skills/import")
async def import_skill(file: UploadFile = File(...), db: Session = Depends(get_db)):
    """Import a skill package (.zip of SKILL.md directories). Per-skill
    results are reported item by item; a bad skill never sinks the rest."""
    from app.skills.package import import_skill_package, MAX_UPLOAD_BYTES
    raw = await file.read(MAX_UPLOAD_BYTES + 1)
    try:
        return import_skill_package(db, raw)
    except ValueError as e:
        raise HTTPException(400, str(e))

@router.delete("/skills/{sid}")
def delete_skill(sid: int, db: Session = Depends(get_db)):
    s = _get_or_404(db, models.Skill, sid)
    db.delete(s); db.commit()
    return {"deleted": sid}

# ---- MCP servers ----
@router.get("/mcp-servers")
def list_mcp(db: Session = Depends(get_db)):
    return db.query(models.MCPServer).all()

@router.post("/mcp-servers", status_code=201)
def create_mcp(body: schemas.MCPIn, db: Session = Depends(get_db)):
    s = models.MCPServer(**body.model_dump())
    db.add(s); db.commit(); db.refresh(s)
    return s

@router.delete("/mcp-servers/{sid}")
def delete_mcp(sid: int, db: Session = Depends(get_db)):
    s = _get_or_404(db, models.MCPServer, sid)
    db.delete(s); db.commit()
    return {"deleted": sid}

@router.put("/mcp-servers/{sid}")
def update_mcp(sid: int, body: dict, db: Session = Depends(get_db)):
    s = _get_or_404(db, models.MCPServer, sid)
    # SQLAlchemy's Boolean type rejects a string at flush time
    # (TypeError -> 500): "false" as a string must be a 422 here, the
    # same rule the audit applied to approval/verification booleans.
    if "enabled" in body and not isinstance(body["enabled"], bool):
        raise HTTPException(422, "enabled 必须是布尔值")
    for key in ("name", "transport", "command_or_url", "args", "env", "enabled"):
        if key in body:
            setattr(s, key, body[key])
    db.commit(); db.refresh(s)
    return s

@router.get("/activity")
def activity_feed(limit: int = 30, db: Session = Depends(get_db)):
    """Merged live feed for the dashboard: agent messages (task
    assignments, results, knowledge references) + workspace audit
    events (commands, browser actions, file changes, leases), newest
    first. Everything comes from real execution records."""
    from datetime import datetime as _dt
    agents = {a.id: a.name for a in db.query(models.Agent).all()}
    tasks = {t.id: t.title for t in db.query(models.Task).all()}
    items = []
    msgs = (db.query(models.AgentMessage)
            .order_by(models.AgentMessage.timestamp.desc()).limit(limit).all())
    for m in msgs:
        items.append({"at": m.timestamp, "kind": m.message_type,
                      "agent_id": m.sender_agent_id,
                      "agent_name": agents.get(m.sender_agent_id),
                      "task_id": m.task_id, "task_title": tasks.get(m.task_id),
                      "text": m.content})
    events = (db.query(models.WorkspaceEvent)
              .order_by(models.WorkspaceEvent.created_at.desc()).limit(limit).all())
    for e in events:
        items.append({"at": e.created_at, "kind": e.event_type,
                      "agent_id": e.agent_id,
                      "agent_name": agents.get(e.agent_id),
                      "task_id": e.task_id, "task_title": tasks.get(e.task_id),
                      "text": e.detail or e.path or ""})
    items.sort(key=lambda x: x["at"] or _dt.min, reverse=True)
    return items[:limit]

@router.post("/mcp-servers/{sid}/test")
def test_mcp(sid: int, db: Session = Depends(get_db)):
    """Connection test performs a real reachability check for HTTP transports;
    stdio servers are reported as not-verified (no process is spawned silently)."""
    s = _get_or_404(db, models.MCPServer, sid)
    if s.transport == "streamable_http" and s.command_or_url.startswith("http"):
        import httpx
        try:
            # trust_env=False: same NO_PROXY parsing crash guard as the LLM client.
            with httpx.Client(trust_env=False, timeout=10) as c:
                r = c.get(s.command_or_url)
            # Reachable is not the same as OK: an upstream 404/500 must
            # not be reported as a passing connection test.
            return {"ok": r.status_code < 400, "status_code": r.status_code}
        except httpx.ConnectError:
            return {"ok": False, "error": "连接失败：目标不可达或拒绝连接"}
        except httpx.TimeoutException:
            return {"ok": False, "error": "连接超时：10 秒内没有响应"}
        except Exception:
            return {"ok": False, "error": "连接失败：请检查地址是否正确"}
    return {"ok": False, "error": "本地命令方式的服务器不做静默连接测试：没有用户明确操作时不会启动子进程"}

# ---- Workflows ----
@router.get("/workflows")
def list_workflows(db: Session = Depends(get_db)):
    return db.query(models.Workflow).all()

@router.post("/workflows", status_code=201)
def save_workflow(body: schemas.WorkflowIn, db: Session = Depends(get_db)):
    w = models.Workflow(name=body.name, graph=body.graph)
    db.add(w); db.commit(); db.refresh(w)
    return w

@router.get("/workflows/{wid}")
def get_workflow(wid: int, db: Session = Depends(get_db)):
    return _get_or_404(db, models.Workflow, wid)

@router.put("/workflows/{wid}")
def update_workflow(wid: int, body: schemas.WorkflowIn, db: Session = Depends(get_db)):
    w = _get_or_404(db, models.Workflow, wid)
    w.name = body.name; w.graph = body.graph
    db.commit(); db.refresh(w)
    return w

@router.delete("/workflows/{wid}")
def delete_workflow(wid: int, db: Session = Depends(get_db)):
    w = _get_or_404(db, models.Workflow, wid)
    db.delete(w); db.commit()
    return {"deleted": wid}

# ---- Memory ----
@router.get("/memories")
def list_memories(agent_id: int = None, q: str = None, db: Session = Depends(get_db)):
    query = db.query(models.MemoryItem)
    if agent_id:
        query = query.filter_by(agent_id=agent_id)
    if q:
        query = query.filter(models.MemoryItem.content.contains(q))
    return query.order_by(models.MemoryItem.id.desc()).all()

@router.post("/memories", status_code=201)
def create_memory(body: schemas.MemoryIn, db: Session = Depends(get_db)):
    m = models.MemoryItem(**body.model_dump())
    db.add(m); db.commit(); db.refresh(m)
    return m

@router.delete("/memories/{mid}")
def delete_memory(mid: int, db: Session = Depends(get_db)):
    m = _get_or_404(db, models.MemoryItem, mid)
    db.delete(m); db.commit()
    return {"deleted": mid}

# ---- Evaluations ----
@router.get("/evaluations")
def list_evaluations(db: Session = Depends(get_db)):
    return db.query(models.EvaluationRun).all()

@router.post("/evaluations", status_code=201)
def create_evaluation(body: schemas.EvaluationIn, db: Session = Depends(get_db)):
    e = models.EvaluationRun(**body.model_dump())
    db.add(e); db.commit(); db.refresh(e)
    return e

@router.post("/evaluations/{eid}/run")
def run_evaluation(eid: int, db: Session = Depends(get_db)):
    """Execute the experiment for real via benchmarks/run_benchmark.py
    (production engines) and store the measured metrics on the row.
    A failure is recorded as status=failed with the error — never faked."""
    e = _get_or_404(db, models.EvaluationRun, eid)
    if e.status == "running":
        raise HTTPException(409, "实验正在运行中")
    # Atomic claim: the check above is only a fast path — two concurrent
    # runs must not both pass it and execute the experiment twice.
    claimed = (db.query(models.EvaluationRun)
               .filter(models.EvaluationRun.id == eid,
                       models.EvaluationRun.status != "running")
               .update({"status": "running"}, synchronize_session=False))
    db.commit()
    if claimed != 1:
        raise HTTPException(409, "实验正在运行中")
    db.refresh(e)
    try:
        e.metrics = evaluation_runner.run_evaluation(e.mode)
        e.status = "completed"
    except Exception as exc:
        e.metrics = {"error": str(exc)}
        e.status = "failed"
    db.commit(); db.refresh(e)
    return e

@router.delete("/evaluations/{eid}")
def delete_evaluation(eid: int, db: Session = Depends(get_db)):
    e = _get_or_404(db, models.EvaluationRun, eid)
    if e.status == "running":
        raise HTTPException(409, "实验正在运行，无法删除")
    db.delete(e); db.commit()
    return {"deleted": eid}

# ---- Stats (computed from real rows only) ----
@router.get("/stats")
def stats(db: Session = Depends(get_db)):
    tasks = db.query(models.Task).all()
    done = [t for t in tasks if t.status == "completed"]
    # Success rate is measured over finished tasks only (completed vs
    # failed): counting pending/running/cancelled tasks in the
    # denominator would punish work that has not had a chance to fail.
    finished = [t for t in tasks if t.status in ("completed", "failed")]
    # Telemetry aggregated from real execution records only: steps that
    # actually started (TaskNode.started_at) and the mean duration of
    # audited shared-computer command executions. None when no data.
    executed_steps = (db.query(models.TaskNode)
                      .filter(models.TaskNode.started_at.isnot(None)).count())
    durations = [r.duration_ms for r in db.query(models.WorkspaceCommandLog).all()
                 if r.duration_ms is not None]
    return {"agent_count": db.query(models.Agent).count(),
            "team_count": db.query(models.Team).count(),
            "task_count": len(tasks),
            "running_tasks": sum(1 for t in tasks if t.status == "running"),
            "success_rate": (len(done) / len(finished)) if finished else None,
            "total_tokens": sum(t.token_usage or 0 for t in tasks),
            "executed_steps": executed_steps,
            "avg_tool_latency_ms": (round(sum(durations) / len(durations))
                                    if durations else None)}
