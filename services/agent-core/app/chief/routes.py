from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app import models
from app.database import get_db
from app.chief import service

router = APIRouter(prefix="/api/chief", tags=["chief"])

@router.post("/plan")
def plan(body: dict):
    goal = body.get("goal", "")
    if not isinstance(goal, str):
        raise HTTPException(422, "goal 必须是字符串")
    return service.plan_goal(goal)

@router.post("/missions", status_code=201)
def create(body: dict, db: Session = Depends(get_db)):
    if not isinstance(body.get("goal"), str) or not body["goal"].strip():
        raise HTTPException(400, "goal 不能为空")
    run = body.get("run", True)
    if not isinstance(run, bool):
        # "false" as a string must not silently start an execution.
        raise HTTPException(422, "run 必须是布尔值")
    m = service.create_mission(db, body["goal"], run=run)
    return {"mission_id": m.id, "status": m.status, "task_id": m.task_id,
            "team_id": m.team_id, "llm_called": False, "report": m.report}

@router.get("/missions/{mid}/report")
def report(mid: int, db: Session = Depends(get_db)):
    m = db.query(models.ChiefMission).get(mid)
    if m is None:
        raise HTTPException(404, "总管任务不存在")
    return m.report or service.build_report(db, m)

@router.post("/handoffs", status_code=201)
def handoff(body: dict, db: Session = Depends(get_db)):
    from_id = body.get("from_agent_id")
    to_id = body.get("to_agent_id")
    if not isinstance(from_id, int) or not isinstance(to_id, int):
        raise HTTPException(422, "from_agent_id 与 to_agent_id 必须是整数")
    if (db.query(models.Agent).get(from_id) is None
            or db.query(models.Agent).get(to_id) is None):
        raise HTTPException(404, "交接的员工不存在")
    task_id = body.get("task_id")
    if task_id is not None:
        if not isinstance(task_id, int):
            raise HTTPException(422, "task_id 必须是整数")
        if db.query(models.Task).get(task_id) is None:
            raise HTTPException(404, f"任务 {task_id} 不存在")
    objective = body.get("objective", "")
    if not isinstance(objective, str):
        raise HTTPException(422, "objective 必须是字符串")
    # The service does list(...) on these: a null would raise TypeError
    # (500) and a bare string would silently store one character per
    # list entry. Reject both up front.
    lists = {}
    for key in ("completedWork", "artifactPaths", "pendingIssues",
                "verificationRequirements"):
        v = body.get(key)
        if v is None:
            lists[key] = []
        elif isinstance(v, list) and all(isinstance(x, str) for x in v):
            lists[key] = v
        else:
            raise HTTPException(422, f"{key} 必须是字符串数组")
    msg = service.create_handoff(db, task_id, from_id, to_id, objective,
        lists["completedWork"], lists["artifactPaths"],
        lists["pendingIssues"], lists["verificationRequirements"])
    return {"message_id": msg.message_id, "meta": msg.meta}

@router.post("/handoffs/{message_id}/accept")
def accept(message_id: str, db: Session = Depends(get_db)):
    try:
        return service.accept_handoff(db, message_id)
    except ValueError as e:
        raise HTTPException(409, str(e))
