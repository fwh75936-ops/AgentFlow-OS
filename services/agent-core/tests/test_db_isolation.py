"""The test suite must never touch the real dev database.

Background threads (task-runner workers, team-chat dispatch) resolve
SessionLocal when they first run — after any function-scoped patch is
torn down. Before the session-wide guard in tests/conftest.py, a
@mention test re-ran real task #1 by id collision (real LLM calls,
real token spend, real task mutation on every full run). This test
posts a dispatch-triggering @mention and asserts the real database
file gains no rows while the threads settle.
"""
import os
import sqlite3
import time

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models
from app.database import Base, DATABASE_URL
from app.teams import chat


def _real_counts():
    path = DATABASE_URL[len("sqlite:///"):]
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        cur = con.cursor()
        return {
            "tasks": cur.execute("SELECT COUNT(*) FROM tasks").fetchone()[0],
            "messages": cur.execute("SELECT COUNT(*) FROM agent_messages").fetchone()[0],
            "decisions": cur.execute("SELECT COUNT(*) FROM scheduler_decisions").fetchone()[0],
            "verifications": cur.execute("SELECT COUNT(*) FROM verification_records").fetchone()[0],
            "snapshots": cur.execute("SELECT COUNT(*) FROM execution_snapshots").fetchone()[0],
            "task1_tokens": cur.execute(
                "SELECT token_usage FROM tasks WHERE id = 1").fetchone()[0],
        }
    finally:
        con.close()


@pytest.fixture()
def dbx(tmp_path):
    engine = create_engine("sqlite:///" + str(tmp_path / "chat.db"),
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    db = sessionmaker(bind=engine, autocommit=False, autoflush=False)()
    yield db
    db.close()


def test_dispatch_threads_never_touch_real_db(dbx):
    for name in ("林晓", "陈工", "苏晴"):
        dbx.add(models.Agent(name=name, role="t", tools=[],
                             capability_tags=[], capability_scores={},
                             status="active"))
    dbx.commit()
    pm, dev, qa = dbx.query(models.Agent).order_by(models.Agent.id).all()
    team = models.Team(name="隔离队", leader_agent_id=pm.id)
    dbx.add(team)
    dbx.commit()
    for a in (pm, dev, qa):
        dbx.add(models.TeamMember(team_id=team.id, agent_id=a.id,
                                  is_leader=(a.id == pm.id)))
    dbx.commit()
    before = _real_counts()
    # A user @message spawns dispatch worker threads for both members.
    chat.post_message(dbx, team.id, "@陈工 修一下，@苏晴 之后复验")
    # Let the daemon threads start and finish (they fail fast: no
    # provider in the temp DB, so no real work is attempted).
    time.sleep(3)
    after = _real_counts()
    assert after == before
