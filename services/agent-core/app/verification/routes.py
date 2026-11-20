from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.database import get_db
from app.verification import reviewer, state

router = APIRouter(prefix="/api/artifact-verifications", tags=["artifact-verifications"])

@router.post("", status_code=201)
def record(body: dict, db: Session = Depends(get_db)):
    if not body.get("artifact_path"):
        raise HTTPException(400, "缺少成果路径")
    if not isinstance(body.get("artifact_path"), str):
        raise HTTPException(422, "artifact_path 必须是字符串")
    if not isinstance(body.get("passed"), bool):
        # bool("false") is True in Python — a failed result sent as a
        # string must never be stored as a pass.
        raise HTTPException(422, "passed 必须是布尔值")
    # The remaining fields are stored verbatim: a dict evidence cannot
    # be sliced (500), and non-int ids / a non-string test_run_id would
    # be stored with the wrong type in their columns.
    for key in ("task_id", "node_id"):
        if body.get(key) is not None and not isinstance(body.get(key), int):
            raise HTTPException(422, f"{key} 必须是整数")
    if body.get("test_run_id") is not None and not isinstance(body.get("test_run_id"), str):
        raise HTTPException(422, "test_run_id 必须是字符串")
    if body.get("evidence") is not None and not isinstance(body.get("evidence"), str):
        raise HTTPException(422, "evidence 必须是字符串")
    try:
        rec = state.record_verification(db, body["artifact_path"], body["passed"],
            body.get("test_run_id"), body.get("task_id"), body.get("node_id"), body.get("evidence", ""))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return state.out(rec)

@router.get("")
def listing(artifact_path: str = None, db: Session = Depends(get_db)):
    return state.list_verifications(db, artifact_path)

@router.get("/current")
def current(artifact_path: str, db: Session = Depends(get_db)):
    rec = state.current_verification(db, artifact_path)
    return {"artifact_path": artifact_path, "verified": bool(rec and rec.passed),
            "verification": state.out(rec)}

@router.post("/reviews", status_code=201)
def create_review(body: dict, db: Session = Depends(get_db)):
    if not body.get("artifact_path") or not body.get("reviewer_agent_id"):
        raise HTTPException(400, "缺少成果路径或评审员工")
    try:
        reviewer_id = int(body["reviewer_agent_id"])
    except (TypeError, ValueError):
        raise HTTPException(422, "reviewer_agent_id 必须是整数")
    try:
        r = reviewer.review_artifact(
            db, body["artifact_path"], reviewer_id,
            author_agent_id=body.get("author_agent_id"), checks=body.get("checks"),
            comment=body.get("comment", ""), task_id=body.get("task_id"))
    except reviewer.ReviewError as e:
        raise HTTPException(400, str(e))
    return reviewer.out(r)

@router.get("/reviews")
def reviews(artifact_path: str = None, db: Session = Depends(get_db)):
    return reviewer.list_reviews(db, artifact_path)

@router.get("/reviews/current")
def current_review(artifact_path: str, db: Session = Depends(get_db)):
    r = reviewer.current_review(db, artifact_path)
    return {"artifact_path": artifact_path,
            "review_approved": bool(r and r.passed), "review": reviewer.out(r)}

@router.post("/check-freshness")
def freshness(body: dict = None, db: Session = Depends(get_db)):
    return {"flipped_to_stale": state.check_freshness(db, (body or {}).get("artifact_path"))}
