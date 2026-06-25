from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone

import aiohttp

from src.common.config import AWSConfig
from src.common.events import emit_event
from src.common.notify import send_notification
from src.common.state import has_seen, mark_seen
from src.watchers import BaseWatcher, WatcherResult, WATCHER
from src.watchers.cloudwatch import _should_skip, _mark_failure, _mark_success

log = logging.getLogger("cottonmouth.watcher.cloudtrail")

_IAM_SENSITIVE_ACTIONS = {
    "CreateUser", "DeleteUser", "CreateRole", "DeleteRole",
    "AttachUserPolicy", "AttachRolePolicy", "DetachUserPolicy",
    "DetachRolePolicy", "PutUserPolicy", "PutRolePolicy",
    "CreateAccessKey", "DeleteAccessKey",
    "CreatePolicyVersion", "DeletePolicy",
}


class CloudTrailWatcher(BaseWatcher):
    name = "cloudtrail"

    def __init__(self, cfg: AWSConfig) -> None:
        self.cfg = cfg

    async def poll(self, session: aiohttp.ClientSession) -> WatcherResult:
        if _should_skip():
            return WatcherResult()

        cfg = self.cfg
        start = (datetime.now(timezone.utc) - timedelta(minutes=cfg.poll_interval // 60 + 2)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        try:
            proc = await asyncio.create_subprocess_exec(
                "aws", "cloudtrail", "lookup-events",
                "--lookup-attributes",
                f"AttributeKey=EventSource,AttributeValue=iam.amazonaws.com",
                "--start-time", start, "--max-results", "20",
                "--profile", cfg.profile, "--region", cfg.region, "--output", "json",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            if proc.returncode != 0:
                err = stderr.decode().strip()
                _mark_failure(err)
                return WatcherResult(ok=False, error=err[:200])

            _mark_success()
            data = json.loads(stdout.decode())
            for event in data.get("Events", []):
                event_id = event.get("EventId", "")
                eid = f"ct-{event_id}"
                if has_seen(WATCHER, eid):
                    continue
                mark_seen(WATCHER, eid)

                event_name = event.get("EventName", "Unknown")
                username = event.get("Username", "?")

                if event_name in _IAM_SENSITIVE_ACTIONS:
                    msg = f"{event_name} by {username}"
                    ct_url = (
                        f"https://console.aws.amazon.com/cloudtrailv2/home"
                        f"?region={cfg.region}#/events/{event_id}"
                    )
                    await send_notification("IAM Security Event", msg, subtitle="Review this change", severity="warning")
                    emit_event(
                        WATCHER, "warning", "IAM Security Event", msg,
                        source="cloudtrail", action_url=ct_url,
                    )

        except Exception as exc:
            log.exception("CloudTrail IAM check failed")
            return WatcherResult(ok=False, error=str(exc))

        return WatcherResult()
