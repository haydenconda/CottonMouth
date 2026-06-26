"""Bedrock tool-use definitions and executors for CottonMouth Investigate."""
from __future__ import annotations

import json
import logging
import os
from base64 import b64encode

import aiohttp

log = logging.getLogger("cottonmouth.tools")

from pathlib import Path as _Path
_BASE_DIR = _Path(__file__).resolve().parents[1]

RESULT_MAX_CHARS = 4000

# ---------------------------------------------------------------------------
# Tool specifications (Bedrock Converse format)
# ---------------------------------------------------------------------------

TOOL_SPECS: list[dict] = [
    {
        "toolSpec": {
            "name": "slack_get_thread",
            "description": (
                "Fetch the full reply thread for a Slack message. "
                "Returns all replies with sender names and timestamps."
            ),
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "channel_id": {
                            "type": "string",
                            "description": "Slack channel ID (e.g. C05EFQ9TZPD)",
                        },
                        "thread_ts": {
                            "type": "string",
                            "description": "Timestamp of the parent message (e.g. 1775666313.831209)",
                        },
                    },
                    "required": ["channel_id", "thread_ts"],
                }
            },
        }
    },
    {
        "toolSpec": {
            "name": "slack_get_channel_history",
            "description": (
                "Get recent messages from a Slack channel. "
                "Returns the most recent messages with sender names and timestamps."
            ),
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "channel_id": {
                            "type": "string",
                            "description": "Slack channel ID (e.g. C05EFQ9TZPD)",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Number of messages to fetch (default 20, max 100)",
                        },
                    },
                    "required": ["channel_id"],
                }
            },
        }
    },
    {
        "toolSpec": {
            "name": "github_get_pr",
            "description": (
                "Fetch details for a GitHub pull request including title, body, "
                "changed files, review status, and CI check status."
            ),
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "owner": {
                            "type": "string",
                            "description": "Repository owner (e.g. anaconda)",
                        },
                        "repo": {
                            "type": "string",
                            "description": "Repository name (e.g. infra)",
                        },
                        "pr_number": {
                            "type": "integer",
                            "description": "Pull request number",
                        },
                    },
                    "required": ["owner", "repo", "pr_number"],
                }
            },
        }
    },
    {
        "toolSpec": {
            "name": "github_get_workflow_run",
            "description": (
                "Fetch details and logs for a GitHub Actions workflow run, "
                "including status, conclusion, and failed job info."
            ),
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "owner": {
                            "type": "string",
                            "description": "Repository owner",
                        },
                        "repo": {
                            "type": "string",
                            "description": "Repository name",
                        },
                        "run_id": {
                            "type": "integer",
                            "description": "Workflow run ID",
                        },
                    },
                    "required": ["owner", "repo", "run_id"],
                }
            },
        }
    },
    {
        "toolSpec": {
            "name": "get_trace",
            "description": (
                "Fetch a complete agent trace by trace_id. Returns all spans "
                "(LLM calls, tool calls, decisions) with timing, status, and cost."
            ),
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "trace_id": {
                            "type": "string",
                            "description": "Trace ID to fetch",
                        },
                    },
                    "required": ["trace_id"],
                }
            },
        }
    },
    {
        "toolSpec": {
            "name": "get_agent_runs",
            "description": (
                "List recent agent runs, optionally filtered by agent name or status. "
                "Returns summary of each run including duration, cost, and error info."
            ),
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "agent_name": {
                            "type": "string",
                            "description": "Filter by agent name (optional)",
                        },
                        "status": {
                            "type": "string",
                            "description": "Filter by status: completed, failed (optional)",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Max results (default 20)",
                        },
                    },
                    "required": [],
                }
            },
        }
    },
    {
        "toolSpec": {
            "name": "get_span_detail",
            "description": (
                "Fetch full details for a specific span including input/output data, "
                "token counts, cost, and error information."
            ),
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "trace_id": {
                            "type": "string",
                            "description": "Trace ID containing the span",
                        },
                        "span_id": {
                            "type": "string",
                            "description": "Span ID to fetch",
                        },
                    },
                    "required": ["trace_id", "span_id"],
                }
            },
        }
    },
    {
        "toolSpec": {
            "name": "get_agent_stats",
            "description": (
                "Get rolling statistics for an agent: average latency, total cost, "
                "error rate, run count."
            ),
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "agent_name": {
                            "type": "string",
                            "description": "Agent name to get stats for",
                        },
                    },
                    "required": ["agent_name"],
                }
            },
        }
    },
    {
        "toolSpec": {
            "name": "search_traces",
            "description": (
                "Search across traces by error message, agent name, or status. "
                "Returns matching spans with context."
            ),
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Search text (matches against error messages, agent names, tool names)",
                        },
                        "agent_name": {
                            "type": "string",
                            "description": "Filter by agent name (optional)",
                        },
                        "status": {
                            "type": "string",
                            "description": "Filter by status (optional)",
                        },
                        "limit": {
                            "type": "integer",
                            "description": "Max results (default 20)",
                        },
                    },
                    "required": ["query"],
                }
            },
        }
    },
    {
        "toolSpec": {
            "name": "jira_get_issue",
            "description": (
                "Fetch full details for a Jira issue including summary, description, "
                "status, assignee, priority, and recent comments."
            ),
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "issue_key": {
                            "type": "string",
                            "description": "Jira issue key (e.g. CORE-1234)",
                        },
                    },
                    "required": ["issue_key"],
                }
            },
        }
    },
]

