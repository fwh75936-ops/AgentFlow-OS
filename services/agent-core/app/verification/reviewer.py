"""Reviewer cross-verification — verification layer 4 (original implementation).

An independent Agent holding the 'review' capability re-checks another
Agent's artifact with its own rule checks (contains / not_contains /
min_length / max_length over the real file content). Independence is
enforced: reviewer != author, and the reviewer must carry the 'review'
capability tag. No LLM is involved; the verdict is the deterministic
outcome of the declared checks, and every review writes BOTH a
ReviewVerification row (version/hash snapshot, stale-aware) and a
VerificationRecord of type 'reviewer' whose evidence JSON names the
reviewer_agent_id and the reviewed artifact version.
"""
import json

from app import models
from app.workspace import service as ws


class ReviewError(Exception):
    pass


def _run_checks(content, checks):
    results = []
    for c in checks or []:
        # A bare string iterates per character and a non-dict entry has
        # no .get — both used to crash with AttributeError (500) instead
        # of a 400 for a malformed checks payload.
        if not isinstance(c, dict):
            raise ReviewError(f"评审检查项必须是对象：{c!r}")
        ctype, value = c.get("type"), c.get("value")
        if ctype == "contains":
            ok = str(value) in content
        elif ctype == "not_contains":
            ok = str(value) not in content
        elif ctype == "min_length":
            try:
                ok = len(content) >= int(value)
            except (TypeError, ValueError):
                raise ReviewError(f"min_length 的值无效：{value!r}")
        elif ctype == "max_length":
            try:
                ok = len(content) <= int(value)
            except (TypeError, ValueError):
                raise ReviewError(f"max_length 的值无效：{value!r}")
        elif ctype == "nonempty":
            ok = len(content.strip()) > 0
        else:
            raise ReviewError(f"unknown review check type: {ctype!r}")
        results.append({"type": ctype, "value": value, "passed": bool(ok)})
    return results


def review_artifact(db, artifact_path, reviewer_agent_id, author_agent_id=None,
                    checks=None, comment="", task_id=None):
    reviewer = db.query(models.Agent).get(reviewer_agent_id)
    if reviewer is None:
        raise ReviewError(f"reviewer agent not found: {reviewer_agent_id}")
    if reviewer.status != "active":
        raise ReviewError(f"agent {reviewer_agent_id} is not active and cannot review")
    if "review" not in (reviewer.capability_tags or []):
        raise ReviewError(f"agent {reviewer_agent_id} lacks the 'review' capability")
    # Types the route does not validate: a non-int author id used to
    # crash on int() (500), a non-int task id would be stored as TEXT
    # in an Integer column, and a non-string comment cannot be bound
    # to a Text column at all (500). All three are caller errors.
    if author_agent_id is not None:
        try:
            author_agent_id = int(author_agent_id)
        except (TypeError, ValueError):
            raise ReviewError(f"author_agent_id 必须是整数：{author_agent_id!r}")
    if task_id is not None and not isinstance(task_id, int):
        raise ReviewError(f"task_id 必须是整数：{task_id!r}")
    if not isinstance(comment, str):
        raise ReviewError("comment 必须是字符串")
    if checks is not None and not isinstance(checks, list):
        raise ReviewError("checks 必须是数组")
    # Independence is checked against the artifact's REAL author — the
    # agent recorded on the file metadata at write time — not merely a
    # caller-supplied claim that can be omitted to dodge the check.
    meta = db.query(models.SharedFileMeta).get(artifact_path)
    if meta is not None and meta.last_modified_by_agent_id is not None:
        author_agent_id = meta.last_modified_by_agent_id
    if author_agent_id is not None and int(author_agent_id) == int(reviewer_agent_id):
        raise ReviewError("reviewer must be independent from the author (same agent)")
    try:
        f = ws.read_file(db, artifact_path)
    except ValueError as e:
        raise ReviewError(str(e))
    results = _run_checks(f["content"], checks or [{"type": "nonempty"}])
    passed = all(r["passed"] for r in results)
    evidence = {
        "reviewer_agent_id": reviewer_agent_id,
        "author_agent_id": author_agent_id,
        "artifact_path": artifact_path,
        "artifact_version": f["version"],
        "source_hash": f["content_hash"],
        "checks": results,
        "comment": comment,
        "method": "rule-based independent re-check (no LLM)",
    }
    rec = models.VerificationRecord(
        task_id=task_id, verification_type="reviewer", passed=passed,
        score=1.0 if passed else 0.0,
        evidence=json.dumps(evidence, ensure_ascii=False),
        errors=[f"check failed: {r['type']} {r['value']!r}" for r in results if not r["passed"]])
    db.add(rec); db.flush()
    review = models.ReviewVerification(
        artifact_path=artifact_path, artifact_version=f["version"],
        source_hash=f["content_hash"], reviewer_agent_id=reviewer_agent_id,
        author_agent_id=author_agent_id, task_id=task_id, passed=passed,
        status="current", checks=results, comment=comment,
        verification_record_id=rec.verification_id)
    db.add(review); db.commit(); db.refresh(review)
    return review


def check_review_freshness(db, artifact_path=None):
    """Flip reviews whose artifact version/hash moved on to stale.
    Called from state.check_freshness so one freshness pass covers both."""
    q = db.query(models.ReviewVerification).filter_by(status="current")
    if artifact_path:
        q = q.filter_by(artifact_path=artifact_path)
    flipped = 0
    for r in q.all():
        meta = db.query(models.SharedFileMeta).get(r.artifact_path)
        cur_v = meta.version if meta else None
        # Probe the real file's hash (same rule as state.check_freshness):
        # terminal writes bypass SharedFileMeta, and for artifacts with
        # no meta row at all the old probe compared against "" — which
        # flipped every such review to stale on its first check even
        # though the snapshot stored the file's true hash.
        disk_h = ws.file_hash_on_disk(r.artifact_path)
        if cur_v != r.artifact_version or disk_h != r.source_hash:
            r.status = "stale"; flipped += 1
    if flipped:
        db.commit()
    return flipped


def current_review(db, artifact_path):
    check_review_freshness(db, artifact_path)
    return (db.query(models.ReviewVerification)
            .filter_by(artifact_path=artifact_path, status="current")
            .order_by(models.ReviewVerification.id.desc()).first())


def out(r):
    if r is None:
        return None
    return {"id": r.id, "artifact_path": r.artifact_path,
            "artifact_version": r.artifact_version, "source_hash": r.source_hash,
            "reviewer_agent_id": r.reviewer_agent_id,
            "author_agent_id": r.author_agent_id, "task_id": r.task_id,
            "passed": r.passed, "status": r.status, "checks": r.checks,
            "comment": r.comment, "verification_record_id": r.verification_record_id,
            "created_at": r.created_at.isoformat() if r.created_at else None}


def list_reviews(db, artifact_path=None):
    check_review_freshness(db, artifact_path)
    q = db.query(models.ReviewVerification)
    if artifact_path:
        q = q.filter_by(artifact_path=artifact_path)
    return [out(r) for r in q.order_by(models.ReviewVerification.id.desc()).limit(200).all()]
