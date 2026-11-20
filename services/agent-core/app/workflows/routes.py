from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.database import get_db
from app import models
from app.workflows import engine

router = APIRouter(prefix="/api/workflows", tags=["workflow-execution"])


@router.post("/{workflow_id}/execute", status_code=201)
def execute(workflow_id: int, body: dict = None, db: Session = Depends(get_db)):
    body = body or {}
    if body.get("title") is not None and not isinstance(body.get("title"), str):
        raise HTTPException(422, "title 必须是字符串")
    try:
        max_retries = int(body.get("max_retries", 2))
    except (TypeError, ValueError):
        raise HTTPException(422, "max_retries 必须是整数")
    if not 0 <= max_retries <= 10:
        raise HTTPException(422, "max_retries 必须在 0 到 10 之间")
    approvals = body.get("approvals") or {}
    if not isinstance(approvals, dict):
        raise HTTPException(422, "approvals 必须是对象")
    try:
        run = engine.execute_workflow(db, workflow_id, title=body.get("title"),
                                      # Default aligned with run_task (task path):
                                      # workflow runs get Self-Loop retries too.
                                      max_retries=max_retries,
                                      approvals=approvals)
    except engine.WorkflowError as e:
        raise HTTPException(400, str(e))
    return engine.run_out(run)


@router.get("/runs/list")
def list_runs(workflow_id: int = None, db: Session = Depends(get_db)):
    q = db.query(models.WorkflowRun)
    if workflow_id is not None:
        q = q.filter_by(workflow_id=workflow_id)
    return [engine.run_out(r) for r in
            q.order_by(models.WorkflowRun.id.desc()).limit(100).all()]
