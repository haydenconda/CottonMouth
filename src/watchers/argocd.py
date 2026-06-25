from __future__ import annotations

import logging
import ssl

import aiohttp

from src.common.config import ArgoCDConfig
from src.common.events import emit_event
from src.common.notify import send_notification
from src.common.state import get_value, set_value
from src.watchers import BaseWatcher, WatcherResult, WATCHER

log = logging.getLogger("cottonmouth.watcher.argocd")


class ArgoCDWatcher(BaseWatcher):
    name = "argocd"

    def __init__(self, cfg: ArgoCDConfig) -> None:
        self.cfg = cfg

    async def poll(self, session: aiohttp.ClientSession) -> WatcherResult:
        cfg = self.cfg
        url = f"{cfg.base_url}/api/v1/applications"
        headers = {"Authorization": f"Bearer {cfg.api_token}"}
        ssl_ctx = ssl.create_default_context()

        try:
            async with session.get(url, headers=headers, ssl=ssl_ctx) as resp:
                if resp.status != 200:
                    return WatcherResult(ok=False, error=f"HTTP {resp.status}")
                data = await resp.json()
        except Exception as exc:
            log.exception("ArgoCD request failed")
            return WatcherResult(ok=False, error=str(exc))

        for app in data.get("items", []):
            name = app.get("metadata", {}).get("name", "unknown")
            status = app.get("status", {})
            sync_status = status.get("sync", {}).get("status", "Unknown")
            health_status = status.get("health", {}).get("status", "Unknown")

            state_key = f"argo-{name}"
            prev_state = get_value(WATCHER, state_key)
            curr_state = f"{sync_status}/{health_status}"

            if curr_state == prev_state:
                continue
            set_value(WATCHER, state_key, curr_state)

            if health_status in ("Degraded", "Missing", "Unknown"):
                msg = f"{name}: {sync_status}, {health_status}"
                await send_notification(
                    "ArgoCD Alert", msg,
                    subtitle="Application needs attention", severity="warning",
                )
                emit_event(
                    WATCHER, "warning", "ArgoCD Alert", msg,
                    source="argocd", action_url=f"{cfg.base_url}/applications/{name}",
                )

        return WatcherResult()
