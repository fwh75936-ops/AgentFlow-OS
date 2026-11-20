import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest


@pytest.fixture(scope="session", autouse=True)
def _isolate_background_threads(tmp_path_factory):
    """Process-wide guard: no test may ever touch the real dev database.

    Background threads (task-runner workers, team-chat dispatch,
    sandbox/dependency installers) resolve SessionLocal when they
    first run — and a function-scoped monkeypatch is already torn
    down by then, so those threads silently used the REAL agentflow.db
    (real LLM calls, real token spend, real task mutation on every
    full run; a @mention test re-ran real task #1 by id collision).
    This session-wide temp DB is the fallback those threads land in:
    test files that patch SessionLocal to their own temp DB keep
    working, and threads that outlive the patch land here — never in
    the real database. Assigned directly (not monkeypatched) on
    purpose: the patch must survive for the whole process, and the
    process exits right after the run.
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    import app.database as database
    from app.database import Base
    from app.system import store
    from app.teams import dispatch

    path = tmp_path_factory.mktemp("isolated") / "session.db"
    engine = create_engine("sqlite:///" + str(path),
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    TestSession = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    database.SessionLocal = TestSession
    # Module-top bindings are NOT redirected by patching app.database
    # alone: these modules captured SessionLocal at import time.
    dispatch.SessionLocal = TestSession
    store.SessionLocal = TestSession
    try:
        from app.browser import service as browser_service
        browser_service.SessionLocal = TestSession
    except Exception:
        pass
    yield
