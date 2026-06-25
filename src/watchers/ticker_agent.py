"""Unified ticker agent — orchestrates per-service watchers and emits events to the ticker."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import time
from datetime import date, datetime, timezone
from pathlib import Path

import aiohttp

from src.common.config import (
    SlackConfig, JiraConfig, ConfluenceConfig, ArgoCDConfig, AWSConfig,
    GitHubConfig, GrafanaConfig, CloudflareConfig, AgentTraceConfig,
)
from src.common.events import emit_event, rotate_jsonl_files
from src.common.notify import send_notification, set_min_severity
from src.common.paths import data_dir

from src.common.slack import discover_joined_channels
from src.common.state import get_value, set_value
from src.watchers import WATCHER
from src.watchers.slack_watcher import SlackWatcher
from src.watchers.jira_watcher import JiraWatcher
from src.watchers.confluence_watcher import ConfluenceWatcher
from src.watchers.argocd import ArgoCDWatcher
from src.watchers.cloudwatch import CloudWatchWatcher
from src.watchers.cloudtrail import CloudTrailWatcher
from src.watchers.github_watcher import GitHubWatcher
from src.watchers.grafana_watcher import GrafanaWatcher
from src.watchers.cloudflare_watcher import CloudflareWatcher
from src.watchers.agent_trace_watcher import AgentTraceWatcher

log = logging.getLogger("cottonmouth.ticker")

CURSOR_AGENT = os.environ.get("CURSOR_AGENT", shutil.which("agent") or "agent")
WORKSPACE = os.environ.get("AGENT_FLEET_WORKSPACE", str(Path(__file__).resolve().parents[2].parent))
HEALTH_FILE = data_dir() / "health.json"
REPORTS_DIR = data_dir() / "reports"
REPORT_HOUR = 8

CHANNEL_REFRESH_INTERVAL = 1200
TICK_INTERVAL = 30


class _WatcherSchedule:
    """Track last-run times, success/failure counts, and health status per watcher."""

    def __init__(self) -> None:
        self._last_run: dict[str, float] = {}
        self._status: dict[str, dict] = {}
        self._start_time = time.monotonic()

    def is_due(self, name: str, interval: int) -> bool:
        now = time.monotonic()
        last = self._last_run.get(name, 0.0)
        if now - last >= interval:
            self._last_run[name] = now
            return True
        return False

    def record_success(self, name: str) -> None:
        entry = self._status.setdefault(name, {"ok": 0, "errors": 0, "last_error": ""})
        entry["ok"] += 1
        entry["last_ok"] = datetime.now(timezone.utc).isoformat()

    def record_failure(self, name: str, error: str) -> None:
        entry = self._status.setdefault(name, {"ok": 0, "errors": 0, "last_error": ""})
        entry["errors"] += 1
        entry["last_error"] = error[:200]
        entry["last_error_at"] = datetime.now(timezone.utc).isoformat()

    def health_snapshot(self) -> dict:
        uptime_s = int(time.monotonic() - self._start_time)
        watchers: dict[str, dict] = {}
        for name, entry in self._status.items():
            watchers[name] = {
                "ok_count": entry.get("ok", 0),
                "error_count": entry.get("errors", 0),
                "last_ok": entry.get("last_ok", ""),
                "last_error": entry.get("last_error", ""),
                "last_error_at": entry.get("last_error_at", ""),
            }
        return {
            "ts": datetime.now(timezone.utc).isoformat(),
            "uptime_seconds": uptime_s,
            "pid": os.getpid(),
            "watchers": watchers,
        }


def _write_health(sched: _WatcherSchedule) -> None:
    try:
        snapshot = sched.health_snapshot()
        tmp = HEALTH_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
        tmp.rename(HEALTH_FILE)
    except Exception:
        log.exception("Failed to write health file")


# ===================================================================
# Daily combined briefing
# ===================================================================

async def _check_daily_report(channel_map: dict[str, str], github_cfg: GitHubConfig) -> None:
    now = datetime.now()
    if now.hour != REPORT_HOUR:
        return

    today = date.today().isoformat()
    last_report = get_value(WATCHER, "last_report_date")
    if last_report == today:
        return

    log.info("Generating daily briefing for %s", today)
    set_value(WATCHER, "last_report_date", today)

    channel_lines = "\n".join(f"  {name} = {cid}" for name, cid in sorted(channel_map.items()))
    repos_list = ", ".join(github_cfg.watched_repos)

    prompt = (
        "Generate a concise morning briefing for a platform team member. "
        "Use the Slack, Jira, GitHub, ArgoCD, and Grafana MCP tools to get live data.\n\n"
        "SLACK CHANNELS (use these IDs with slack_get_channel_history):\n"
        f"{channel_lines}\n\n"
        "Always check team-platform-core and ask-platform-core first.\n\n"
        f"GitHub repos: {repos_list}\n"
        f"GitHub username: {github_cfg.username}\n\n"
        "Include:\n"
        "- Key Slack messages from team-platform-core and ask-platform-core\n"
        "- Unread DMs and mentions\n"
        "- Jira tickets with recent status changes, blockers, upcoming deadlines\n"
        "- Open PRs needing review, recently merged PRs\n"
        "- Any failing CI, degraded ArgoCD apps, firing Grafana alerts\n\n"
        "Format as concise markdown with clear sections."
    )

    try:
        proc = await asyncio.create_subprocess_exec(
            CURSOR_AGENT,
            "-p", "--mode=ask",
            "--model", "claude-4.6-opus-max",
            "--workspace", WORKSPACE,
            "--output-format", "text",
            "--approve-mcps", "--trust", "--force",
            prompt,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=240)

        if proc.returncode != 0:
            log.warning("Daily report agent failed (exit %d)", proc.returncode)
            return

        report = stdout.decode().strip()
        if not report:
            return

    except asyncio.TimeoutError:
        log.warning("Daily report agent timed out")
        proc.kill()
        return
    except Exception:
        log.exception("Daily report generation failed")
        return

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    report_path = REPORTS_DIR / f"daily-{today}.md"
    report_path.write_text(f"# Daily Briefing — {today}\n\n{report}\n", encoding="utf-8")
    log.info("Daily report written to %s", report_path)

    first_line = report.split("\n")[0][:100]
    await send_notification("Daily Briefing", first_line, subtitle=today, severity="info")
    emit_event(WATCHER, "info", f"Daily Briefing — {today}", first_line, source="daily-report")


# ===================================================================
# Main run loop
# ===================================================================

def _try_config(name: str, factory):
    try:
        return factory()
    except RuntimeError as e:
        log.warning("Skipping %s watcher — missing config: %s", name, e)
        return None


async def run() -> None:
    agent_trace_cfg = _try_config("agent-trace", AgentTraceConfig) or AgentTraceConfig()
    slack_cfg = _try_config("slack", SlackConfig)
    jira_cfg = _try_config("jira", JiraConfig)
    confluence_cfg = _try_config("confluence", ConfluenceConfig)
    argocd_cfg = _try_config("argocd", ArgoCDConfig)
    aws_cfg = _try_config("aws", AWSConfig)
    github_cfg = _try_config("github", GitHubConfig)
    grafana_cfg = _try_config("grafana", GrafanaConfig)
    cf_cfg = _try_config("cloudflare", CloudflareConfig)

    sched = _WatcherSchedule()
    if slack_cfg:
        set_min_severity(slack_cfg.notify_min_severity)
    log.info("Ticker agent starting (tick every %ds, per-watcher intervals)", TICK_INTERVAL)

    async with aiohttp.ClientSession() as session:
        watchers: dict[str, tuple] = {}
        channel_map: dict[str, str] = {}
        slack_w = None

        if slack_cfg:
            try:
                channel_map = await discover_joined_channels(session, slack_cfg)
                slack_w = SlackWatcher(slack_cfg, channel_map)
                watchers["slack"] = (slack_w, slack_cfg.poll_interval)
                log.info("Monitoring %d Slack channels", len(channel_map))
            except Exception:
                log.warning("Slack watcher failed to initialize, skipping")

        if jira_cfg:
            watchers["jira"] = (JiraWatcher(jira_cfg), jira_cfg.poll_interval)
        if confluence_cfg:
            watchers["confluence"] = (ConfluenceWatcher(confluence_cfg), confluence_cfg.poll_interval)
        if argocd_cfg:
            watchers["argocd"] = (ArgoCDWatcher(argocd_cfg), argocd_cfg.poll_interval)
        if aws_cfg:
            watchers["cloudwatch"] = (CloudWatchWatcher(aws_cfg), aws_cfg.poll_interval)
            watchers["cloudtrail"] = (CloudTrailWatcher(aws_cfg), aws_cfg.poll_interval)
        if github_cfg:
            watchers["github"] = (GitHubWatcher(github_cfg), github_cfg.poll_interval)
        if grafana_cfg:
            watchers["grafana"] = (GrafanaWatcher(grafana_cfg), grafana_cfg.poll_interval)
        if cf_cfg:
            watchers["cloudflare"] = (CloudflareWatcher(cf_cfg), cf_cfg.poll_interval)

        agent_trace_w = AgentTraceWatcher(
            traces_dir=agent_trace_cfg.traces_dir,
            cost_threshold=agent_trace_cfg.cost_alert_threshold_usd,
            latency_multiplier=agent_trace_cfg.latency_alert_multiplier,
        )
        watchers["agent-trace"] = (agent_trace_w, agent_trace_cfg.poll_interval)

        # COTTONMOUTH_WATCHERS (comma-separated) whitelists which watchers run. This lets
        # a cluster deploy run just "agent-trace" even when IRSA grants AWS creds
        # that would otherwise auto-enable the cloudwatch/cloudtrail watchers.
        allow = {w.strip() for w in os.environ.get("COTTONMOUTH_WATCHERS", "").split(",") if w.strip()}
        if allow:
            watchers = {k: v for k, v in watchers.items() if k in allow}
            if not watchers:
                agent_trace_w = AgentTraceWatcher(
                    traces_dir=agent_trace_cfg.traces_dir,
                    cost_threshold=agent_trace_cfg.cost_alert_threshold_usd,
                    latency_multiplier=agent_trace_cfg.latency_alert_multiplier,
                )
                watchers["agent-trace"] = (agent_trace_w, agent_trace_cfg.poll_interval)

        log.info("Active watchers: %s", ", ".join(sorted(watchers.keys())))

        first_tick = True
        while True:
            try:
                if slack_w and sched.is_due("channel_refresh", CHANNEL_REFRESH_INTERVAL) and not first_tick:
                    await slack_w.refresh_channels(session)

                named_tasks: list[tuple[str, asyncio.Task]] = []

                for watcher_name, (watcher, interval) in watchers.items():
                    if sched.is_due(watcher_name, interval):
                        task = asyncio.ensure_future(watcher.poll(session))
                        named_tasks.append((watcher_name, task))

                if named_tasks:
                    results = await asyncio.gather(
                        *(t for _, t in named_tasks), return_exceptions=True,
                    )
                    for (name, _), result in zip(named_tasks, results):
                        if isinstance(result, Exception):
                            sched.record_failure(name, str(result))
                        elif hasattr(result, "ok") and not result.ok:
                            sched.record_failure(name, result.error)
                        else:
                            sched.record_success(name)

                if slack_w and github_cfg:
                    await _check_daily_report(slack_w.channel_map, github_cfg)

                if sched.is_due("file_rotation", 3600):
                    rotate_jsonl_files()

                _write_health(sched)
                first_tick = False
            except Exception:
                log.exception("Ticker agent watcher error")
            await asyncio.sleep(TICK_INTERVAL)
