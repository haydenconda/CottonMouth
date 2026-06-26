"""Read-only client for the LiteLLM gateway's management surface.

CottonMouth does not enforce gateway controls — it *reconciles* them. This client
reads what the gateway exposes (``/v1/models``) and, when the gateway is backed by
a database, per-key entitlements/spend (``/key/info``), so the governance view can
show each agent's real model access and budget next to its CottonMouth tool
permissions. All calls are best-effort and short-cached; the gateway being down or
absent degrades gracefully to "unknown".
"""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from typing import Any

_BASE = os.environ.get("LITELLM_BASE_URL", "")
_KEY = os.environ.get("LITELLM_API_KEY", "")
_TIMEOUT = float(os.environ.get("LITELLM_TIMEOUT", "4"))
_TTL = float(os.environ.get("LITELLM_CACHE_TTL", "60"))

_cache: dict[str, Any] = {"at": 0.0, "data": None}


def enabled() -> bool:
    return bool(_BASE)


def _get(path: str) -> Any:
    req = urllib.request.Request(
        _BASE.rstrip("/") + path,
        headers={"Authorization": f"Bearer {_KEY}"} if _KEY else {},
    )
    with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _models() -> list[str]:
    try:
        data = _get("/v1/models")
        rows = data.get("data", data) if isinstance(data, dict) else data
        return sorted({r.get("id", "") for r in rows if isinstance(r, dict) and r.get("id")})
    except (urllib.error.URLError, OSError, ValueError, KeyError):
        return []


def _key_info() -> dict[str, Any]:
    """Per-key entitlements/spend — only meaningful when the gateway has a DB.
    Returns ``{}`` (and db_backed=False upstream) otherwise."""
    try:
        info = _get("/key/info")
        return info.get("info", info) if isinstance(info, dict) else {}
    except (urllib.error.URLError, OSError, ValueError, KeyError):
        return {}


def snapshot(force: bool = False) -> dict[str, Any]:
    """Return ``{enabled, endpoint, reachable, db_backed, models, key}`` for the
    gateway, cached for ``LITELLM_CACHE_TTL`` seconds."""
    if not enabled():
        return {"enabled": False, "endpoint": "", "reachable": False,
                "db_backed": False, "models": [], "key": {}}

    now = time.time()
    if not force and _cache["data"] is not None and now - _cache["at"] < _TTL:
        return _cache["data"]

    models = _models()
    key = _key_info()
    data = {
        "enabled": True,
        "endpoint": _BASE,
        "reachable": bool(models) or bool(key),
        # /key/info returns data only when LiteLLM is backed by a DB (virtual keys).
        "db_backed": bool(key),
        "models": models,
        "key": key,
    }
    _cache.update(at=now, data=data)
    return data
