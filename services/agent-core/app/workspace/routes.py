"""REST API for the Shared Linux Workspace (prefix /api/workspace).

Permission model: executing commands or writing files requires the acting
agent to hold the matching unified-registry tool (linux_exec / linux_file_write
etc.) — the same check the Tool Registry itself performs. There is no
side channel around it.
"""
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app import models, schemas
from app.database import get_db
from app.seed_faces import backfill_seed_face
from app.tools.registry import check_permission
from app.workspace import config, dependencies, desktop, review, sandbox, service

router = APIRouter(prefix="/api/workspace")


def _agent_or_404(db, agent_id):
    agent = db.query(models.Agent).get(agent_id)
    if agent is None:
        raise HTTPException(404, f"员工 {agent_id} 不存在")
    return agent


def _require_tool(agent, tool_id):
    if not check_permission(agent.tools, tool_id):
        raise HTTPException(403, f"员工 {agent.id} 没有使用工具「{tool_id}」的权限")


def _session_out(s):
    return {"id": s.id, "env_id": s.env_id, "agent_id": s.agent_id, "task_id": s.task_id,
            "cwd": s.cwd or "", "status": s.status,
            "created_at": s.created_at.isoformat() if s.created_at else None,
            "last_active_at": s.last_active_at.isoformat() if s.last_active_at else None}


def _log_out(row):
    return {"id": row.id, "env_id": row.env_id, "sandbox": getattr(row, "sandbox", None),
            "session_id": row.session_id,
            "agent_id": row.agent_id, "task_id": row.task_id, "command": row.command,
            "cwd": row.cwd or "", "exit_code": row.exit_code, "stdout": row.stdout or "",
            "stderr": row.stderr or "", "status": row.status,
            "started_at": row.started_at.isoformat() if row.started_at else None,
            "finished_at": row.finished_at.isoformat() if row.finished_at else None,
            "duration_ms": row.duration_ms}


def _event_out(e):
    return {"id": e.id, "env_id": e.env_id, "event_type": e.event_type,
            "agent_id": e.agent_id, "task_id": e.task_id, "path": e.path,
            "detail": e.detail or "",
            "created_at": e.created_at.isoformat() if e.created_at else None}


# ------------------------------------------------------------- environment ----

@router.get("/status")
def workspace_status(db: Session = Depends(get_db)):
    return service.env_status(db)


# ----------------------------------------------------------------- sandbox ----

@router.get("/sandbox")
def sandbox_get(db: Session = Depends(get_db)):
    return sandbox.sandbox_info(db)


@router.put("/sandbox")
def sandbox_select(body: schemas.SandboxSelectIn, db: Session = Depends(get_db)):
    if body.distro not in sandbox.DISTRO_IDS:
        raise HTTPException(400, f"不支持的 Linux 发行版 {body.distro}，只能从受支持清单中选择")
    version = body.version or sandbox.default_version(body.distro)
    if sandbox.find_version(body.distro, version) is None:
        raise HTTPException(400, f"发行版 {body.distro} 没有 {version} 这个受支持版本")
    if sandbox.install_status(db, body.distro, version) != "installed":
        raise HTTPException(400, "这个系统还没有安装，请先安装，装好后才能设为当前沙箱")
    sandbox.set_selection(db, body.distro, version)
    info = sandbox.sandbox_info(db)
    info["message"] = sandbox.switch_note(info["isolation_mode"])
    return info


@router.post("/sandbox/install")
def sandbox_install(body: schemas.SandboxSelectIn, db: Session = Depends(get_db)):
    if body.distro not in sandbox.DISTRO_IDS:
        raise HTTPException(400, f"不支持的 Linux 发行版 {body.distro}，只能从受支持清单中选择")
    version = body.version or sandbox.default_version(body.distro)
    if sandbox.find_version(body.distro, version) is None:
        raise HTTPException(400, f"发行版 {body.distro} 没有 {version} 这个受支持版本")
    return sandbox.start_install(db, body.distro, version)


@router.get("/sandbox/dependencies")
def sandbox_dependencies(db: Session = Depends(get_db)):
    return dependencies.status_payload(db)


@router.post("/sandbox/dependencies/install")
def sandbox_dependencies_install(body: schemas.DepsInstallIn = None,
                                 db: Session = Depends(get_db)):
    keys = body.packages if body else None
    if keys is not None:
        unknown = [k for k in keys if k not in dependencies.KEYS]
        if unknown:
            raise HTTPException(400, f"不在常用依赖清单中的项：{', '.join(unknown)}")
    return dependencies.start_job(db, keys)


# ---------------------------------------------------------------- sessions ----

