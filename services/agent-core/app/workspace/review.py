"""Staged file-change review for agent tool writes.

An agent calling the linux_file_write tool no longer writes the shared
file directly: the change is staged here as a PendingFileChange holding
the original content, the proposed content and a unified diff. The user
reviews staged changes on the AI 电脑 page; only an explicit accept
replays the write through service.write_file (with the staged base
version as expected_version, so a file that moved on in the meantime
conflicts honestly instead of being silently overwritten). Rejecting
discards the change. Both decisions emit workspace events, so the audit
trail shows the full lifecycle.
"""
import difflib
import os
from datetime import datetime

from app import models
from app.workspace import config, service

_DIFF_CHAR_LIMIT = 20000
_READ_LIMIT_BYTES = 10 * 1024 * 1024  # same cap as service.read_file


def _read_current(db, path):
    """(exists, content, version) for the shared file right now."""
    root = config.get_shared_root()
    full = service._confine(root, path)
    meta = db.query(models.SharedFileMeta).get(path)
    if not os.path.isfile(full):
        return False, None, (meta.version if meta else None)
    # Unbounded read: staging a change for a huge file would load it
    # whole into memory (read_file refuses over this same limit).
    if os.path.getsize(full) > _READ_LIMIT_BYTES:
        raise ValueError(f"文件过大，无法审阅：{path}（上限 10MB）")
    with open(full, "rb") as f:
        content = f.read().decode("utf-8", errors="replace")
    return True, content, (meta.version if meta else None)


def stage_change(db, agent_id, path, content, task_id=None):
    """Stage an agent's proposed write. Returns the created row as a dict.
    A still-pending earlier change for the same path is superseded, so the
    review queue always shows the latest proposal per file."""
    _exists, original, base_version = _read_current(db, path)
    new_content = content or ""
    old_lines = (original or "").splitlines()
    new_lines = new_content.splitlines()
    diff_lines = list(difflib.unified_diff(
        old_lines, new_lines, fromfile=f"当前内容 {path}", tofile=f"待审变更 {path}",
        lineterm=""))
    added = sum(1 for l in diff_lines if l.startswith("+") and not l.startswith("+++"))
    removed = sum(1 for l in diff_lines if l.startswith("-") and not l.startswith("---"))
    diff_text = "\n".join(diff_lines)[:_DIFF_CHAR_LIMIT]
    if not diff_lines and (original or "") != new_content:
        # splitlines() hides newline-only changes (trailing newline,
        # CRLF<->LF): the contents DO differ, so the review must not
        # present an empty diff as "no change".
        diff_text = "（内容有变化：换行符或文件末尾换行不同，逐行对比无差异）"

    for old in (db.query(models.PendingFileChange)
                .filter_by(path=path, status="pending").all()):
        old.status = "superseded"
        old.decided_at = datetime.utcnow()
    row = models.PendingFileChange(
        path=path, agent_id=agent_id, task_id=task_id,
        base_version=base_version, original_content=original,
        new_content=new_content,
        diff_summary={"added_lines": added, "removed_lines": removed},
        diff_text=diff_text, status="pending")
    db.add(row)
    db.commit()
    db.refresh(row)
    service.emit_event(db, "file_change_staged", agent_id=agent_id,
                       task_id=task_id, path=path,
                       detail=f"提交待审变更：新增 {added} 行 · 删除 {removed} 行")
    return change_out(row)


def change_out(row):
    return {"id": row.id, "path": row.path, "agent_id": row.agent_id,
            "task_id": row.task_id, "base_version": row.base_version,
            "original_content": row.original_content,
            "new_content": row.new_content,
            "diff_summary": row.diff_summary or {},
            "diff_text": row.diff_text or "",
            "status": row.status,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "decided_at": row.decided_at.isoformat() if row.decided_at else None}


def list_changes(db, status=None, limit=100):
    q = db.query(models.PendingFileChange)
    if status:
        q = q.filter_by(status=status)
    rows = q.order_by(models.PendingFileChange.id.desc()).limit(limit).all()
    return [change_out(r) for r in rows]


def _get_pending(db, change_id):
    row = db.query(models.PendingFileChange).get(change_id)
    if row is None:
        raise ValueError(f"待审变更 {change_id} 不存在")
    if row.status != "pending":
        raise ValueError(f"这条变更已经处理过了，当前状态：{row.status}")
    return row


def accept_change(db, change_id):
    """Replay the staged write through the normal versioned write path.
    A version conflict (file changed since staging) propagates as
    service.ConflictError and the change stays pending."""
    row = _get_pending(db, change_id)
    result = service.write_file(db, row.agent_id, row.path, row.new_content,
                                 expected_version=row.base_version,
                                 task_id=row.task_id)
    row.status = "accepted"
    row.decided_at = datetime.utcnow()
    db.commit()
    service.emit_event(db, "file_change_accepted", agent_id=row.agent_id,
                       task_id=row.task_id, path=row.path,
                       detail=f"变更已接受并写入：第 {result['version']} 版")
    return {"change": change_out(row), "write": result}


def reject_change(db, change_id):
    row = _get_pending(db, change_id)
    row.status = "rejected"
    row.decided_at = datetime.utcnow()
    db.commit()
    service.emit_event(db, "file_change_rejected", agent_id=row.agent_id,
                       task_id=row.task_id, path=row.path,
                       detail="变更被用户拒绝，文件未做任何修改")
    return {"change": change_out(row)}
