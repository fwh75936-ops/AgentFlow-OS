"""System settings, backup/restore, runtime health and usage stats.

Backup: a zip holding the SQLite database (which includes provider
API keys — the UI says so plainly) plus a small manifest. Restore is
two-stage for safety: the uploaded backup is fully validated first
(readable zip, SQLite integrity check, core tables present), then
staged next to the live database; app.database swaps it in before the
engine is created on the next backend start, and the API answers
restart_required so nobody believes it already took effect.
"""
import io
import json
import os
import shutil
import sqlite3
import subprocess
import zipfile
from datetime import datetime

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from sqlalchemy import text as sql_text
from sqlalchemy.orm import Session

from app import database, models
from app.database import get_db
from app.system import store

router = APIRouter(prefix="/api/system", tags=["system"])

REQUIRED_TABLES = {"agents", "tasks", "model_providers", "skills"}


def _db_path() -> str:
    url = database.DATABASE_URL
    if not url.startswith("sqlite:///"):
        raise HTTPException(400, "当前数据库不是本地 SQLite 文件，无法用此方式备份")
    return url[len("sqlite:///"):]


# ------------------------------------------------------------------ settings

@router.get("/settings")
def read_settings(db: Session = Depends(get_db)):
    return store.get_settings(db)


@router.put("/settings")
def write_settings(body: dict, db: Session = Depends(get_db)):
    mode = body.get("proxy_mode")
    if mode is not None and mode not in ("none", "system", "custom"):
        raise HTTPException(400, f"不支持的代理模式：{mode}")
    url = body.get("proxy_url")
    if url is not None and url and not (
            url.startswith("http://") or url.startswith("https://")
            or url.startswith("socks5://") or url.startswith("socks5h://")):
        raise HTTPException(400, "代理地址要以 http://、https:// 或 socks5:// 开头")
    try:
        return store.update_settings(body, db)
    except ValueError as e:
        # store-level validation (e.g. custom proxy without a usable URL)
        # is a client input problem, not a server fault.
        raise HTTPException(400, str(e))


# -------------------------------------------------------------------- backup