@router.post("/sessions", status_code=201)
def create_session(body: schemas.WorkspaceSessionIn, db: Session = Depends(get_db)):
    _agent_or_404(db, body.agent_id)
    try:
        return _session_out(service.create_session(db, body.agent_id, body.task_id))
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/sessions")
def list_sessions(agent_id: int = None, db: Session = Depends(get_db)):
    return [_session_out(s) for s in service.list_sessions(db, agent_id)]


@router.delete("/sessions/{sid}")
def close_session(sid: int, db: Session = Depends(get_db)):
    try:
        return _session_out(service.close_session(db, sid))
    except ValueError as e:
        raise HTTPException(404, str(e))


@router.post("/sessions/{sid}/exec")
def exec_in_session(sid: int, body: schemas.WorkspaceExecIn, db: Session = Depends(get_db)):
    session = db.query(models.WorkspaceSession).get(sid)
    if session is None:
        raise HTTPException(404, f"会话 {sid} 不存在")
    if session.status != "active":
        raise HTTPException(400, f"会话 {sid} 已关闭")
    agent = _agent_or_404(db, session.agent_id)
    _require_tool(agent, "linux_exec")
    try:
        result = service.exec_in_session(db, session, body.command, task_id=body.task_id,
                                         approved=body.approved, timeout=body.timeout)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if result.get("blocked"):
        raise HTTPException(403, result["stderr"])
    return result


@router.get("/sessions/{sid}/logs")
def session_logs(sid: int, limit: int = 100, db: Session = Depends(get_db)):
    if db.query(models.WorkspaceSession).get(sid) is None:
        raise HTTPException(404, f"会话 {sid} 不存在")
    return [_log_out(r) for r in service.list_command_logs(db, session_id=sid, limit=limit)]


@router.get("/commands")
def command_logs(agent_id: int = None, task_id: int = None, limit: int = 100,
                 db: Session = Depends(get_db)):
    return [_log_out(r) for r in service.list_command_logs(
        db, agent_id=agent_id, task_id=task_id, limit=limit)]


# ------------------------------------------------------------------- files ----

@router.get("/files")
def list_files(path: str = "", db: Session = Depends(get_db)):
    try:
        return service.list_files(db, path)
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/files/content")
def read_file(path: str, db: Session = Depends(get_db)):
    try:
        return service.read_file(db, path)
    except ValueError as e:
        raise HTTPException(404, str(e))


