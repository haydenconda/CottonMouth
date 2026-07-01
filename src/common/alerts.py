"""Built-in alerting — a simple Slack Incoming Webhook.

This is the batteries-included alerting path for teams that don't run a
Prometheus/Alertmanager stack: point CottonMouth at a Slack webhook and matching
events are pushed straight to a channel. Teams that do run Prometheus can instead
alert off the metrics exporter (see deploy/observability/) — the two paths are
complementary.

Every event already funnels through ``events.emit_event`` (failures, permission
denials, cost spikes, slow runs, anomalies), so we hook there: one chokepoint,
no new event plumbing. Delivery is best-effort and fire-and-forget on a daemon
thread so a slow/unreachable Slack can never block the watcher or the API.

Config (env):
    COTTONMOUTH_SLACK_WEBHOOK_URL   Slack Incoming Webhook URL. Unset = disabled.
    COTTONMOUTH_ALERT_MIN_SEVERITY  Minimum severity to send: info|warning|critical
                                    (default: warning).
    COTTONMOUTH_ALERT_SOURCES       Optional comma-separated allowlist of event
                                    ``source`` values (e.g. "agent-error,agent-permission").
                                    Unset = all sources.
    COTTONMOUTH_DASHBOARD_URL       Optional dashboard base URL; turns each alert's
                                    cottonmouth://trace/<id> deep link into a
                                    clickable https link.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import urllib.request

log = logging.getLogger("cottonmouth.alerts")

_SEVERITY_RANK = {"info": 10, "warning": 20, "critical": 30}
_SEVERITY_EMOJI = {"info": ":information_source:", "warning": ":warning:", "critical": ":rotating_light:"}


def _webhook_url() -> str:
    return os.environ.get("COTTONMOUTH_SLACK_WEBHOOK_URL", "").strip()


def enabled() -> bool:
    return bool(_webhook_url())


def _min_rank() -> int:
    name = os.environ.get("COTTONMOUTH_ALERT_MIN_SEVERITY", "warning").strip().lower()
    return _SEVERITY_RANK.get(name, _SEVERITY_RANK["warning"])


def _source_allowed(source: str) -> bool:
    raw = os.environ.get("COTTONMOUTH_ALERT_SOURCES", "").strip()
    if not raw:
        return True
    allow = {s.strip() for s in raw.split(",") if s.strip()}
    return source in allow


def _dashboard_link(action_url: str) -> str:
    """Turn a cottonmouth://trace/<id>[/span/<id>] deep link into an https link
    if COTTONMOUTH_DASHBOARD_URL is set, else return ''."""
    base = os.environ.get("COTTONMOUTH_DASHBOARD_URL", "").strip().rstrip("/")
    if not base or not action_url.startswith("cottonmouth://trace/"):
        return ""
    rest = action_url[len("cottonmouth://trace/"):]
    trace_id = rest.split("/", 1)[0]
    return f"{base}/traces/{trace_id}" if trace_id else ""


def _build_payload(event: dict) -> dict:
    severity = event.get("severity", "info")
    emoji = _SEVERITY_EMOJI.get(severity, "")
    agent = event.get("agent", "")
    title = event.get("title", "CottonMouth alert")
    message = event.get("message", "")
    link = _dashboard_link(event.get("action_url", ""))

    header = f"{emoji} *{title}*".strip()
    context_bits = [f"severity: `{severity}`"]
    if agent:
        context_bits.append(f"source: `{agent}`")
    if event.get("source"):
        context_bits.append(f"event: `{event['source']}`")

    blocks = [
        {"type": "section", "text": {"type": "mrkdwn", "text": header}},
        {"type": "section", "text": {"type": "mrkdwn", "text": message or "_(no detail)_"}},
        {"type": "context", "elements": [{"type": "mrkdwn", "text": "  ·  ".join(context_bits)}]},
    ]
    if link:
        blocks.append({
            "type": "actions",
            "elements": [{
                "type": "button",
                "text": {"type": "plain_text", "text": "Open trace"},
                "url": link,
            }],
        })
    # ``text`` is the notification/fallback string; blocks render the rich card.
    return {"text": f"{title} — {message}"[:3000], "blocks": blocks}


def _post(url: str, payload: dict) -> None:
    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            resp.read()
    except Exception as exc:  # never let alerting break the caller
        log.warning("Slack alert delivery failed: %s", exc)


def maybe_dispatch(event: dict) -> None:
    """Send ``event`` to Slack if it passes the configured filters. Best-effort;
    returns immediately (delivery runs on a daemon thread)."""
    try:
        url = _webhook_url()
        if not url:
            return
        rank = _SEVERITY_RANK.get(event.get("severity", "info"), 0)
        if rank < _min_rank():
            return
        if not _source_allowed(event.get("source", "")):
            return
        payload = _build_payload(event)
        threading.Thread(target=_post, args=(url, payload), daemon=True).start()
    except Exception:
        log.exception("Failed to dispatch alert")
