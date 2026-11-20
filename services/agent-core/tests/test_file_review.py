"""Staged file-change review: agent tool writes are staged, only a user
accept writes through the normal versioned path, rejection changes
nothing. Runs against a real temporary shared directory + SQLite DB."""
import os

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models
from app.database import Base
from app.tools import registry
from app.tools.registry import execute_tool
from app.workspace import review, service


@pytest.fixture()
def ws(tmp_path, monkeypatch):
    root = tmp_path / "shared"
    root.mkdir()
    monkeypatch.setenv("AGENTFLOW_SHARED_ROOT", str(root))
    engine = create_engine("sqlite:///" + str(tmp_path / "review.db"),
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    db = factory()
    yield db, factory, str(root)
    db.close()


def make_agent(db, name, tools):
    a = models.Agent(name=name, role="developer", tools=tools,
                     capability_tags=["coding"], capability_scores={"coding": 0.9},
                     status="active")
    db.add(a)
    db.commit()
    db.refresh(a)
    return a


def test_tool_write_is_staged_not_written(ws, monkeypatch):
    db, factory, root = ws
    dev = make_agent(db, "开发工程师", ["linux_file_write", "linux_file_read"])
    monkeypatch.setattr(registry, "_ws_db", lambda: factory())
    out = execute_tool("linux_file_write",
                       {"agent_id": dev.id, "path": "proj/a.py", "content": "x = 1\n"},
                       dev.tools)
    assert out["staged"] is True and out["status"] == "pending_review"
    # The shared file must NOT exist yet — staging never touches disk.
    assert not os.path.exists(os.path.join(root, "proj", "a.py"))
    rows = review.list_changes(db, status="pending")
    assert len(rows) == 1 and rows[0]["path"] == "proj/a.py"
    assert rows[0]["diff_summary"]["added_lines"] == 1


def test_accept_writes_and_bumps_version(ws):
    db, _factory, root = ws
    dev = make_agent(db, "开发工程师", ["linux_file_write"])
    first = service.write_file(db, dev.id, "proj/a.py", "x = 1\n")
    assert first["version"] == 1
    change = review.stage_change(db, dev.id, "proj/a.py", "x = 2\n")
    # Still v1 content on disk while pending.
    assert service.read_file(db, "proj/a.py")["content"] == "x = 1\n"
    out = review.accept_change(db, change["id"])
    assert out["write"]["version"] == 2
    assert service.read_file(db, "proj/a.py")["content"] == "x = 2\n"
    assert out["change"]["status"] == "accepted"
    # A second decision on the same change is refused.
    with pytest.raises(ValueError):
        review.accept_change(db, change["id"])


def test_reject_leaves_file_untouched_and_records_event(ws):
    db, _factory, _root = ws
    dev = make_agent(db, "开发工程师", ["linux_file_write"])
    service.write_file(db, dev.id, "proj/b.py", "keep = True\n")
    change = review.stage_change(db, dev.id, "proj/b.py", "keep = False\n")
    out = review.reject_change(db, change["id"])
    assert out["change"]["status"] == "rejected"
    after = service.read_file(db, "proj/b.py")
    assert after["content"] == "keep = True\n" and after["version"] == 1
    events = service.list_events(db, limit=50)
    assert any(e.event_type == "file_change_rejected" for e in events)


def test_accept_conflicts_when_file_moved_on(ws):
    db, _factory, _root = ws
    dev = make_agent(db, "开发工程师", ["linux_file_write"])
    other = make_agent(db, "测试工程师", ["linux_file_write"])
    service.write_file(db, dev.id, "proj/c.py", "v = 1\n")
    change = review.stage_change(db, dev.id, "proj/c.py", "v = 2\n")
    service.write_file(db, other.id, "proj/c.py", "v = 3\n")  # file moved to v2 meanwhile
    with pytest.raises(service.ConflictError):
        review.accept_change(db, change["id"])
    row = db.query(models.PendingFileChange).get(change["id"])
    assert row.status == "pending"  # still actionable, not silently dropped


def test_new_stage_supersedes_older_pending(ws):
    db, _factory, _root = ws
    dev = make_agent(db, "开发工程师", ["linux_file_write"])
    first = review.stage_change(db, dev.id, "proj/d.py", "a = 1\n")
    second = review.stage_change(db, dev.id, "proj/d.py", "a = 2\n")
    rows = {r["id"]: r["status"] for r in review.list_changes(db)}
    assert rows[first["id"]] == "superseded"
    assert rows[second["id"]] == "pending"
