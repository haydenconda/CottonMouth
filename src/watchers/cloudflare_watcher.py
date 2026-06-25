from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import aiohttp

from src.common.config import CloudflareConfig
from src.common.events import emit_event
from src.common.notify import send_notification
from src.common.state import has_seen, mark_seen, prune_old
from src.watchers import BaseWatcher, WatcherResult, WATCHER

log = logging.getLogger("cottonmouth.watcher.cloudflare")


class CloudflareWatcher(BaseWatcher):
    name = "cloudflare"

    def __init__(self, cfg: CloudflareConfig) -> None:
        self.cfg = cfg

    async def poll(self, session: aiohttp.ClientSession) -> WatcherResult:
        cfg = self.cfg
        url = f"https://api.cloudflare.com/client/v4/accounts/{cfg.account_id}/audit_logs"
        headers = {"Authorization": f"Bearer {cfg.api_token}", "Content-Type": "application/json"}
        since = (datetime.now(timezone.utc) - timedelta(minutes=cfg.poll_interval // 60 + 2)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        params = {"since": since, "per_page": "20"}

        try:
            async with session.get(url, headers=headers, params=params) as resp:
                if resp.status != 200:
                    return WatcherResult(ok=False, error=f"HTTP {resp.status}")
                data = await resp.json()
        except Exception as exc:
            log.exception("Cloudflare audit check failed")
            return WatcherResult(ok=False, error=str(exc))

        if not data.get("success"):
            return WatcherResult(ok=False, error="API returned success=false")

        security_actions = {"waf_rule", "firewall_rule", "access_rule", "page_rule", "dns_record", "ssl_certificate"}

        for entry in data.get("result", []):
            eid = entry.get("id", "")
            if has_seen(WATCHER, f"cf-{eid}"):
                continue
            mark_seen(WATCHER, f"cf-{eid}")

            action_type = entry.get("action", {}).get("type", "")
            resource_type = entry.get("resource", {}).get("type", "")

            if resource_type in security_actions or "security" in action_type.lower():
                actor = entry.get("actor", {}).get("email", "?")
                msg = f"{action_type}: {resource_type}"
                cf_url = f"https://dash.cloudflare.com/{cfg.account_id}/audit-log"
                await send_notification("Cloudflare Security Event", msg, subtitle=f"By {actor}", severity="warning")
                emit_event(
                    WATCHER, "warning", "Cloudflare Security Event", f"{msg} by {actor}",
                    source="cloudflare", action_url=cf_url,
                )

        prune_old(WATCHER, keep_latest=500)
        return WatcherResult()
