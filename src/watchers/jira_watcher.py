from __future__ import annotations

import logging
from base64 import b64encode
from datetime import datetime, timedelta, timezone

import aiohttp

from src.common.config import JiraConfig
from src.common.events import emit_event
from src.common.notify import send_notification
from src.common.state import has_seen, mark_seen, get_value, set_value, prune_old
from src.watchers import BaseWatcher, WatcherResult, WATCHER

log = logging.getLogger("cottonmouth.watcher.jira")


def _auth_header(cfg: JiraConfig) -> str:
    creds = b64encode(f"{cfg.email}:{cfg.api_token}".encode()).decode()
    return f"Basic {creds}"


class JiraWatcher(BaseWatcher):
    name = "jira"

    def __init__(self, cfg: JiraConfig) -> None:
        self.cfg = cfg

    async def poll(self, session: aiohttp.ClientSession) -> WatcherResult:
        try:
            await _check_assigned_issues(session, self.cfg)
            await _check_project_issues(session, self.cfg)
        except Exception as exc:
            log.exception("Jira watcher failed")
            return WatcherResult(ok=False, error=str(exc))
        return WatcherResult()


async def _check_assigned_issues(session: aiohttp.ClientSession, cfg: JiraConfig) -> None:
    url = f"{cfg.site_url}/rest/api/3/search/jql"
    headers = {
        "Authorization": _auth_header(cfg),
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    jql = "assignee = currentUser() AND updated >= -1h ORDER BY updated DESC"
    body = {
        "jql": jql, "maxResults": 20,
        "fields": ["summary", "status", "priority", "comment", "updated"],
    }

    try:
        async with session.post(url, headers=headers, json=body) as resp:
            if resp.status != 200:
                log.warning("Jira search returned %d", resp.status)
                return
            data = await resp.json()
    except Exception:
        log.exception("Jira search failed")
        return

    for issue in data.get("issues", []):
        key = issue.get("key", "")
        fields = issue.get("fields", {})
        summary = fields.get("summary", "")
        status = fields.get("status", {}).get("name", "?")
        priority = fields.get("priority", {}).get("name", "?")

        state_key = f"jira-status-{key}"
        prev_status = get_value(WATCHER, state_key)

        if prev_status and prev_status != status:
            set_value(WATCHER, state_key, status)
            msg = f"{summary} ({prev_status} → {status})"
            await send_notification(
                f"Jira: {key}", summary,
                subtitle=f"Status changed: {prev_status} → {status}", severity="info",
            )
            emit_event(
                WATCHER, "info", f"Jira: {key}", msg,
                source="jira", action_url=f"{cfg.site_url}/browse/{key}",
            )
        elif not prev_status:
            set_value(WATCHER, state_key, status)

        if priority in ("Blocker", "Critical", "Highest"):
            blocker_id = f"jira-blocker-{key}"
            if not has_seen(WATCHER, blocker_id):
                mark_seen(WATCHER, blocker_id)
                await send_notification(
                    f"Jira Blocker: {key}", summary,
                    subtitle=f"Priority: {priority}", severity="critical",
                )
                emit_event(
                    WATCHER, "critical", f"Jira Blocker: {key}", summary,
                    source="jira", action_url=f"{cfg.site_url}/browse/{key}",
                )

        comments = fields.get("comment", {}).get("comments", [])
        if comments:
            latest = comments[-1]
            comment_id = latest.get("id", "")
            cid = f"jira-comment-{key}-{comment_id}"
            if not has_seen(WATCHER, cid):
                mark_seen(WATCHER, cid)
                author = latest.get("author", {}).get("displayName", "Someone")
                body_doc = latest.get("body", {})
                text = ""
                if isinstance(body_doc, dict):
                    for block in body_doc.get("content", []):
                        for item in block.get("content", []):
                            if item.get("type") == "text":
                                text += item.get("text", "")
                text = text[:200] if text else "(comment)"
                await send_notification(
                    f"Jira: {key}", text,
                    subtitle=f"New comment from {author}", severity="info",
                )
                emit_event(
                    WATCHER, "info", f"Jira: {key}", f"Comment from {author}: {text}",
                    source="jira", action_url=f"{cfg.site_url}/browse/{key}",
                )

    prune_old(WATCHER, keep_latest=500)


async def _check_project_issues(session: aiohttp.ClientSession, cfg: JiraConfig) -> None:
    if not cfg.watched_projects:
        return

    projects_jql = ",".join(cfg.watched_projects)
    url = f"{cfg.site_url}/rest/api/3/search/jql"
    headers = {
        "Authorization": _auth_header(cfg),
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    jql = f"project in ({projects_jql}) AND updated >= -10m ORDER BY updated DESC"
    body = {
        "jql": jql, "maxResults": 15,
        "fields": ["summary", "status", "priority", "issuetype", "assignee", "creator", "created", "updated"],
    }

    try:
        async with session.post(url, headers=headers, json=body) as resp:
            if resp.status != 200:
                log.warning("Jira project search returned %d", resp.status)
                return
            data = await resp.json()
    except Exception:
        log.exception("Jira project search failed")
        return

    for issue in data.get("issues", []):
        key = issue.get("key", "")
        fields = issue.get("fields", {})
        summary = fields.get("summary", "")
        status = fields.get("status", {}).get("name", "?")
        issue_type = fields.get("issuetype", {}).get("name", "?")
        creator = fields.get("creator", {}).get("displayName", "?")
        assignee_obj = fields.get("assignee")
        assignee = assignee_obj.get("displayName", "Unassigned") if assignee_obj else "Unassigned"

        created_str = fields.get("created", "")
        is_new = False
        if created_str:
            try:
                created_dt = datetime.fromisoformat(created_str.replace("Z", "+00:00"))
                is_new = (datetime.now(timezone.utc) - created_dt) < timedelta(minutes=10)
            except Exception:
                pass

        if is_new:
            new_id = f"jira-new-{key}"
            if has_seen(WATCHER, new_id):
                continue
            mark_seen(WATCHER, new_id)
            msg = f"{summary} ({issue_type}, {status}) by {creator}"
            emit_event(
                WATCHER, "info", f"Jira: {key}", f"New: {msg}",
                source="jira", action_url=f"{cfg.site_url}/browse/{key}",
            )
        else:
            state_key = f"jira-proj-status-{key}"
            prev_status = get_value(WATCHER, state_key)
            if prev_status and prev_status != status:
                set_value(WATCHER, state_key, status)
                msg = f"{summary} ({prev_status} → {status})"
                emit_event(
                    WATCHER, "info", f"Jira: {key}", msg,
                    source="jira", action_url=f"{cfg.site_url}/browse/{key}",
                )
            elif not prev_status:
                set_value(WATCHER, state_key, status)
