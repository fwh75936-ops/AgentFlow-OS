"""SQLite persistence via SQLAlchemy. DB path overridable with AGENTFLOW_DB_URL."""
import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_URL = "sqlite:///" + os.path.join(BASE_DIR, "agentflow.db")
DATABASE_URL = os.environ.get("AGENTFLOW_DB_URL", DEFAULT_URL)

# Restore staging: POST /api/system/restore validates an uploaded backup
# and writes it next to the live DB as <db>.restore-staged. Before the
# engine is created (i.e. before any connection can exist), swap it in.
# The running process never hot-swaps its own database file.
if DATABASE_URL.startswith("sqlite:///"):
    _db_file = DATABASE_URL[len("sqlite:///"):]
    _staged = _db_file + ".restore-staged"
    try:
        if os.path.isfile(_staged):
            os.replace(_staged, _db_file)
            for _suffix in ("-wal", "-shm"):
                if os.path.isfile(_db_file + _suffix):
                    os.unlink(_db_file + _suffix)
    except OSError as e:
        # A restore the user explicitly staged must never fail silently:
        # starting on the OLD database while reporting success would be
        # worse than not starting at all.
        raise RuntimeError(f"恢复备份失败，服务未启动：{e}") from e
connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
_engine_kwargs = {}
if DATABASE_URL in ("sqlite:///:memory:", "sqlite://"):
    # Every new connection to :memory: is a DIFFERENT empty database;
    # pin all work to one shared connection.
    from sqlalchemy.pool import StaticPool
    _engine_kwargs["poolclass"] = StaticPool
engine = create_engine(DATABASE_URL, connect_args=connect_args, **_engine_kwargs)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def _ensure_added_columns():
    """Idempotently add columns introduced after a table already exists
    (create_all never alters existing tables). Only additive, nullable or
    defaulted columns — existing data is never touched."""
    from sqlalchemy import inspect, text
    wanted = {
        "model_providers": {"format": "VARCHAR(30) DEFAULT 'chat_completions'",
                            "models": "JSON DEFAULT '[]'"},
        "skills": {"source": "VARCHAR(30) DEFAULT 'manual'", "files": "JSON DEFAULT '[]'"},
        "mcp_servers": {"env": "JSON DEFAULT '{}'", "args": "JSON DEFAULT '[]'"},
        "memory_items": {"title": "VARCHAR(300) DEFAULT ''", "source": "VARCHAR(60) DEFAULT ''"},
        "agents": {"responsibilities": "JSON DEFAULT '{}'",
                   "appearance": "JSON DEFAULT '{}'"},
        "sandbox_installs": {"method": "VARCHAR(20) DEFAULT ''"},
        "workspace_command_logs": {"sandbox": "VARCHAR(120)"},
        "dependency_install_jobs": {"sandbox": "VARCHAR(120)",
                                    "target_distro": "VARCHAR(60)",
                                    "target_version": "VARCHAR(60)"},
    }
    insp = inspect(engine)
    tables = set(insp.get_table_names())
    with engine.begin() as conn:
        for table, cols in wanted.items():
            if table not in tables:
                continue
            have = {c["name"] for c in insp.get_columns(table)}
            for name, ddl in cols.items():
                if name not in have:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))


def init_db():
    from app import models  # noqa: F401
    Base.metadata.create_all(bind=engine)
    _ensure_added_columns()
