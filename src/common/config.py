from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

_ENV_LOADED = False


def _ensure_env() -> None:
    global _ENV_LOADED
    if _ENV_LOADED:
        return
    env_path = Path(__file__).resolve().parents[2] / ".env"
    load_dotenv(env_path)
    _ENV_LOADED = True


def _req(key: str) -> str:
    _ensure_env()
    val = os.getenv(key)
    if not val:
        raise RuntimeError(f"Missing required env var: {key}")
    return val


def _opt(key: str, default: str = "") -> str:
    _ensure_env()
    return os.getenv(key, default)


@dataclass(frozen=True)
class SlackConfig:
    token: str = field(default_factory=lambda: _req("SLACK_USER_TOKEN") if _opt("SLACK_USER_TOKEN") else _req("SLACK_BOT_TOKEN"))
    team_id: str = field(default_factory=lambda: _req("SLACK_TEAM_ID"))
    user_id: str = field(default_factory=lambda: _req("SLACK_USER_ID"))
    priority_channels: list[str] = field(
        default_factory=lambda: [
            c.strip() for c in _opt("SLACK_CHANNELS", "").split(",") if c.strip()
        ]
    )
    firehose_channels: list[str] = field(
        default_factory=lambda: [
            c.strip() for c in _opt("SLACK_FIREHOSE_CHANNELS", "").split(",") if c.strip()
        ]
    )
    poll_interval: int = 90
    notify_min_severity: str = field(default_factory=lambda: _opt("NOTIFY_MIN_SEVERITY", "warning"))


@dataclass(frozen=True)
class JiraConfig:
    site_url: str = field(default_factory=lambda: _req("ATLASSIAN_SITE_URL"))
    email: str = field(default_factory=lambda: _req("ATLASSIAN_USER_EMAIL"))
    api_token: str = field(default_factory=lambda: _req("ATLASSIAN_API_TOKEN"))
    watched_projects: list[str] = field(
        default_factory=lambda: [
            p.strip() for p in _opt("JIRA_WATCHED_PROJECTS", "").split(",") if p.strip()
        ]
    )
    poll_interval: int = 300


@dataclass(frozen=True)
class ConfluenceConfig:
    site_url: str = field(default_factory=lambda: _req("ATLASSIAN_SITE_URL"))
    email: str = field(default_factory=lambda: _req("ATLASSIAN_USER_EMAIL"))
    api_token: str = field(default_factory=lambda: _req("ATLASSIAN_API_TOKEN"))
    watched_spaces: list[str] = field(
        default_factory=lambda: [
            s.strip() for s in _opt("CONFLUENCE_SPACES", "").split(",") if s.strip()
        ]
    )
    poll_interval: int = 300


@dataclass(frozen=True)
class GitHubConfig:
    token: str = field(default_factory=lambda: _req("GITHUB_TOKEN"))
    username: str = field(default_factory=lambda: _req("GITHUB_USERNAME"))
    watched_repos: list[str] = field(
        default_factory=lambda: [
            r.strip() for r in _opt("GITHUB_WATCHED_REPOS", "").split(",") if r.strip()
        ]
    )
    poll_interval: int = 120


@dataclass(frozen=True)
class ArgoCDConfig:
    base_url: str = field(default_factory=lambda: _req("ARGOCD_BASE_URL"))
    api_token: str = field(default_factory=lambda: _req("ARGOCD_API_TOKEN"))
    poll_interval: int = 120


@dataclass(frozen=True)
class GrafanaConfig:
    url: str = field(default_factory=lambda: _req("GRAFANA_URL"))
    service_account_token: str = field(
        default_factory=lambda: _req("GRAFANA_SERVICE_ACCOUNT_TOKEN")
    )
    poll_interval: int = 60


@dataclass(frozen=True)
class CloudflareConfig:
    api_token: str = field(default_factory=lambda: _req("CLOUDFLARE_API_TOKEN"))
    account_id: str = field(default_factory=lambda: _req("CLOUDFLARE_ACCOUNT_ID"))
    poll_interval: int = 600


@dataclass(frozen=True)
class AWSConfig:
    profile: str = field(default_factory=lambda: _opt("AWS_PROFILE", "sandbox"))
    region: str = field(default_factory=lambda: _opt("AWS_REGION", "us-east-1"))
    poll_interval: int = 600


@dataclass(frozen=True)
class AgentTraceConfig:
    traces_dir: str = field(default_factory=lambda: _opt("AGENT_TRACES_DIR", ""))
    cost_alert_threshold_usd: float = field(
        default_factory=lambda: float(_opt("AGENT_COST_ALERT_THRESHOLD", "1.0"))
    )
    latency_alert_multiplier: float = field(
        default_factory=lambda: float(_opt("AGENT_LATENCY_ALERT_MULTIPLIER", "2.0"))
    )
    poll_interval: int = 10


