from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.browser import service
from app.database import get_db

router = APIRouter(prefix="/api/browser", tags=["browser"])


@router.get("/status")
def browser_status(db: Session = Depends(get_db)):
    return service.status(db)


@router.get("/config")
def browser_config(db: Session = Depends(get_db)):
    return service.config_out(service.get_config(db))


@router.put("/config")
def browser_update_config(body: dict, db: Session = Depends(get_db)):
    try:
        return service.update_config(db, body)
    except service.BrowserError as e:
        raise HTTPException(400, str(e))


@router.get("/search-engines")
def search_engines():
    return [{"key": k, "label": v[0]} for k, v in service.SEARCH_ENGINES.items()]


@router.post("/open")
def browser_open(body: dict, db: Session = Depends(get_db)):
    """User-driven open from the AI 电脑 page (no agent lease involved)."""
    try:
        return service.open_url(db, str(body.get("url") or ""))
    except service.BrowserError as e:
        raise HTTPException(400, str(e))


@router.post("/clear-data")
def browser_clear(db: Session = Depends(get_db)):
    return service.clear_data(db)
