from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from app.database import get_db
from app.computer_use import service

router = APIRouter(prefix="/api/computer-use", tags=["computer-use"])

@router.get("/observe")
def observe(db: Session = Depends(get_db)):
    return service.observe(db)

def _agent_id(body: dict) -> int:
    try:
        return int(body["agent_id"])
    except (KeyError, TypeError, ValueError):
        from fastapi import HTTPException
        raise HTTPException(422, "agent_id 必须是整数")


@router.post("/act")
def act(body: dict, db: Session = Depends(get_db)):
    # approved must be a real boolean: bool("false") is True, and this
    # flag is what parks high-risk key chords for human confirmation.
    approved = body.get("approved", False)
    if not isinstance(approved, bool):
        from fastapi import HTTPException
        raise HTTPException(422, "approved 必须是布尔值")
    return service.act(db, _agent_id(body), body.get("action", "click"),
                       body.get("args"), approved=approved)

@router.post("/cycle")
def cycle(body: dict, db: Session = Depends(get_db)):
    return service.run_cycle(db, _agent_id(body), body.get("action", "click"), body.get("args"))
