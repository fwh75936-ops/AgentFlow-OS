"""AgentFlow OS — Agent Core (FastAPI)."""
import asyncio
import json

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.workspace.routes import router as workspace_router
from app.resources.routes import router as resources_router
from app.verification.routes import router as artifact_verification_router
from app.chief.routes import router as chief_router
from app.computer_use.routes import router as computer_use_router
from app.proactive.routes import router as proactive_router
from app.workflows.routes import router as workflow_execution_router
from app.teams.routes import router as team_chat_router
from app.memory.routes import router as knowledge_router
from app.mcp.routes import router as mcp_router
from app.system.routes import router as system_router
from app.browser.routes import router as browser_router
from app.database import init_db, SessionLocal
from app import models

app = FastAPI(title="AgentFlow OS Agent Core", version="0.1.0")
# "null" is the Origin a file:// page sends — the packaged Electron app
# loads dist/index.html over file:// and must be able to reach this API.
app.add_middleware(CORSMiddleware, allow_origins=["null"],
                   allow_origin_regex=r"^http://(localhost|127\.0\.0\.1)(:\d+)?$",
                   allow_methods=["*"], allow_headers=["*"], allow_private_network=True)
app.include_router(router)
app.include_router(workspace_router)
app.include_router(resources_router)
app.include_router(artifact_verification_router)
app.include_router(chief_router)
app.include_router(computer_use_router)
app.include_router(proactive_router)
app.include_router(workflow_execution_router)
app.include_router(team_chat_router)
app.include_router(knowledge_router)
app.include_router(mcp_router)
app.include_router(system_router)
app.include_router(browser_router)

@app.on_event("startup")
def _startup():
    init_db()
    from app.workspace import sandbox as _ws_sandbox
    _db = SessionLocal()
    try:
        _ws_sandbox.reconcile_interrupted(_db)
    finally:
        _db.close()
    from app.workspace import service as _ws_service
    _ws_service.start_watcher()

@app.get("/health")
def health():
    import os
    from app import database
    url = database.DATABASE_URL
    if url.startswith("sqlite:///") and not url.endswith(":memory:"):
        db_file = url[len("sqlite:///"):]
    elif url.endswith(":memory:"):
        db_file = ":memory:"
    else:
        db_file = url.split("://", 1)[0] + "://…"
    return {"status": "ok", "service": "agentflow-agent-core", "version": "0.1.0",
            "data_dir": os.path.join(database.BASE_DIR, "data"),
            "db_file": db_file}

@app.websocket("/ws/tasks/{task_id}")
async def ws_task(websocket: WebSocket, task_id: int):
    """Pushes real task/node status snapshots (polled from DB) until terminal."""
    await websocket.accept()
    try:
        while True:
            db = SessionLocal()
            try:
                t = db.query(models.Task).get(task_id)
                if t is None:
                    payload = {"error": "任务不存在"}
                    terminal = True
                else:
                    nodes = db.query(models.TaskNode).filter_by(task_id=task_id).all()
                    payload = {
                        "task_id": t.id, "status": t.status,
                        "nodes": [{"id": n.id, "name": n.name, "status": n.status,
                                   "assigned_agent_id": n.assigned_agent_id} for n in nodes]}
                    terminal = t.status in ("completed", "failed", "cancelled")
            finally:
                db.close()  # closed BEFORE the network send, never held across it
            await websocket.send_text(json.dumps(payload))
            if terminal:
                break
            await asyncio.sleep(1.0)
    except WebSocketDisconnect:
        pass
