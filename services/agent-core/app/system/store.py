"""Application settings: one JSON document in app_settings (id=1).

Fields (all optional, defaults applied on read):
  proxy_mode: "none" | "system" | "custom"   (default "none" = direct)
  proxy_url:  http:// or socks5:// address for custom mode
  default_provider_id / default_model: prefill for newly created agents
"""
import threading
from urllib.parse import urlparse

from app import models
from app.database import SessionLocal

# The settings document is read-modify-write as a whole; serialise
# updates so two concurrent patches cannot overwrite each other's keys.
_update_lock = threading.Lock()

DEFAULTS = {"proxy_mode": "none", "proxy_url": "",
            "default_provider_id": None, "default_model": ""}


def get_settings(db=None) -> dict:
    own = db is None
    db = db or SessionLocal()
    try:
        row = db.query(models.AppSetting).get(1)
        data = dict(DEFAULTS)
        if row and isinstance(row.data, dict):
            data.update(row.data)
        return data
    finally:
        if own:
            db.close()


def _validate_patch(patch: dict) -> None:
    if "proxy_mode" in patch and patch["proxy_mode"] not in ("none", "system", "custom"):
        raise ValueError(f"proxy_mode 只能是 none、system 或 custom，收到：{patch['proxy_mode']!r}")
    if patch.get("proxy_mode") == "custom" or "proxy_url" in patch:
        url = patch.get("proxy_url")
        if url:
            parsed = urlparse(str(url))
            if parsed.scheme not in ("http", "https", "socks5") or not parsed.hostname:
                raise ValueError(f"proxy_url 不是有效的代理地址：{url!r}")
    if patch.get("proxy_mode") == "custom" and not patch.get("proxy_url"):
        raise ValueError("使用自定义代理时必须填写 proxy_url")


def update_settings(patch: dict, db=None) -> dict:
    _validate_patch(patch or {})
    with _update_lock:
        return _update_settings_locked(patch, db)


def _update_settings_locked(patch: dict, db=None) -> dict:
    own = db is None
    db = db or SessionLocal()
    try:
        row = db.query(models.AppSetting).get(1)
        if row is None:
            row = models.AppSetting(id=1, data={})
            db.add(row)
        data = dict(DEFAULTS)
        if isinstance(row.data, dict):
            data.update(row.data)
        for key in DEFAULTS:
            if key in patch:
                data[key] = patch[key]
        row.data = data
        db.commit()
        return data
    finally:
        if own:
            db.close()
