from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app import models
from app.database import get_db
from app.proactive import service

router = APIRouter(prefix="/api/proactive", tags=["proactive"])

# A missing suggestion is a 404: the service raises ValueError("建议 … 不存在")
# for it, which the routes below map to 400 — the wrong code
# for a resource that does not exist (an already-decided suggestion
# still returns 400 via the same ValueError path).
def _suggestion_or_404(db, sid):
    if db.query(models.ProactiveSuggestion).get(sid) is None:
        raise HTTPException(404, f"建议 {sid} 不存在")

_RULE_LEVELS = ("L1", "L2", "L4")

def _validate_rule_body(body):
    # enabled is a Boolean column: a string "false" crashes at flush
    # (TypeError -> 500) instead of disabling the rule; an unknown
    # level would be stored silently and never match any scan.
    if "enabled" in body and not isinstance(body["enabled"], bool):
        raise HTTPException(422, "enabled 必须是布尔值")
    if "level" in body and body["level"] not in _RULE_LEVELS:
        raise HTTPException(422, f"level 只能是 {', '.join(_RULE_LEVELS)}")

@router.get("/rules")
def rules(db: Session = Depends(get_db)):
    service.ensure_default_rules(db)
    return [{"id": r.id, "name": r.name, "event_type": r.event_type, "level": r.level,
             "action": r.action, "enabled": r.enabled}
            for r in db.query(models.ProactiveRule).order_by(models.ProactiveRule.id).all()]

@router.post("/rules", status_code=201)
def create_rule(body: dict, db: Session = Depends(get_db)):
    if not body.get("event_type"):
        raise HTTPException(422, "event_type 是必填项")
    _validate_rule_body(body)
    r = models.ProactiveRule(name=body.get("name", "rule"), event_type=body["event_type"],
                             level=body.get("level", "L2"), action=body.get("action", ""),
                             enabled=body.get("enabled", True))
    db.add(r); db.commit(); db.refresh(r)
    return {"id": r.id, "event_type": r.event_type, "level": r.level}

@router.patch("/rules/{rid}")
def patch_rule(rid: int, body: dict, db: Session = Depends(get_db)):
    r = db.query(models.ProactiveRule).get(rid)
    if r is None: raise HTTPException(404, "规则不存在")
    _validate_rule_body(body)
    for k in ("name", "level", "action", "enabled"):
        if k in body: setattr(r, k, body[k])
    db.commit(); return {"id": r.id, "enabled": r.enabled}

@router.delete("/rules/{rid}")
def delete_rule(rid: int, db: Session = Depends(get_db)):
    r = db.query(models.ProactiveRule).get(rid)
    if r is None: raise HTTPException(404, "规则不存在")
    db.delete(r); db.commit(); return {"deleted": rid}

@router.get("/suggestions")
def suggestions(status: str = None, db: Session = Depends(get_db)):
    q = db.query(models.ProactiveSuggestion)
    if status: q = q.filter_by(status=status)
    return [service.out(s) for s in q.order_by(models.ProactiveSuggestion.id.desc()).limit(200).all()]

@router.post("/scan")
def scan(db: Session = Depends(get_db)):
    return {"created": [service.out(s) for s in service.scan_events(db)]}

@router.post("/suggestions/{sid}/approve")
def approve_s(sid: int, db: Session = Depends(get_db)):
    _suggestion_or_404(db, sid)
    try: return service.out(service.approve(db, sid))
    except ValueError as e: raise HTTPException(400, str(e))

@router.post("/suggestions/{sid}/reject")
def reject_s(sid: int, db: Session = Depends(get_db)):
    _suggestion_or_404(db, sid)
    try: return service.out(service.reject(db, sid))
    except ValueError as e: raise HTTPException(400, str(e))

@router.post("/suggestions/{sid}/execute")
def execute_s(sid: int, db: Session = Depends(get_db)):
    _suggestion_or_404(db, sid)
    try: return service.execute_suggestion(db, sid)
    except PermissionError as e: raise HTTPException(403, str(e))
    except ValueError as e: raise HTTPException(400, str(e))
