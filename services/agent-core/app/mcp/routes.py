from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app import models
from app.database import get_db
from app.mcp import service
from app.mcp.client import MCPError

router = APIRouter(prefix="/api/mcp", tags=["mcp"])


@router.post("/servers/{server_id}/connect")
def connect(server_id: int, db: Session = Depends(get_db)):
    try:
        return service.connect(db, server_id)
    except (ValueError, MCPError) as e:
        raise HTTPException(400, str(e))


@router.post("/servers/{server_id}/disconnect")
def disconnect(server_id: int):
    return service.disconnect(server_id)


@router.get("/servers/{server_id}/tools")
def tools(server_id: int):
    try:
        # Wrapped like prompts/resources below: the client reads
        # {"tools": [...]}; returning the bare array made the UI claim
        # the server provides no tools even when it does.
        return {"tools": service.get_session(server_id).list_tools()}
    except ValueError as e:
        raise HTTPException(409, str(e))


@router.post("/servers/{server_id}/call")
def call(server_id: int, body: dict):
    tool = body.get("tool")
    if not isinstance(tool, str) or not tool.strip():
        raise HTTPException(422, "tool 必须是非空字符串")
    arguments = body.get("arguments")
    if arguments is not None and not isinstance(arguments, dict):
        raise HTTPException(422, "arguments 必须是对象")
    try:
        return service.call(server_id, tool, arguments)
    except ValueError as e:
        raise HTTPException(409, str(e))
    except MCPError as e:
        raise HTTPException(502, str(e))


@router.get("/catalog")
def catalog():
    """Preset servers for one-click adding, with honest local runtime
    availability for each entry."""
    from app.mcp.catalog import catalog_with_availability
    return {"catalog": catalog_with_availability()}


@router.post("/import-config")
def import_config(body: dict, db: Session = Depends(get_db)):
    """Import the de-facto `mcpServers` JSON config shape. Every entry
    is registered independently and reported; a bad entry never sinks
    the rest."""
    import json as _json
    import shlex as _shlex
    text = str(body.get("text") or "")
    try:
        parsed = _json.loads(text)
    except Exception as e:
        raise HTTPException(400, f"粘贴的内容不是有效的 JSON：{e}")
    servers = parsed.get("mcpServers") if isinstance(parsed, dict) else None
    if not isinstance(servers, dict) or not servers:
        raise HTTPException(400, "没有找到 mcpServers 配置，请粘贴包含 mcpServers 的完整配置")
    existing = {s.name for s in db.query(models.MCPServer).all()}
    results = []
    for name, cfg in servers.items():
        if not isinstance(cfg, dict):
            results.append({"name": name, "ok": False, "reason": "这一条的配置不是对象", "error": "这一条的配置不是对象"})
            continue
        if name in existing:
            results.append({"name": name, "ok": False, "reason": "已存在同名服务器，未重复添加", "error": "已存在同名服务器，未重复添加"})
            continue
        env = cfg.get("env") or {}
        if not isinstance(env, dict):
            results.append({"name": name, "ok": False, "reason": "env 必须是对象", "error": "env 必须是对象"})
            continue
        url = cfg.get("url")
        if url:
            row = models.MCPServer(name=name, transport="streamable_http",
                                   command_or_url=str(url),
                                   env=env, enabled=True, tools=[])
        else:
            command = str(cfg.get("command") or "").strip()
            if not command:
                results.append({"name": name, "ok": False, "reason": "缺少 command 或 url", "error": "缺少 command 或 url"})
                continue
            args = cfg.get("args") or []
            if not isinstance(args, list):
                results.append({"name": name, "ok": False, "reason": "args 必须是数组", "error": "args 必须是数组"})
                continue
            # shlex.join quotes the WHOLE argv losslessly; partial
            # hand-quoting corrupted paths with spaces or backslashes.
            full = _shlex.join([command] + [str(a) for a in args])
            row = models.MCPServer(name=name, transport="stdio",
                                   command_or_url=full,
                                   env=env, enabled=True, tools=[])
        db.add(row); db.flush()
        existing.add(name)
        results.append({"name": name, "ok": True, "id": row.id})
    db.commit()
    return {"results": results}


def _session_or_409(server_id: int):
    try:
        return service.get_session(server_id)
    except ValueError as e:
        raise HTTPException(409, str(e))


@router.get("/servers/{server_id}/prompts")
def prompts(server_id: int):
    session = _session_or_409(server_id)
    try:
        res = session.request("prompts/list")
        return {"prompts": res.get("prompts", [])}
    except MCPError as e:
        raise HTTPException(502, f"这台服务器没有提供提示词列表：{e}")


@router.get("/servers/{server_id}/resources")
def resources(server_id: int):
    session = _session_or_409(server_id)
    try:
        res = session.request("resources/list")
        return {"resources": res.get("resources", [])}
    except MCPError as e:
        raise HTTPException(502, f"这台服务器没有提供资源列表：{e}")


@router.get("/servers/{server_id}/logs")
def logs(server_id: int):
    session = _session_or_409(server_id)
    return {"state": session.state, "status": session.state,
            "server_info": session.server_info,
            "stderr_tail": session.stderr_tail[-50:]}
