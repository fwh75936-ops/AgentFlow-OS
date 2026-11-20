import os

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app import models
from app.database import get_db
from app.memory.parse import extract_text
from app.memory.search import KnowledgeIndex, chunk_text

router = APIRouter(prefix="/api/knowledge", tags=["knowledge-search"])

MAX_DOC_BYTES = 20 * 1024 * 1024


def _doc_out(item):
    title = item.title or (item.content.splitlines()[0][:60] if item.content else "")
    return {"id": item.id, "title": title, "source": item.source or "",
            "scope": item.scope, "agent_id": item.agent_id,
            "chunks": len(chunk_text(item.content)),
            "chars": len(item.content),
            "created_at": item.created_at.isoformat() if item.created_at else None,
            "preview": item.content[:160]}


def _add_document(db, title, source, content):
    content = (content or "").strip()
    if not content:
        raise ValueError("文档内容为空")
    item = models.MemoryItem(scope="long_term", title=title, source=source,
                             content=content)
    db.add(item); db.commit(); db.refresh(item)
    return item


@router.post("/search")
def search(body: dict, db: Session = Depends(get_db)):
    query = body.get("query", "")
    if not isinstance(query, str):
        raise HTTPException(422, "query 必须是字符串")
    try:
        top_k = int(body.get("top_k", 5))
    except (TypeError, ValueError):
        raise HTTPException(422, "top_k 必须是整数")
    agent_id = body.get("agent_id")
    if agent_id is not None and not isinstance(agent_id, int):
        raise HTTPException(422, "agent_id 必须是整数")
    idx = KnowledgeIndex(db)
    return {"query": query, "method": idx.stats()["method"],
            "results": idx.search(query, top_k=top_k, agent_id=agent_id)}


@router.get("/index/stats")
def index_stats(db: Session = Depends(get_db)):
    return KnowledgeIndex(db).stats()


@router.get("/documents")
def documents(db: Session = Depends(get_db)):
    items = db.query(models.MemoryItem).order_by(models.MemoryItem.id.desc()).all()
    return [_doc_out(it) for it in items]


@router.post("/import")
async def import_files(files: list[UploadFile] = File(...),
                       db: Session = Depends(get_db)):
    """Upload one or more documents. Each file is parsed, chunked and
    indexed independently; the response reports every file's real
    outcome (indexed with its chunk count, or failed with the reason)."""
    results = []
    for f in files:
        # Bounded read: the size limit is enforced WHILE reading, not
        # after the whole file is already in memory.
        raw = await f.read(MAX_DOC_BYTES + 1)
        if len(raw) > MAX_DOC_BYTES:
            results.append({"filename": f.filename, "status": "failed",
                            "error": "文件超过 20 MB 上限"})
            continue
        try:
            text = extract_text(f.filename or "", raw)
            item = _add_document(db, f.filename or "未命名文档", "upload", text)
            results.append({"filename": f.filename, "status": "indexed",
                            "item_id": item.id,
                            "chunks": len(chunk_text(item.content))})
        except Exception as e:
            # Per-file boundary: one bad file (parse OR database) never
            # sinks the batch or hides the files already imported.
            db.rollback()
            results.append({"filename": f.filename, "status": "failed",
                            "error": str(e)})
    return {"results": results}


@router.post("/import-text")
def import_text(body: dict, db: Session = Depends(get_db)):
    try:
        item = _add_document(db, str(body.get("title") or "粘贴的文档"),
                             "paste", str(body.get("content") or body.get("text") or ""))
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"status": "indexed", "item_id": item.id,
            "chunks": len(chunk_text(item.content))}


@router.post("/import-workspace")
def import_workspace(body: dict, db: Session = Depends(get_db)):
    """Import a file that already lives in the shared workspace."""
    from app.workspace.config import get_shared_root
    rel = str(body.get("path") or "").strip()
    if not rel:
        raise HTTPException(400, "没有给出共享工作区里的文件路径")
    # Resolve symlinks BEFORE the containment check: abspath alone
    # lets a workspace symlink point outside and still pass the prefix
    # test, importing files the workspace boundary is meant to exclude.
    root = os.path.realpath(get_shared_root())
    full = os.path.realpath(os.path.join(root, rel))
    if not (full == root or os.path.commonpath([root, full]) == root):
        raise HTTPException(400, "路径超出了共享工作区范围")
    if not os.path.isfile(full):
        raise HTTPException(404, f"共享工作区里没有这个文件：{rel}")
    with open(full, "rb") as fh:
        raw = fh.read(MAX_DOC_BYTES + 1)
    if len(raw) > MAX_DOC_BYTES:
        raise HTTPException(400, "文件超过 20 MB 上限")
    try:
        text = extract_text(os.path.basename(full), raw)
        item = _add_document(db, os.path.basename(full), "workspace", text)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"status": "indexed", "item_id": item.id,
            "chunks": len(chunk_text(item.content))}
