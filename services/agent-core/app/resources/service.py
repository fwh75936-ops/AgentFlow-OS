"""Generic resource leases: same expiry/grant discipline as the desktop
lease, but for any resource (desktop/file/git/port/browser)."""
import threading
from datetime import datetime, timedelta
from app import models
from app.workspace import service as ws_service

# Check-then-insert is serialised process-wide: two concurrent acquires
# for the same resource must not both observe "free" and both insert a
# held lease.
_lock = threading.RLock()


def _clamp_ttl(ttl_seconds):
    try:
        ttl = int(ttl_seconds)
    except (TypeError, ValueError):
        raise ValueError(f"ttl_seconds 必须是整数：{ttl_seconds!r}")
    if not 1 <= ttl <= 86400:
        raise ValueError(f"ttl_seconds 必须在 1 到 86400 秒之间：{ttl}")
    return ttl

TYPES = {"desktop", "file", "git", "port", "browser"}

class ResourceConflict(Exception):
    pass

def _expire(db, lease):
    if lease and lease.status == "held" and lease.expires_at and lease.expires_at < datetime.utcnow():
        lease.status = "expired"; lease.released_at = datetime.utcnow(); db.commit()
        ws_service.emit_event(db, "resource_lease_expired", agent_id=lease.holder_agent_id,
                              task_id=lease.task_id, detail=f"{lease.resource_type}:{lease.resource_key}")
        return None
    return lease

def held_lease(db, resource_type, resource_key):
    lease = (db.query(models.ResourceLease).filter_by(
        resource_type=resource_type, resource_key=resource_key, status="held")
        .order_by(models.ResourceLease.id.desc()).first())
    return _expire(db, lease)

def conflict_risk(db, agent_id, resource_type=None, resource_key=None):
    """0.0 = no conflict for this agent, 1.0 = resource held by someone else."""
    if resource_key is None:
        return 0.0
    lease = held_lease(db, resource_type, resource_key)
    if lease is None or lease.holder_agent_id == agent_id:
        return 0.0
    return 1.0

def acquire(db, resource_type, resource_key, agent_id, task_id=None, ttl_seconds=300):
    with _lock:
        return _acquire_locked(db, resource_type, resource_key, agent_id,
                               task_id, ttl_seconds)


def _acquire_locked(db, resource_type, resource_key, agent_id, task_id=None, ttl_seconds=300):
    if resource_type not in TYPES:
        raise ValueError(f"未知的资源类型：{resource_type}")
    ttl_seconds = _clamp_ttl(ttl_seconds)
    lease = held_lease(db, resource_type, resource_key)
    if lease is not None:
        if lease.holder_agent_id == agent_id:
            lease.expires_at = datetime.utcnow() + timedelta(seconds=ttl_seconds); db.commit()
            return lease, False
        raise ResourceConflict(f"资源 {resource_type}：{resource_key} 已被员工 {lease.holder_agent_id} 占用")
    lease = models.ResourceLease(resource_type=resource_type, resource_key=resource_key,
        holder_agent_id=agent_id, task_id=task_id, status="held",
        acquired_at=datetime.utcnow(),
        expires_at=datetime.utcnow() + timedelta(seconds=ttl_seconds))
    db.add(lease); db.commit(); db.refresh(lease)
    ws_service.emit_event(db, "resource_lease_acquired", agent_id=agent_id, task_id=task_id,
                          detail=f"{resource_type}:{resource_key} lease #{lease.id}")
    return lease, True

def release(db, resource_type, resource_key, agent_id):
    # Same lock as acquire: an unlocked release/renew can interleave
    # with the expiry flip + re-acquire in held_lease and, e.g., renew
    # an already-expired lease someone else now holds.
    with _lock:
        lease = held_lease(db, resource_type, resource_key)
        if lease is None:
            return None
        if lease.holder_agent_id != agent_id:
            raise ResourceConflict(f"资源 {resource_type}：{resource_key} 已被员工 {lease.holder_agent_id} 占用")
        lease.status = "released"; lease.released_at = datetime.utcnow(); db.commit()
        ws_service.emit_event(db, "resource_lease_released", agent_id=agent_id,
                              detail=f"{resource_type}:{resource_key} lease #{lease.id}")
        return lease

def renew(db, resource_type, resource_key, agent_id, ttl_seconds=300):
    with _lock:
        ttl_seconds = _clamp_ttl(ttl_seconds)
        lease = held_lease(db, resource_type, resource_key)
        if lease is None or lease.holder_agent_id != agent_id:
            raise ValueError("该员工并未持有这项资源")
        lease.expires_at = datetime.utcnow() + timedelta(seconds=ttl_seconds); db.commit()
        return lease

def list_leases(db, status=None):
    q = db.query(models.ResourceLease)
    if status: q = q.filter_by(status=status)
    out = []
    for lease in q.order_by(models.ResourceLease.id.desc()).limit(200).all():
        if _expire(db, lease) is None and status == "held":
            continue
        out.append(lease)
    return out

def out(lease):
    return {"id": lease.id, "resource_type": lease.resource_type, "resource_key": lease.resource_key,
            "holder_agent_id": lease.holder_agent_id, "task_id": lease.task_id, "status": lease.status,
            "acquired_at": lease.acquired_at.isoformat() if lease.acquired_at else None,
            "expires_at": lease.expires_at.isoformat() if lease.expires_at else None}
