"""Proactive engine L1/L2 (default): workspace events -> suggestions.
Nothing executes before approval. L4 (high-risk) actions additionally require
explicit human approval and can never be auto-executed."""
import threading
from datetime import datetime
from app import models

# Scan de-duplication is check-then-insert; serialise scans so two
# concurrent scans cannot both create the same suggestion.
_scan_lock = threading.Lock()

EVENT_TITLES = {"test_failed": "检测到测试失败", "build_failed": "检测到构建失败",
                "file_changed": "检测到文件变化", "task_timeout": "检测到任务超时"}

def ensure_default_rules(db):
    defaults = [("测试失败建议修复", "test_failed", "L2", "分析失败原因并尝试修复"),
                ("构建失败建议修复", "build_failed", "L2", "分析构建失败并尝试修复"),
                ("文件变化提醒", "file_changed", "L1", ""),
                ("任务超时提醒", "task_timeout", "L1", "")]
    for name, et, lvl, act in defaults:
        if not db.query(models.ProactiveRule).filter_by(event_type=et, name=name).first():
            db.add(models.ProactiveRule(name=name, event_type=et, level=lvl, action=act, enabled=True))
    db.commit()

def scan_events(db, limit=100):
    """Create one suggestion per (rule, event) not already suggested."""
    with _scan_lock:
        return _scan_events_locked(db, limit)


def _scan_events_locked(db, limit=100):
    ensure_default_rules(db)
    rules = db.query(models.ProactiveRule).filter_by(enabled=True).all()
    created = []
    events = (db.query(models.WorkspaceEvent)
              .order_by(models.WorkspaceEvent.id.desc()).limit(limit).all())
    for ev in events:
        for rule in rules:
            if rule.event_type != ev.event_type:
                continue
            dup = db.query(models.ProactiveSuggestion).filter_by(
                rule_id=rule.id, source_event_id=ev.id).first()
            if dup: continue
            s = models.ProactiveSuggestion(
                rule_id=rule.id, source_event_id=ev.id, event_type=ev.event_type,
                level=rule.level, title=EVENT_TITLES.get(ev.event_type, ev.event_type),
                detail=ev.detail or "", action=rule.action, status="pending")
            db.add(s); db.commit(); db.refresh(s); created.append(s)
    return created

_SUGGESTION_STATUS_ZH = {
    "pending": "待处理", "approved": "已批准",
    "rejected": "已拒绝", "executed": "已执行",
}


def _suggestion_status_zh(status):
    return _SUGGESTION_STATUS_ZH.get(status, status)


def _transition(db, sid, from_status, to_status, decided=False):
    """State change as ONE conditional UPDATE: when two requests race,
    only the first matches the WHERE clause; the loser learns the state
    has already moved instead of silently overwriting it."""
    values = {"status": to_status}
    if decided:
        values["decided_at"] = datetime.utcnow()
    n = (db.query(models.ProactiveSuggestion)
         .filter(models.ProactiveSuggestion.id == sid,
                 models.ProactiveSuggestion.status == from_status)
         .update(values, synchronize_session=False))
    db.commit()
    s = db.query(models.ProactiveSuggestion).get(sid)
    if s is None:
        raise ValueError(f"建议 {sid} 不存在")
    if n != 1:
        raise ValueError(
            f"建议 {sid} 当前状态为「{_suggestion_status_zh(s.status)}」，不能重复处理")
    return s


def approve(db, sid):
    return _transition(db, sid, "pending", "approved", decided=True)

def reject(db, sid):
    return _transition(db, sid, "pending", "rejected", decided=True)

def execute_suggestion(db, sid):
    """The ONLY execution path, and it refuses unless approved. L4 always
    requires the approval recorded here; there is no prompt-level bypass."""
    s = db.query(models.ProactiveSuggestion).get(sid)
    if s is None: raise ValueError(f"建议 {sid} 不存在")
    if s.status != "approved":
        raise PermissionError(
            f"建议 {sid} 当前状态为「{_suggestion_status_zh(s.status)}」，需要先批准才能执行")
    s = _transition(db, sid, "approved", "executed")
    return {"suggestion_id": s.id, "status": s.status, "action": s.action,
            "note": "marked executed after approval; the linked repair itself runs through the normal task/Self-Loop path"}

def out(s):
    return {"id": s.id, "rule_id": s.rule_id, "source_event_id": s.source_event_id,
            "event_type": s.event_type, "level": s.level, "title": s.title,
            "detail": s.detail, "action": s.action, "status": s.status,
            "created_at": s.created_at.isoformat() if s.created_at else None}
