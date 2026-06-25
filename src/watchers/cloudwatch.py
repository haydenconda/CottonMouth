from __future__ import annotations

import asyncio
import json
import logging
import time

import aiohttp

from src.common.config import AWSConfig
from src.common.events import emit_event
from src.common.notify import send_notification
from src.common.state import get_value, set_value, has_seen, mark_seen, prune_old
from src.watchers import BaseWatcher, WatcherResult, WATCHER

log = logging.getLogger("cottonmouth.watcher.cloudwatch")

_backoff_until: float = 0.0
_consecutive_failures: int = 0
_BACKOFF_BASE = 60
_BACKOFF_MAX = 3600


def _should_skip() -> bool:
    return time.monotonic() < _backoff_until


def _mark_failure(stderr_text: str) -> None:
    global _backoff_until, _consecutive_failures
    auth_errors = ("token has expired", "refresh failed", "sso", "not authorized", "credentials")
    if not any(e in stderr_text.lower() for e in auth_errors):
        return
    _consecutive_failures += 1
    delay = min(_BACKOFF_BASE * (2 ** (_consecutive_failures - 1)), _BACKOFF_MAX)
    _backoff_until = time.monotonic() + delay
    log.warning("AWS auth error — backing off for %ds (attempt %d)", delay, _consecutive_failures)


def _mark_success() -> None:
    global _backoff_until, _consecutive_failures
    _backoff_until = 0.0
    _consecutive_failures = 0


class CloudWatchWatcher(BaseWatcher):
    name = "cloudwatch"

    def __init__(self, cfg: AWSConfig) -> None:
        self.cfg = cfg

    async def poll(self, session: aiohttp.ClientSession) -> WatcherResult:
        if _should_skip():
            return WatcherResult()

        cfg = self.cfg
        try:
            proc = await asyncio.create_subprocess_exec(
                "aws", "cloudwatch", "describe-alarms",
                "--state-value", "ALARM",
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
            alarms = data.get("MetricAlarms", []) + data.get("CompositeAlarms", [])

            for alarm in alarms:
                alarm_name = alarm.get("AlarmName", "unknown")
                state_key = f"cw-alarm-{alarm_name}"
                prev = get_value(WATCHER, state_key)
                if prev == "ALARM":
                    continue
                set_value(WATCHER, state_key, "ALARM")
                desc = alarm.get("AlarmDescription", alarm_name)
                cw_url = (
                    f"https://console.aws.amazon.com/cloudwatch/home"
                    f"?region={cfg.region}#alarmsV2:alarm/{alarm_name}"
                )
                await send_notification("CloudWatch Alarm", desc, subtitle=alarm_name, severity="critical")
                emit_event(
                    WATCHER, "critical", "CloudWatch Alarm", f"{alarm_name}: {desc}",
                    source="cloudwatch", action_url=cw_url,
                )

        except Exception as exc:
            log.exception("CloudWatch check failed")
            return WatcherResult(ok=False, error=str(exc))

        return WatcherResult()
