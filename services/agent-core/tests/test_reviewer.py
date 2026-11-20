"""Reviewer cross-verification (verification layer 4) tests.

Covers: approve path, reject path, independence enforcement, capability
enforcement, the linked VerificationRecord (type='reviewer', evidence
names reviewer_agent_id + artifact version), and stale linkage — after
the author writes a new version, check_freshness flips the review too.
"""
import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models
from app.database import Base
from app.verification import reviewer, state as vs
from app.workspace import service as ws


@pytest.fixture()
def dbx(tmp_path, monkeypatch):
    root = tmp_path / "shared"; root.mkdir()
    monkeypatch.setenv("AGENTFLOW_SHARED_ROOT", str(root))
    engine = create_engine("sqlite:///" + str(tmp_path / "rev.db"),
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    db = sessionmaker(bind=engine, autocommit=False, autoflush=False)()
    yield db
    db.close()


def mkagent(db, name, caps):
    a = models.Agent(name=name, role="t", tools=[], capability_tags=caps,
                     capability_scores={}, status="active")
    db.add(a); db.commit(); db.refresh(a); return a


GOOD = "def admin_stats(user):\n    if user.role != 'admin':\n        raise PermissionError\n    return {}\n"
BAD = "def admin_stats(user):\n    return {}  # TODO fix permission check\n"


def test_review_approve_and_record_linkage(dbx):
    author = mkagent(dbx, "dev", ["coding"])
    rev = mkagent(dbx, "qa-reviewer", ["review"])
    ws.write_file(dbx, author.id, "user_api.py", GOOD)
    r = reviewer.review_artifact(
        dbx, "user_api.py", rev.id, author_agent_id=author.id,
        checks=[{"type": "contains", "value": "PermissionError"},
                {"type": "not_contains", "value": "TODO"}],
        comment="permission guard present")
    assert r.passed is True and r.status == "current" and r.artifact_version == 1
    rec = dbx.query(models.VerificationRecord).get(r.verification_record_id)
    assert rec is not None and rec.verification_type == "reviewer" and rec.passed is True
    ev = json.loads(rec.evidence)
    assert ev["reviewer_agent_id"] == rev.id and ev["artifact_version"] == 1
    cur = reviewer.current_review(dbx, "user_api.py")
    assert cur is not None and cur.id == r.id


def test_review_reject_path(dbx):
    author = mkagent(dbx, "dev", ["coding"])
    rev = mkagent(dbx, "qa-reviewer", ["review"])
    ws.write_file(dbx, author.id, "user_api.py", BAD)
    r = reviewer.review_artifact(
        dbx, "user_api.py", rev.id, author_agent_id=author.id,
        checks=[{"type": "contains", "value": "PermissionError"},
                {"type": "not_contains", "value": "TODO"}])
    assert r.passed is False
    failed = [c for c in r.checks if not c["passed"]]
    assert len(failed) == 2
    rec = dbx.query(models.VerificationRecord).get(r.verification_record_id)
    assert rec.passed is False and len(rec.errors) == 2


def test_review_independence_and_capability(dbx):
    author = mkagent(dbx, "dev", ["coding", "review"])
    plain = mkagent(dbx, "plain", ["coding"])
    ws.write_file(dbx, author.id, "a.py", "x = 1\n")
    with pytest.raises(reviewer.ReviewError):
        reviewer.review_artifact(dbx, "a.py", author.id, author_agent_id=author.id)
    with pytest.raises(reviewer.ReviewError):
        reviewer.review_artifact(dbx, "a.py", plain.id, author_agent_id=author.id)
    with pytest.raises(reviewer.ReviewError):
        reviewer.review_artifact(dbx, "missing.py", author.id)


def test_review_goes_stale_after_new_version(dbx):
    author = mkagent(dbx, "dev", ["coding"])
    rev = mkagent(dbx, "qa-reviewer", ["review"])
    ws.write_file(dbx, author.id, "user_api.py", GOOD)
    r = reviewer.review_artifact(dbx, "user_api.py", rev.id, author_agent_id=author.id,
                                 checks=[{"type": "contains", "value": "PermissionError"}])
    assert r.passed is True
    ws.write_file(dbx, author.id, "user_api.py", BAD)  # v2: artifact moved on
    flipped = vs.check_freshness(dbx, "user_api.py")
    assert flipped >= 1
    dbx.refresh(r)
    assert r.status == "stale"
    assert reviewer.current_review(dbx, "user_api.py") is None
