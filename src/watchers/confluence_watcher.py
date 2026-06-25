from __future__ import annotations

import logging
from base64 import b64encode
from datetime import datetime, timedelta, timezone

import aiohttp

from src.common.config import ConfluenceConfig
from src.common.events import emit_event
from src.common.state import get_value, set_value
from src.watchers import BaseWatcher, WatcherResult, WATCHER

log = logging.getLogger("cottonmouth.watcher.confluence")

_space_cache: dict[str, str] = {}
_user_cache: dict[str, str] = {}


class ConfluenceWatcher(BaseWatcher):
    name = "confluence"

    def __init__(self, cfg: ConfluenceConfig) -> None:
        self.cfg = cfg

    async def poll(self, session: aiohttp.ClientSession) -> WatcherResult:
        cfg = self.cfg
        if not cfg.watched_spaces:
            return WatcherResult()

        creds = b64encode(f"{cfg.email}:{cfg.api_token}".encode()).decode()
        headers = {"Authorization": f"Basic {creds}", "Accept": "application/json"}

        try:
            for space_key in cfg.watched_spaces:
                space_id = await _resolve_space_id(session, cfg.site_url, headers, space_key)
                if not space_id:
                    continue

                url = f"{cfg.site_url}/wiki/api/v2/pages"
                params = {
                    "space-id": space_id,
                    "sort": "-modified-date",
                    "limit": "10",
                    "body-format": "storage",
                }

                try:
                    async with session.get(url, headers=headers, params=params) as resp:
                        if resp.status != 200:
                            log.warning("Confluence pages API returned %d for space %s", resp.status, space_key)
                            continue
                        data = await resp.json()
                except Exception:
                    log.exception("Confluence check failed for space %s", space_key)
                    continue

                for page in data.get("results", []):
                    page_id = page.get("id", "")
                    title = page.get("title", "")
                    version = page.get("version", {})
                    version_num = version.get("number", 0)
                    modified_by = version.get("authorId", "")

                    state_key = f"confluence-{space_key}-{page_id}"
                    prev_version = get_value(WATCHER, state_key)

                    if prev_version and str(version_num) != prev_version:
                        set_value(WATCHER, state_key, str(version_num))
                        author_name = await _resolve_user(session, cfg.site_url, headers, modified_by)
                        action = "Created" if version_num == 1 else f"Updated (v{version_num})"
                        page_url = f"{cfg.site_url}/wiki/spaces/{space_key}/pages/{page_id}"
                        emit_event(
                            WATCHER, "info", f"Confluence: {space_key}",
                            f"{action}: {title} by {author_name}",
                            source="confluence", action_url=page_url,
                        )
                    elif not prev_version:
                        set_value(WATCHER, state_key, str(version_num))

        except Exception as exc:
            log.exception("Confluence watcher failed")
            return WatcherResult(ok=False, error=str(exc))

        return WatcherResult()


async def _resolve_space_id(
    session: aiohttp.ClientSession, site_url: str,
    headers: dict[str, str], space_key: str,
) -> str:
    if space_key in _space_cache:
        return _space_cache[space_key]

    url = f"{site_url}/wiki/api/v2/spaces"
    params = {"keys": space_key, "limit": "1"}
    try:
        async with session.get(url, headers=headers, params=params) as resp:
            if resp.status != 200:
                return ""
            data = await resp.json()
    except Exception:
        log.exception("Confluence space lookup failed for %s", space_key)
        return ""

    results = data.get("results", [])
    if not results:
        log.warning("Confluence space '%s' not found", space_key)
        return ""

    sid = str(results[0].get("id", ""))
    _space_cache[space_key] = sid
    return sid


async def _resolve_user(
    session: aiohttp.ClientSession, site_url: str,
    headers: dict[str, str], account_id: str,
) -> str:
    if not account_id:
        return "Someone"
    if account_id in _user_cache:
        return _user_cache[account_id]

    url = f"{site_url}/wiki/rest/api/user"
    params = {"accountId": account_id}
    try:
        async with session.get(url, headers=headers, params=params) as resp:
            if resp.status != 200:
                return account_id
            data = await resp.json()
    except Exception:
        return account_id

    name = data.get("displayName", data.get("publicName", account_id))
    _user_cache[account_id] = name
    return name
