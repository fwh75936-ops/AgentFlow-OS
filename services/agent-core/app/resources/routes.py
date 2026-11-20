from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app import models
from app.database import get_db
from app.resources import service

router = APIRouter(prefix="/api/resources", tags=["resources"])


def _agent_or_404(db, agent_id):
    if db.query(models.Agent).get(agent_id) is None:
        raise HTTPException(404, f"员工 {agent_id} 不存在")


@router.get("/leases")
def leases(status: str = None, db: Session = Depends(get_db)):
    return [service.out(l) for l in service.list_leases(db, status=status)]

@router.post("/leases/acquire", status_code=201)
def acquire(body: dict, db: Session = Depends(get_db)):
    try:
        agent_id = int(body["agent_id"])
    except (ValueError, TypeError, KeyError) as e:
        raise HTTPException(400, str(e))
    # A lease held by a nonexistent agent would block the resource
    # until expiry with nobody able to release or renew it.
    _agent_or_404(db, agent_id)
    try:
        lease, _new = service.acquire(db, body["resource_type"], body["resource_key"],
            agent_id, body.get("task_id"), int(body.get("ttl_seconds", 300)))
        return service.out(lease)
    except service.ResourceConflict as e:
        raise HTTPException(409, str(e))
    except (ValueError, TypeError, KeyError) as e:
        raise HTTPException(400, str(e))

@router.post("/leases/release")
def release(body: dict, db: Session = Depends(get_db)):
    try:
        lease = service.release(db, body["resource_type"], body["resource_key"], int(body["agent_id"]))
        return {"released": lease.id if lease else None}
    except service.ResourceConflict as e:
        raise HTTPException(409, str(e))
    except (ValueError, TypeError, KeyError) as e:
        raise HTTPException(400, str(e))

@router.post("/leases/renew")
def renew(body: dict, db: Session = Depends(get_db)):
    try:
        lease = service.renew(db, body["resource_type"], body["resource_key"],
                              int(body["agent_id"]), int(body.get("ttl_seconds", 300)))
        return service.out(lease)
    except (ValueError, TypeError, KeyError) as e:
        raise HTTPException(400, str(e))

@router.get("/conflict")
def conflict(agent_id: int, resource_type: str, resource_key: str, db: Session = Depends(get_db)):
    return {"agent_id": agent_id, "resource_type": resource_type, "resource_key": resource_key,
            "conflict_risk": service.conflict_risk(db, agent_id, resource_type, resource_key)}
