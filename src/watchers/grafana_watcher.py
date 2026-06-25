from __future__ import annotations

import logging

import aiohttp

from src.common.config import GrafanaConfig
from src.common.events import emit_event
from src.common.notify import send_notification
from src.common.state import has_seen, mark_seen, prune_old
from src.watchers import BaseWatcher, WatcherResult, WATCHER

log = logging.getLogger("cottonmouth.watcher.grafana")


class GrafanaWatcher(BaseWatcher):
    name = "grafana"

    def __init__(self, cfg: GrafanaConfig) -> None:
        self.cfg = cfg

    async def poll(self, session: aiohttp.ClientSession) -> WatcherResult:
        cfg = self.cfg
        url = f"{cfg.url}/api/v1/provisioning/alert-rules"
        headers = {"Authorization": f"Bearer {cfg.service_account_token}", "Accept": "application/json"}

        try:
            async with session.get(url, headers=headers) as resp:
                if resp.status == 404:
                    url_alt = f"{cfg.url}/api/alertmanager/grafana/api/v2/alerts"
                    async with session.get(url_alt, headers=headers) as resp2:
                        if resp2.status != 200:
                            return WatcherResult(ok=False, error=f"HTTP {resp2.status}")
                        alerts = await resp2.json()
                        await _process_alertmanager_alerts(alerts, cfg.url)
                        return WatcherResult()
                if resp.status != 200:
                    return WatcherResult(ok=False, error=f"HTTP {resp.status}")
        except Exception as exc:
            log.exception("Grafana alert rules check failed")
            return WatcherResult(ok=False, error=str(exc))

        url_instances = f"{cfg.url}/api/alertmanager/grafana/api/v2/alerts"
        try:
            async with session.get(url_instances, headers=headers) as resp:
                if resp.status != 200:
                    return WatcherResult(ok=False, error=f"HTTP {resp.status}")
                alerts = await resp.json()
        except Exception as exc:
            log.exception("Grafana alertmanager check failed")
            return WatcherResult(ok=False, error=str(exc))

        await _process_alertmanager_alerts(alerts, cfg.url)
        return WatcherResult()


async def _process_alertmanager_alerts(alerts: list[dict], grafana_url: str = "") -> None:
    for alert in alerts:
        status = alert.get("status", {})
        state = status.get("state", "") if isinstance(status, dict) else status
        if state not in ("firing", "active"):
            continue

        fingerprint = alert.get("fingerprint", "")
        alert_id = f"grafana-{fingerprint}"
        if has_seen(WATCHER, alert_id):
            continue
        mark_seen(WATCHER, alert_id)

        labels = alert.get("labels", {})
        annotations = alert.get("annotations", {})
        name = labels.get("alertname", "Unknown Alert")
        summary = annotations.get("summary", annotations.get("description", ""))

        severity = labels.get("severity", "unknown")
        title = f"Grafana Alert: {name}"
        if severity in ("critical", "high"):
            title = f"CRITICAL: {name}"

        msg = summary[:200] if summary else f"Alert {name} is firing"
        sev = "critical" if severity in ("critical", "high") else "warning"
        alert_url = f"{grafana_url}/alerting/list" if grafana_url else ""
        await send_notification(title, msg, subtitle=f"Severity: {severity}", severity=sev)
        emit_event(WATCHER, sev, title, msg, source="grafana", action_url=alert_url)

    prune_old(WATCHER, keep_latest=500)
