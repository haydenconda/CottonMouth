from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import aiohttp

from src.common.config import GitHubConfig
from src.common.events import emit_event
from src.common.notify import send_notification
from src.common.state import has_seen, mark_seen, prune_old
from src.watchers import BaseWatcher, WatcherResult, WATCHER

log = logging.getLogger("cottonmouth.watcher.github")
GH_API = "https://api.github.com"


def _headers(cfg: GitHubConfig) -> dict[str, str]:
    return {
        "Authorization": f"token {cfg.token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


class GitHubWatcher(BaseWatcher):
    name = "github"

    def __init__(self, cfg: GitHubConfig) -> None:
        self.cfg = cfg

    async def poll(self, session: aiohttp.ClientSession) -> WatcherResult:
        try:
            await _check_review_requests(session, self.cfg)
            await _check_failed_actions(session, self.cfg)
            await _check_watched_repo_prs(session, self.cfg)
            await _check_watched_repo_mentions(session, self.cfg)
            await _check_watched_repo_comments(session, self.cfg)
        except Exception as exc:
            log.exception("GitHub watcher failed")
            return WatcherResult(ok=False, error=str(exc))
        return WatcherResult()


async def _check_review_requests(session: aiohttp.ClientSession, cfg: GitHubConfig) -> None:
    url = f"{GH_API}/search/issues"
    query = f"is:open is:pr review-requested:{cfg.username} archived:false"
    params = {"q": query, "sort": "updated", "per_page": "20"}

    try:
        async with session.get(url, headers=_headers(cfg), params=params) as resp:
            if resp.status != 200:
                return
            data = await resp.json()
    except Exception:
        log.exception("GitHub review request check failed")
        return

    for item in data.get("items", []):
        pr_id = f"review-{item['id']}"
        if has_seen(WATCHER, pr_id):
            continue
        mark_seen(WATCHER, pr_id)
        repo = item.get("repository_url", "").split("/")[-1]
        title = item.get("title", "")
        user = item.get("user", {}).get("login", "?")
        msg = f"{repo}: {title}"
        await send_notification("PR Review Requested", msg, subtitle=f"From {user}", severity="warning")
        emit_event(
            WATCHER, "warning", "PR Review Requested", f"{msg} (from {user})",
            source="github-pr", action_url=item.get("html_url", ""),
        )


async def _check_failed_actions(session: aiohttp.ClientSession, cfg: GitHubConfig) -> None:
    repos_url = f"{GH_API}/user/repos"
    params = {"sort": "pushed", "per_page": "10", "affiliation": "owner,collaborator"}

    try:
        async with session.get(repos_url, headers=_headers(cfg), params=params) as resp:
            if resp.status != 200:
                return
            repos = await resp.json()
    except Exception:
        log.exception("GitHub repos check failed")
        return

    for repo in repos:
        full_name = repo.get("full_name", "")
        runs_url = f"{GH_API}/repos/{full_name}/actions/runs"
        runs_params = {"status": "failure", "per_page": "5"}

        try:
            async with session.get(runs_url, headers=_headers(cfg), params=runs_params) as resp:
                if resp.status != 200:
                    continue
                runs_data = await resp.json()
        except Exception:
            continue

        for run_item in runs_data.get("workflow_runs", []):
            run_id = f"action-{run_item['id']}"
            if has_seen(WATCHER, run_id):
                continue
            mark_seen(WATCHER, run_id)
            name = run_item.get("name", "workflow")
            branch = run_item.get("head_branch", "?")
            repo_name = full_name.split("/")[-1]
            msg = f"{repo_name}/{name} on {branch}"
            await send_notification("GitHub Actions Failed", msg, subtitle="Workflow run failed", severity="critical")
            emit_event(
                WATCHER, "critical", "GitHub Actions Failed", msg,
                source="github-actions", action_url=run_item.get("html_url", ""),
            )

    prune_old(WATCHER, keep_latest=500)


async def _check_watched_repo_prs(session: aiohttp.ClientSession, cfg: GitHubConfig) -> None:
    headers = _headers(cfg)
    for repo in cfg.watched_repos:
        repo_short = repo.split("/")[-1]
        url = f"{GH_API}/repos/{repo}/pulls"
        params = {"state": "open", "sort": "updated", "direction": "desc", "per_page": "25"}

        try:
            async with session.get(url, headers=headers, params=params) as resp:
                if resp.status != 200:
                    continue
                prs = await resp.json()
        except Exception:
            continue

        for pr in prs:
            pr_id = f"watched-pr-{repo}-{pr['number']}"
            if has_seen(WATCHER, pr_id):
                continue
            mark_seen(WATCHER, pr_id)

            title = pr.get("title", "")
            author = pr.get("user", {}).get("login", "?")
            html_url = pr.get("html_url", "")
            draft = " [draft]" if pr.get("draft") else ""
            emit_event(
                WATCHER, "info", f"PR: {repo_short}#{pr['number']}", f"{title} (by {author}){draft}",
                source="github-pr", action_url=html_url,
            )


async def _check_watched_repo_mentions(session: aiohttp.ClientSession, cfg: GitHubConfig) -> None:
    headers = _headers(cfg)
    repos_filter = " ".join(f"repo:{r}" for r in cfg.watched_repos)
    query = f"mentions:{cfg.username} is:open {repos_filter}"
    url = f"{GH_API}/search/issues"
    params = {"q": query, "sort": "updated", "order": "desc", "per_page": "30"}

    try:
        async with session.get(url, headers=headers, params=params) as resp:
            if resp.status != 200:
                return
            data = await resp.json()
    except Exception:
        log.exception("GitHub mention search failed")
        return

    for item in data.get("items", []):
        mention_id = f"mention-{item['id']}"
        if has_seen(WATCHER, mention_id):
            continue
        mark_seen(WATCHER, mention_id)

        repo_name = item.get("repository_url", "").split("/")[-1]
        title = item.get("title", "")
        author = item.get("user", {}).get("login", "?")
        html_url = item.get("html_url", "")
        kind = "PR" if item.get("pull_request") else "Issue"
        await send_notification(f"Mentioned in {kind}", f"{repo_name}: {title}", subtitle=f"by {author}", severity="warning")
        emit_event(
            WATCHER, "warning", f"Mentioned in {kind}: {repo_name}", f"{title} (by {author})",
            source="github-mention", action_url=html_url,
        )


async def _check_watched_repo_comments(session: aiohttp.ClientSession, cfg: GitHubConfig) -> None:
    headers = _headers(cfg)
    since = (datetime.now(timezone.utc) - timedelta(seconds=cfg.poll_interval + 30)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )

    for repo in cfg.watched_repos:
        repo_short = repo.split("/")[-1]
        for endpoint in ("issues/comments", "pulls/comments"):
            url = f"{GH_API}/repos/{repo}/{endpoint}"
            params = {"since": since, "sort": "updated", "direction": "desc", "per_page": "30"}

            try:
                async with session.get(url, headers=headers, params=params) as resp:
                    if resp.status != 200:
                        continue
                    comments = await resp.json()
            except Exception:
                continue

            for comment in comments:
                body = comment.get("body", "")
                if f"@{cfg.username}" not in body:
                    continue

                cid = f"gh-comment-{comment['id']}"
                if has_seen(WATCHER, cid):
                    continue
                mark_seen(WATCHER, cid)

                author = comment.get("user", {}).get("login", "?")
                html_url = comment.get("html_url", "")
                snippet = body[:150]
                await send_notification(
                    "GitHub Comment Mention", f"{repo_short}: {author} mentioned you",
                    subtitle=snippet, severity="warning",
                )
                emit_event(
                    WATCHER, "warning", f"Comment mention: {repo_short}", f"{author}: {snippet}",
                    source="github-mention", action_url=html_url,
                )