# ---------------------------------------------------------------------------
# Shared HTTP helpers
# ---------------------------------------------------------------------------

_SLACK_API = "https://slack.com/api"
_GH_API = "https://api.github.com"


def _slack_headers() -> dict[str, str]:
    token = os.environ.get("SLACK_USER_TOKEN") or os.environ.get("SLACK_BOT_TOKEN", "")
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _gh_headers() -> dict[str, str]:
    token = os.environ.get("GITHUB_TOKEN", "")
    return {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _jira_headers() -> dict[str, str]:
    email = os.environ.get("ATLASSIAN_USER_EMAIL", "")
    api_token = os.environ.get("ATLASSIAN_API_TOKEN", "")
    creds = b64encode(f"{email}:{api_token}".encode()).decode()
    return {
        "Authorization": f"Basic {creds}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def _truncate(text: str, max_chars: int = RESULT_MAX_CHARS) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n... (truncated)"


# ---------------------------------------------------------------------------
# Slack user-name cache (resolve user IDs to display names within a session)
# ---------------------------------------------------------------------------

_user_cache: dict[str, str] = {}


async def _resolve_slack_user(session: aiohttp.ClientSession, user_id: str) -> str:
    if user_id in _user_cache:
        return _user_cache[user_id]
    try:
        async with session.get(
            f"{_SLACK_API}/users.info",
            headers=_slack_headers(),
            params={"user": user_id},
        ) as resp:
            data = await resp.json()
            if data.get("ok"):
                profile = data["user"].get("profile", {})
                name = profile.get("display_name") or profile.get("real_name") or user_id
                _user_cache[user_id] = name
                return name
    except Exception:
        pass
    return user_id


# ---------------------------------------------------------------------------
# Tool executors
# ---------------------------------------------------------------------------

async def _exec_slack_get_thread(args: dict) -> dict:
    channel_id = args["channel_id"]
    thread_ts = args["thread_ts"]

    async with aiohttp.ClientSession() as session:
        async with session.get(
            f"{_SLACK_API}/conversations.replies",
            headers=_slack_headers(),
            params={"channel": channel_id, "ts": thread_ts, "limit": "50"},
        ) as resp:
            data = await resp.json()

        if not data.get("ok"):
            return {"error": data.get("error", "Unknown Slack API error")}

        messages = data.get("messages", [])
        result_lines: list[str] = []
        for msg in messages:
            user_id = msg.get("user", "unknown")
            name = await _resolve_slack_user(session, user_id)
            text = msg.get("text", "")
            ts = msg.get("ts", "")
            result_lines.append(f"[{ts}] {name}: {text}")

        return {"thread": result_lines, "message_count": len(messages)}


async def _exec_slack_get_channel_history(args: dict) -> dict:
    channel_id = args["channel_id"]
    limit = min(args.get("limit", 20), 100)

    async with aiohttp.ClientSession() as session:
        async with session.get(
            f"{_SLACK_API}/conversations.history",
            headers=_slack_headers(),
            params={"channel": channel_id, "limit": str(limit)},
        ) as resp:
            data = await resp.json()

        if not data.get("ok"):
            return {"error": data.get("error", "Unknown Slack API error")}

        messages = data.get("messages", [])
        result_lines: list[str] = []
        for msg in messages:
            user_id = msg.get("user", "unknown")
            name = await _resolve_slack_user(session, user_id)
            text = msg.get("text", "")[:300]
            ts = msg.get("ts", "")
            thread_count = msg.get("reply_count", 0)
            line = f"[{ts}] {name}: {text}"
            if thread_count:
                line += f" ({thread_count} replies)"
            result_lines.append(line)

        return {"messages": result_lines, "message_count": len(messages)}


async def _exec_github_get_pr(args: dict) -> dict:
    owner = args["owner"]
    repo = args["repo"]
    pr_number = args["pr_number"]

    async with aiohttp.ClientSession() as session:
        async with session.get(
            f"{_GH_API}/repos/{owner}/{repo}/pulls/{pr_number}",
            headers=_gh_headers(),
        ) as resp:
            if resp.status != 200:
                return {"error": f"GitHub API {resp.status}"}
            pr = await resp.json()

        async with session.get(
            f"{_GH_API}/repos/{owner}/{repo}/pulls/{pr_number}/files",
            headers=_gh_headers(),
            params={"per_page": "30"},
        ) as resp:
            files = await resp.json() if resp.status == 200 else []

        async with session.get(
            f"{_GH_API}/repos/{owner}/{repo}/pulls/{pr_number}/reviews",
            headers=_gh_headers(),
        ) as resp:
            reviews = await resp.json() if resp.status == 200 else []

    file_list = [f.get("filename", "") for f in files] if isinstance(files, list) else []
    review_summary = []
    if isinstance(reviews, list):
        for r in reviews:
            review_summary.append({
                "user": r.get("user", {}).get("login", ""),
                "state": r.get("state", ""),
            })

    return {
        "title": pr.get("title", ""),
        "author": pr.get("user", {}).get("login", ""),
        "state": pr.get("state", ""),
        "draft": pr.get("draft", False),
        "mergeable": pr.get("mergeable"),
        "body": (pr.get("body") or "")[:1000],
        "additions": pr.get("additions", 0),
        "deletions": pr.get("deletions", 0),
        "changed_files": pr.get("changed_files", 0),
        "files": file_list[:20],
        "reviews": review_summary,
        "html_url": pr.get("html_url", ""),
    }


async def _exec_github_get_workflow_run(args: dict) -> dict:
    owner = args["owner"]
    repo = args["repo"]
    run_id = args["run_id"]

    async with aiohttp.ClientSession() as session:
        async with session.get(
            f"{_GH_API}/repos/{owner}/{repo}/actions/runs/{run_id}",
            headers=_gh_headers(),
        ) as resp:
            if resp.status != 200:
                return {"error": f"GitHub API {resp.status}"}
            run = await resp.json()

        async with session.get(
            f"{_GH_API}/repos/{owner}/{repo}/actions/runs/{run_id}/jobs",
            headers=_gh_headers(),
        ) as resp:
            jobs_data = await resp.json() if resp.status == 200 else {}

    jobs = jobs_data.get("jobs", [])
    failed_jobs = []
    for job in jobs:
        if job.get("conclusion") == "failure":
            failed_steps = [
                s.get("name", "")
                for s in job.get("steps", [])
                if s.get("conclusion") == "failure"
            ]
            failed_jobs.append({
                "name": job.get("name", ""),
                "failed_steps": failed_steps,
            })

    return {
        "workflow": run.get("name", ""),
        "status": run.get("status", ""),
        "conclusion": run.get("conclusion", ""),
        "branch": run.get("head_branch", ""),
        "event": run.get("event", ""),
        "actor": run.get("actor", {}).get("login", ""),
        "html_url": run.get("html_url", ""),
        "failed_jobs": failed_jobs,
        "total_jobs": len(jobs),
    }


async def _exec_jira_get_issue(args: dict) -> dict:
    issue_key = args["issue_key"]
    site_url = os.environ.get("ATLASSIAN_SITE_URL", "")
    if not site_url:
        return {"error": "ATLASSIAN_SITE_URL not configured"}

    url = f"{site_url}/rest/api/3/issue/{issue_key}"
    params = {"fields": "summary,status,assignee,priority,creator,issuetype,comment,description,updated"}

    async with aiohttp.ClientSession() as session:
        async with session.get(url, headers=_jira_headers(), params=params) as resp:
            if resp.status != 200:
                return {"error": f"Jira API {resp.status}"}
            issue = await resp.json()

    fields = issue.get("fields", {})

    comments_raw = fields.get("comment", {}).get("comments", [])
    recent_comments = []
    for c in comments_raw[-5:]:
        author = c.get("author", {}).get("displayName", "")
        body_adf = c.get("body", {})
        body_text = _adf_to_text(body_adf)[:500]
        recent_comments.append({"author": author, "text": body_text})

    description_adf = fields.get("description") or {}
    description_text = _adf_to_text(description_adf)[:1000]

    return {
        "key": issue_key,
        "summary": fields.get("summary", ""),
        "status": (fields.get("status") or {}).get("name", ""),
        "priority": (fields.get("priority") or {}).get("name", ""),
        "assignee": (fields.get("assignee") or {}).get("displayName", "Unassigned"),
        "creator": (fields.get("creator") or {}).get("displayName", ""),
        "type": (fields.get("issuetype") or {}).get("name", ""),
        "updated": fields.get("updated", ""),
        "description": description_text,
        "recent_comments": recent_comments,
        "url": f"{site_url}/browse/{issue_key}",
    }


def _adf_to_text(adf: dict) -> str:
    """Extract plain text from Atlassian Document Format."""
    if not isinstance(adf, dict):
        return ""
    parts: list[str] = []
    for node in adf.get("content", []):
        if node.get("type") == "paragraph":
            for inline in node.get("content", []):
                if inline.get("type") == "text":
                    parts.append(inline.get("text", ""))
        elif node.get("type") == "text":
            parts.append(node.get("text", ""))
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Agent trace tool executors
# ---------------------------------------------------------------------------

# Only the most recent spans are kept in memory. The trace file is append-only
# and grows unbounded (an infra-failure burst once pushed it to 164MB / 227k
# lines, which OOM-killed the backend when loaded whole and blocked the event
# loop long enough for the liveness probe to kill the pod). We seek to the tail
# of the file and retain just the most recent lines -- enough for the
# dashboard's recent-window stats -- which bounds BOTH memory and IO/CPU
# regardless of on-disk size.
_TRACES_WINDOW = 12000

# Never read more than this many bytes from the end of the file. At ~750
# bytes/span this comfortably holds far more than _TRACES_WINDOW lines, so the
# window is line-bounded in the common case and byte-bounded as a hard backstop
# for pathologically large files.
_MAX_TAIL_BYTES = 48 * 1024 * 1024

# Parsed-trace cache keyed on the file's (mtime, size). The file is append-only,
# so any write changes the key and invalidates the cache; between writes the many
# dashboard polls (agents/traces/events) reuse one parse instead of re-streaming.
_TRACES_CACHE: dict[str, object] = {"key": None, "spans": []}


def _read_tail_lines(path, max_lines: int, max_bytes: int) -> list[str]:
    """Return up to ``max_lines`` lines from the end of ``path`` without reading
    more than ``max_bytes`` from disk.

    Seeks to ``size - max_bytes`` (discarding the first, likely-partial line)
    so peak memory and IO are independent of the file's total size.
    """
    from collections import deque
    try:
        size = path.stat().st_size
    except OSError:
        return []
    try:
        with open(path, "rb") as f:
            if size > max_bytes:
                f.seek(size - max_bytes)
                f.readline()  # drop the partial line at the seek boundary
            tail = deque(f, maxlen=max_lines)
    except OSError:
        return []
    return [ln.decode("utf-8", "replace") for ln in tail]


def _load_traces_file() -> list[dict]:
    """Return the most recent spans (up to ``_TRACES_WINDOW``).

    Reads only the tail of the file (bounded by ``_MAX_TAIL_BYTES`` /
    ``_TRACES_WINDOW``) so peak memory and IO are independent of the (unbounded)
    on-disk file size, and caches the parsed window keyed on the file's
    mtime/size.
    """
    from src.common.paths import traces_file as _traces_file
    traces_file = _traces_file()
    if not traces_file.exists():
        return []
    try:
        st = traces_file.stat()
        key = (st.st_mtime_ns, st.st_size)
    except OSError:
        key = None

    if key is not None and _TRACES_CACHE["key"] == key:
        return _TRACES_CACHE["spans"]  # type: ignore[return-value]

    spans: list[dict] = []
    for line in _read_tail_lines(traces_file, _TRACES_WINDOW, _MAX_TAIL_BYTES):
        line = line.strip()
        if line:
            try:
                spans.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    if key is not None:
        _TRACES_CACHE["key"] = key
        _TRACES_CACHE["spans"] = spans
    return spans


async def _exec_get_trace(args: dict) -> dict:
    trace_id = args["trace_id"]
    all_spans = _load_traces_file()
    matching = [s for s in all_spans if s.get("trace_id") == trace_id]
    if not matching:
        return {"error": f"No trace found with id {trace_id}"}
    matching.sort(key=lambda s: s.get("start_time", ""))
    summary = []
    for s in matching:
        summary.append({
            "span_id": s.get("span_id", ""),
            # Canonical keys consumed by the web UI (Span); parent/input_tokens/
            # output_tokens kept as aliases for the Bedrock investigate tool.
            "parent_span_id": s.get("parent_span_id", ""),
            "parent": s.get("parent_span_id", ""),
            "type": s.get("span_type", ""),
            "name": s.get("name", ""),
            "status": s.get("status", ""),
            "started_at": s.get("start_time", ""),
            "ended_at": s.get("end_time", ""),
            "duration_ms": s.get("duration_ms", 0),
            "model": s.get("model", ""),
            "tokens_in": s.get("input_tokens", 0),
            "tokens_out": s.get("output_tokens", 0),
            "input_tokens": s.get("input_tokens", 0),
            "output_tokens": s.get("output_tokens", 0),
            "cost_usd": s.get("cost_usd", 0),
            "input": s.get("input_data") or s.get("input"),
            "output": s.get("output_data") or s.get("output"),
            "error": (s.get("error") or "")[:200],
            "tool_name": s.get("tool_name", ""),
            "tool_input": s.get("tool_input") or {},
            "tool_output": s.get("tool_output") or {},
            # Decision pillar ("why did it do it").
            "decision_type": s.get("decision_type", ""),
            "options_considered": s.get("options_considered") or [],
            "chosen_option": s.get("chosen_option", ""),
            "reasoning": s.get("reasoning", ""),
            # Permission pillar ("what was it allowed to do").
            "permission_result": s.get("permission_result", ""),
            "permission_policy": s.get("permission_policy", ""),
            # Span metadata: integration source (e.g. "litellm"), provider, call
            # origination (caller/host/pod), and gateway-policy decision.
            "metadata": s.get("metadata") or {},
        })
    total_cost = sum(s.get("cost_usd", 0) for s in matching)
    root = next((s for s in matching if s.get("span_type") == "agent_run"), matching[0])
    total_duration = max(
        (s.get("duration_ms", 0) for s in matching if s.get("span_type") == "agent_run"),
        default=0,
    )
    agent_name = root.get("agent_name", "unknown")
    return {
        "trace_id": trace_id,
        "agent_name": agent_name,
        "status": root.get("status", ""),
        "started_at": root.get("start_time", ""),
        "total_duration_ms": total_duration,
        "total_cost_usd": round(total_cost, 6),
        "span_count": len(matching),
        "spans": summary,
    }


async def _exec_get_agent_runs(args: dict) -> dict:
    agent_filter = args.get("agent_name", "")
    status_filter = args.get("status", "")
    limit = min(args.get("limit", 20), 50)

    all_spans = _load_traces_file()
    root_spans = [s for s in all_spans if s.get("span_type") == "agent_run"]
    if agent_filter:
        root_spans = [s for s in root_spans if s.get("agent_name") == agent_filter]
    if status_filter:
        root_spans = [s for s in root_spans if s.get("status") == status_filter]
    root_spans.sort(key=lambda s: s.get("start_time", ""), reverse=True)
    root_spans = root_spans[:limit]

    runs = []
    for r in root_spans:
        tid = r.get("trace_id", "")
        trace_spans = [s for s in all_spans if s.get("trace_id") == tid]
        total_cost = sum(s.get("cost_usd", 0) for s in trace_spans)
        cost = round(total_cost, 6)
        start = r.get("start_time", "")
        runs.append({
            "trace_id": tid,
            "agent_name": r.get("agent_name", ""),
            "status": r.get("status", ""),
            "duration_ms": r.get("duration_ms", 0),
            # Canonical keys consumed by the web UI (TraceRun); start_time/cost_usd
            # kept as aliases for the Bedrock investigate tool callers.
            "started_at": start,
            "total_cost_usd": cost,
            "start_time": start,
            "cost_usd": cost,
            "error": r.get("error", "")[:100],
            "span_count": len(trace_spans),
        })
    return {"runs": runs, "total": len(runs)}


async def _exec_get_span_detail(args: dict) -> dict:
    trace_id = args["trace_id"]
    span_id = args["span_id"]
    all_spans = _load_traces_file()
    for s in all_spans:
        if s.get("trace_id") == trace_id and s.get("span_id") == span_id:
            return s
    return {"error": f"Span {span_id} not found in trace {trace_id}"}


async def _exec_get_agent_stats(args: dict) -> dict:
    """Agent stats derived from the retained traces (single source of truth,
    consistent with the dashboard). Infra failures are excluded from the error
    rate and reported via ``infra_failure_count``."""
    from src.common.agent_stats import compute_agent_stats
    agent_name = args["agent_name"]
    return compute_agent_stats(agent_name, _load_traces_file())


async def _exec_search_traces(args: dict) -> dict:
    query = args["query"].lower()
    agent_filter = args.get("agent_name", "")
    status_filter = args.get("status", "")
    limit = min(args.get("limit", 20), 50)

    all_spans = _load_traces_file()
    matches = []
    for s in all_spans:
        if agent_filter and s.get("agent_name") != agent_filter:
            continue
        if status_filter and s.get("status") != status_filter:
            continue
        searchable = " ".join([
            s.get("error", ""),
            s.get("agent_name", ""),
            s.get("name", ""),
            s.get("tool_name", ""),
            s.get("model", ""),
        ]).lower()
        if query in searchable:
            matches.append({
                "trace_id": s.get("trace_id", ""),
                "span_id": s.get("span_id", ""),
                "agent_name": s.get("agent_name", ""),
                "span_type": s.get("span_type", ""),
                "name": s.get("name", ""),
                "status": s.get("status", ""),
                "error": s.get("error", "")[:200],
                "start_time": s.get("start_time", ""),
            })
            if len(matches) >= limit:
                break
    return {"matches": matches, "total": len(matches)}


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

_EXECUTORS: dict[str, callable] = {
    "slack_get_thread": _exec_slack_get_thread,
    "slack_get_channel_history": _exec_slack_get_channel_history,
    "github_get_pr": _exec_github_get_pr,
    "github_get_workflow_run": _exec_github_get_workflow_run,
    "jira_get_issue": _exec_jira_get_issue,
    "get_trace": _exec_get_trace,
    "get_agent_runs": _exec_get_agent_runs,
    "get_span_detail": _exec_get_span_detail,
    "get_agent_stats": _exec_get_agent_stats,
    "search_traces": _exec_search_traces,
}


async def execute_tool(name: str, args: dict) -> str:
    """Run a tool by name, return JSON string result (truncated to fit context)."""
    executor = _EXECUTORS.get(name)
    if not executor:
        return json.dumps({"error": f"Unknown tool: {name}"})

    log.info("Executing tool: %s(%s)", name, json.dumps(args)[:200])
    try:
        result = await executor(args)
        result_str = json.dumps(result, ensure_ascii=False, default=str)
        return _truncate(result_str)
    except Exception as e:
        log.exception("Tool %s failed", name)
        return json.dumps({"error": f"Tool execution failed: {e}"})