@router.get("/backup")
def backup(db: Session = Depends(get_db)):
    path = _db_path()
    # Flush WAL into the main file so the copy is complete.
    try:
        db.execute(sql_text("PRAGMA wal_checkpoint(TRUNCATE)"))
        db.commit()
    except Exception:
        pass
    buf = io.BytesIO()
    manifest = {"app": "AgentFlow OS", "kind": "backup",
                "created_at": datetime.utcnow().isoformat(),
                "contains_secrets": True,
                "note": "包含数据库，其中有模型供应商的 API Key，请妥善保管"}
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.write(path, "agentflow.db")
        z.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    buf.seek(0)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return StreamingResponse(
        buf, media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="agentflow-backup-{stamp}.zip"'})


def _validate_backup(raw: bytes) -> bytes:
    """Returns the database bytes when the backup is structurally sound,
    else raises HTTPException with the reason. Nothing is written here."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile:
        raise HTTPException(400, "这不是一个有效的 zip 备份文件")
    names = zf.namelist()
    if "agentflow.db" not in names:
        raise HTTPException(400, "备份里缺少数据库文件 agentflow.db")
    info = zf.getinfo("agentflow.db")
    if info.file_size > 500 * 1024 * 1024:
        raise HTTPException(400, "备份里的数据库过大（超过 500 MB），已拒绝")
    data = zf.read("agentflow.db")
    # Integrity + schema check on a temp file.
    import tempfile
    fd, tmp_path = tempfile.mkstemp(suffix=".db")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        conn = sqlite3.connect(tmp_path)
        try:
            ok = conn.execute("PRAGMA integrity_check").fetchone()[0]
            if ok != "ok":
                raise HTTPException(400, f"备份数据库完整性检查未通过：{ok}")
            tables = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'")}
            missing = REQUIRED_TABLES - tables
            if missing:
                raise HTTPException(400, f"备份数据库缺少核心表：{'、'.join(sorted(missing))}")
        finally:
            conn.close()
    finally:
        os.unlink(tmp_path)
    return data


@router.post("/restore")
async def restore(file: UploadFile = File(...)):
    raw = await file.read(600 * 1024 * 1024 + 1)
    if len(raw) > 600 * 1024 * 1024:
        raise HTTPException(400, "备份文件过大（超过 600 MB），已拒绝")
    data = _validate_backup(raw)
    path = _db_path()
    staged = path + ".restore-staged"
    with open(staged, "wb") as f:
        f.write(data)
    return {"ok": True, "staged": True, "restart_required": True,
            "message": "备份已校验并暂存，重启后端服务后生效"}


@router.post("/clear-data")
def clear_data(body: dict, db: Session = Depends(get_db)):
    if str(body.get("confirm") or "") != "确认清除":
        raise HTTPException(400, "需要在确认字段里填写「确认清除」")
    counts = {}
    for table in reversed(models.Base.metadata.sorted_tables):
        result = db.execute(table.delete())
        counts[table.name] = result.rowcount
    db.commit()
    return {"ok": True, "cleared": counts}


# ------------------------------------------------------------------ runtimes

def _runtime(name, candidates, version_args, key=None):
    path = None
    for c in candidates:
        path = shutil.which(c)
        if path:
            break
    version = ""
    if path and version_args:
        try:
            r = subprocess.run([path, *version_args], capture_output=True,
                               text=True, timeout=8)
            version = (r.stdout or r.stderr).strip().splitlines()[0][:80]
        except Exception:
            version = ""
    return {"key": key or name, "label": name, "name": name,
            "available": bool(path), "path": path or "",
            "version": version}


@router.get("/runtimes")
def runtimes():
    return {"runtimes": [
        _runtime("Node.js", ["node"], ["--version"], key="node"),
        _runtime("npx", ["npx"], ["--version"], key="npx"),
        _runtime("Python", ["python3", "python"], ["--version"], key="python3"),
        _runtime("uvx", ["uvx"], ["--version"], key="uvx"),
        _runtime("远程桌面服务", ["tigervncserver", "vncserver"], None, key="vnc"),
        _runtime("桌面控制工具", ["xdotool"], ["--version"], key="xdotool"),
    ]}


# --------------------------------------------------------------------- usage

@router.get("/usage")
def usage(db: Session = Depends(get_db)):
    """Token + request counts per agent / task / provider, aggregated
    from the same execution snapshots the workbench telemetry uses.
    Provider attribution follows each agent's CURRENT binding (an
    agent's history moves with it if it is rebound) — stated in the
    response so the 口径 is never ambiguous."""
    per_agent: dict[int, dict] = {}
    per_task_tokens: dict[int, int] = {}
    per_task_calls: dict[int, int] = {}
    requests = 0
    for snap in db.query(models.ExecutionSnapshot).all():
        usage_row = (snap.state or {}).get("node_token_usage")
        if not usage_row:
            continue
        requests += 1
        aid = usage_row.get("agent_id")
        total = int(usage_row.get("total_tokens", 0))
        if aid is not None:
            entry = per_agent.setdefault(aid, {"agent_id": aid, "tokens": 0, "requests": 0})
            entry["tokens"] += total
            entry["requests"] += 1
        per_task_tokens[snap.task_id] = per_task_tokens.get(snap.task_id, 0) + total
        per_task_calls[snap.task_id] = per_task_calls.get(snap.task_id, 0) + 1
    agents = {a.id: a for a in db.query(models.Agent).all()}
    providers = {p.id: p for p in db.query(models.ModelProvider).all()}
    per_provider: dict[int, dict] = {}
    for aid, entry in per_agent.items():
        agent = agents.get(aid)
        entry["agent_name"] = agent.name if agent else f"员工 {aid}"
        pid = agent.model_provider_id if agent else None
        if pid is not None:
            agg = per_provider.setdefault(
                pid, {"provider_id": pid,
                      "provider_name": providers[pid].name if pid in providers else f"供应商 {pid}",
                      "tokens": 0, "requests": 0})
            agg["tokens"] += entry["tokens"]
            agg["requests"] += entry["requests"]
    tasks = {t.id: t for t in db.query(models.Task).all()}
    per_task = [{"task_id": tid, "task_title": tasks[tid].title if tid in tasks else f"任务 {tid}",
                 "tokens": tokens, "calls": per_task_calls.get(tid, 0)}
                for tid, tokens in sorted(per_task_tokens.items(), key=lambda kv: -kv[1])]
    per_agent_sorted = sorted(per_agent.values(), key=lambda e: -e["tokens"])
    per_provider_sorted = sorted(per_provider.values(), key=lambda e: -e["tokens"])
    attribution = "按员工当前绑定的供应商归属"
    return {
        "per_agent": per_agent_sorted,
        "per_task": per_task,
        "per_provider": per_provider_sorted,
        "total_requests": requests,
        "provider_attribution": attribution,
        # The settings usage page consumes this exact shape (by_* lists
        # with name/title/provider + calls). It previously read field
        # names the backend never sent, so the page showed 暂无数据
        # forever regardless of real usage. Both shapes are returned;
        # they are built from the same aggregates and cannot diverge.
        "by_agent": [{"agent_id": e["agent_id"], "name": e["agent_name"],
                      "tokens": e["tokens"], "calls": e["requests"]}
                     for e in per_agent_sorted],
        "by_task": [{"task_id": e["task_id"], "title": e["task_title"],
                     "tokens": e["tokens"], "calls": e["calls"]}
                    for e in per_task],
        "by_provider": [{"provider_id": e["provider_id"], "provider": e["provider_name"],
                         "tokens": e["tokens"], "calls": e["requests"]}
                        for e in per_provider_sorted],
        "note": attribution,
    }
