"""State-aware verification on top of SharedFileMeta (version + sha256).

A verification is only *current* while the artifact still matches the
snapshotted version AND hash. Any later write bumps the version and changes
the hash, which flips older records to stale — a stale record must never be
reported as verifying the current artifact.
"""
from app import models

def _meta(db, path):
    return db.query(models.SharedFileMeta).get(path)

def record_verification(db, artifact_path, passed, test_run_id=None, task_id=None,
                          node_id=None, evidence=""):
    meta = _meta(db, artifact_path)
    if meta is None:
        # A verification snapshot needs a real artifact to bind to.
        # Recording (None, "") used to match the equally-empty freshness
        # probe forever, so a nonexistent file could report verified.
        raise ValueError(f"成果没有登记记录，不能记录验证：{artifact_path}")
    # A newer verification supersedes older ones for the same artifact
    # version: leaving two contradictory "current" rows (one passed,
    # one failed) makes the list view self-contradictory.
    (db.query(models.ArtifactVerification)
     .filter_by(artifact_path=artifact_path, status="current")
     .update({"status": "superseded"}, synchronize_session=False))
    rec = models.ArtifactVerification(
        artifact_path=artifact_path,
        artifact_version=meta.version,
        source_hash=meta.content_hash,
        test_run_id=test_run_id, task_id=task_id, node_id=node_id,
        passed=bool(passed), status="current", evidence=(evidence or "")[:2000])
    db.add(rec); db.commit(); db.refresh(rec)
    return rec

def check_freshness(db, artifact_path=None):
    """Flip records whose snapshot no longer matches the file to stale.
    Returns the number of records flipped."""
    q = db.query(models.ArtifactVerification).filter_by(status="current")
    if artifact_path: q = q.filter_by(artifact_path=artifact_path)
    from app.workspace import service as ws_service
    flipped = 0
    for rec in q.all():
        meta = _meta(db, rec.artifact_path)
        cur_v = meta.version if meta else None
        # Probe the REAL file, not only its metadata row: a terminal
        # write changes the bytes without touching SharedFileMeta, and
        # the old probe kept such records "current" forever.
        disk_h = ws_service.file_hash_on_disk(rec.artifact_path)
        if meta is None or cur_v != rec.artifact_version or disk_h != rec.source_hash:
            rec.status = "stale"; flipped += 1
    if flipped: db.commit()
    # Reviewer cross-verifications share the same version/hash snapshot:
    # an artifact change must stale them in the same pass.
    from app.verification import reviewer
    flipped += reviewer.check_review_freshness(db, artifact_path)
    return flipped

def current_verification(db, artifact_path):
    """The latest verification that is still valid for the CURRENT file
    version, or None. Stale records are refreshed first."""
    check_freshness(db, artifact_path)
    return (db.query(models.ArtifactVerification)
            .filter_by(artifact_path=artifact_path, status="current")
            .order_by(models.ArtifactVerification.id.desc()).first())

def is_verified(db, artifact_path):
    rec = current_verification(db, artifact_path)
    return bool(rec and rec.passed)

def out(rec):
    if rec is None: return None
    return {"id": rec.id, "artifact_path": rec.artifact_path,
            "artifact_version": rec.artifact_version, "source_hash": rec.source_hash,
            "test_run_id": rec.test_run_id, "task_id": rec.task_id, "node_id": rec.node_id,
            "passed": rec.passed, "status": rec.status, "evidence": rec.evidence,
            "created_at": rec.created_at.isoformat() if rec.created_at else None}

def list_verifications(db, artifact_path=None):
    check_freshness(db, artifact_path)
    q = db.query(models.ArtifactVerification)
    if artifact_path: q = q.filter_by(artifact_path=artifact_path)
    return [out(r) for r in q.order_by(models.ArtifactVerification.id.desc()).limit(200).all()]