@router.post("/files/write")
def write_file(body: schemas.WorkspaceFileWriteIn, db: Session = Depends(get_db)):
    agent = _agent_or_404(db, body.agent_id)
    _require_tool(agent, "linux_file_write")
    try:
        return service.write_file(db, body.agent_id, body.path, body.content,
                                  expected_version=body.expected_version, task_id=body.task_id)
    except service.ConflictError as e:
        raise HTTPException(409, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/files/delete")
def delete_file(body: schemas.WorkspaceFileActionIn, db: Session = Depends(get_db)):
    agent = _agent_or_404(db, body.agent_id)
    _require_tool(agent, "linux_file_write")
    try:
        return service.delete_file(db, body.agent_id, body.path, task_id=body.task_id)
    except service.ConflictError as e:
        raise HTTPException(409, str(e))
    except ValueError as e:
        raise HTTPException(404, str(e))


@router.post("/files/lock")
def lock_file(body: schemas.WorkspaceLockIn, db: Session = Depends(get_db)):
    _agent_or_404(db, body.agent_id)
    try:
        lock = service.acquire_file_lock(db, body.agent_id, body.path, body.ttl_seconds)
        return {"lock_id": lock.id, "path": lock.path, "agent_id": lock.agent_id,
                "expires_at": lock.expires_at.isoformat() if lock.expires_at else None}
    except service.ConflictError as e:
        raise HTTPException(409, str(e))


@router.post("/files/unlock")
def unlock_file(body: schemas.WorkspaceFileActionIn, db: Session = Depends(get_db)):
    _agent_or_404(db, body.agent_id)
    try:
        lock = service.release_file_lock(db, body.agent_id, body.path)
        return {"released": bool(lock), "path": body.path}
    except service.ConflictError as e:
        raise HTTPException(409, str(e))


# ------------------------------------------------- staged file changes ----

@router.get("/file-changes")
def list_file_changes(status: str = None, limit: int = 100, db: Session = Depends(get_db)):
    return review.list_changes(db, status=status, limit=limit)


@router.post("/file-changes/{cid}/accept")
def accept_file_change(cid: int, db: Session = Depends(get_db)):
    try:
        return review.accept_change(db, cid)
    except service.ConflictError as e:
        raise HTTPException(409, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.post("/file-changes/{cid}/reject")
def reject_file_change(cid: int, db: Session = Depends(get_db)):
    try:
        return review.reject_change(db, cid)
    except ValueError as e:
        raise HTTPException(400, str(e))


# ------------------------------------------------------------------ events ----

@router.get("/events")
def list_events(limit: int = 100, event_type: str = None, db: Session = Depends(get_db)):
    return [_event_out(e) for e in service.list_events(db, limit=limit, event_type=event_type)]


# ----------------------------------------------------------------- desktop ----

@router.get("/desktop")
def desktop_status(db: Session = Depends(get_db)):
    return {"desktop": desktop.status(), "lease": service.current_lease(db)}


@router.post("/desktop/start")
def desktop_start():
    return desktop.start()


@router.post("/desktop/stop")
def desktop_stop():
    return desktop.stop()


@router.post("/desktop/lease/acquire")
def lease_acquire(body: schemas.DesktopLeaseIn, db: Session = Depends(get_db)):
    agent = _agent_or_404(db, body.agent_id)
    _require_tool(agent, "desktop_control")
    return service.acquire_desktop_lease(db, body.agent_id, body.ttl_seconds)


@router.post("/desktop/lease/renew")
def lease_renew(body: schemas.DesktopLeaseIn, db: Session = Depends(get_db)):
    agent = _agent_or_404(db, body.agent_id)
    _require_tool(agent, "desktop_control")
    try:
        return service.renew_desktop_lease(db, body.agent_id, body.ttl_seconds)
    except ValueError as e:
        raise HTTPException(409, str(e))


@router.post("/desktop/lease/release")
def lease_release(body: schemas.DesktopReleaseIn, db: Session = Depends(get_db)):
    try:
        return service.release_desktop_lease(db, agent_id=body.agent_id,
                                             holder_type=body.holder_type)
    except ValueError as e:
        raise HTTPException(409, str(e))


@router.post("/desktop/lease/force-takeover")
def lease_force_takeover(db: Session = Depends(get_db)):
    return service.force_takeover_desktop(db)


# -------------------------------------------------------------- demo seed -----

_DEMO_AGENTS = [
    {"name": "产品经理", "role": "product_manager",
     "capability_tags": ["planning", "review"],
     "capability_scores": {"planning": 0.9, "review": 0.85},
     "tools": ["linux_file_read", "linux_file_list"]},
    {"name": "开发工程师", "role": "developer",
     "capability_tags": ["coding", "general"],
     "capability_scores": {"coding": 0.95, "general": 0.8},
     "tools": ["linux_exec", "linux_file_read", "linux_file_write", "linux_file_list",
               "desktop_control", "run_pytest"]},
    {"name": "测试工程师", "role": "tester",
     "capability_tags": ["review", "research"],
     "capability_scores": {"review": 0.92, "research": 0.8},
     "tools": ["linux_exec", "linux_file_read", "linux_file_list", "run_pytest",
               "desktop_control"]},
]


@router.post("/seed-demo", status_code=201)
def seed_demo(db: Session = Depends(get_db)):
    """Create (idempotently, by name) the three acceptance agents + one team.
    These are real Agent records used by the workspace; no fake results."""
    created = []
    agents = {}
    for spec in _DEMO_AGENTS:
        existing = db.query(models.Agent).filter_by(name=spec["name"]).first()
        if existing:
            # 旧行缺头像/形象时幂等补齐（与总管 ensure_team 共用同一份映射）。
            if backfill_seed_face(existing):
                db.flush()
            agents[spec["name"]] = existing
            continue
        a = models.Agent(name=spec["name"], role=spec["role"],
                         description="共享 Linux 电脑验收角色",
                         tools=spec["tools"], capability_tags=spec["capability_tags"],
                         capability_scores=spec["capability_scores"], status="active")
        backfill_seed_face(a)
        db.add(a)
        db.flush()
        agents[spec["name"]] = a
        created.append(a.id)
    team = db.query(models.Team).filter_by(name="Linux Workspace 协作团队").first()
    if team is None:
        team = models.Team(name="Linux Workspace 协作团队",
                           description="三名 AI 员工共用一台 Linux 电脑协作",
                           leader_agent_id=agents["产品经理"].id)
        db.add(team)
        db.flush()
        for name, role in (("产品经理", "leader"), ("开发工程师", "developer"),
                           ("测试工程师", "tester")):
            db.add(models.TeamMember(team_id=team.id, agent_id=agents[name].id,
                                     role_in_team=role, is_leader=(role == "leader")))
    # One commit for the whole seed: a failure midway must not leave a
    # half-initialised demo team behind.
    db.commit()
    for a in agents.values():
        db.refresh(a)
    if team is not None:
        db.refresh(team)
    service.emit_event(db, "demo_seeded", detail=f"演示团队已初始化：成员 {list(agents)} · 团队 {team.id}")
    return {"created_agent_ids": created,
            "agents": [{"id": a.id, "name": a.name, "role": a.role} for a in agents.values()],
            "team_id": team.id, "env_id": config.ENV_ID}
