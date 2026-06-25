from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

import boto3
from botocore.config import Config as BotoConfig

from src.common.config import SlackConfig
from src.common.paths import data_dir
from src.tools import TOOL_SPECS, execute_tool

log = logging.getLogger("cottonmouth.query-router")

BASE_DIR = data_dir()
QUERIES_FILE = BASE_DIR / "queries.jsonl"
RESPONSES_FILE = BASE_DIR / "responses.jsonl"

BEDROCK_MODEL = os.environ.get("BEDROCK_MODEL", "us.anthropic.claude-opus-4-6-v1")
BEDROCK_REGION = os.environ.get("BEDROCK_REGION", "us-east-1")
# Empty AWS_PROFILE => use the default credential chain (IRSA / instance role /
# env vars). A named profile is only used for local SSO development.
AWS_PROFILE = os.environ.get("AWS_PROFILE", "")
BEDROCK_MAX_TOOL_ROUNDS = int(os.environ.get("BEDROCK_MAX_TOOL_ROUNDS", "5"))

_bedrock_client = None


def _get_bedrock_client():
    global _bedrock_client
    if _bedrock_client is None:
        session = boto3.Session(profile_name=AWS_PROFILE) if AWS_PROFILE else boto3.Session()
        _bedrock_client = session.client(
            "bedrock-runtime",
            region_name=BEDROCK_REGION,
            config=BotoConfig(
                connect_timeout=60,
                read_timeout=120,
                retries={"max_attempts": 2},
            ),
        )
    return _bedrock_client

_channel_map: dict[str, str] = {}

# ---------------------------------------------------------------------------
# Session-based conversation history
# ---------------------------------------------------------------------------

_SESSION_MAX_TURNS = 12
SESSION_CONTEXT_DIR = BASE_DIR / "session_context"


class _SessionHistory:
    """Keeps per-session conversation turns, backed by disk for restart survival."""

    def __init__(self) -> None:
        self._sessions: dict[str, list[dict]] = {}
        SESSION_CONTEXT_DIR.mkdir(parents=True, exist_ok=True)

    def _load(self, session_id: str) -> list[dict]:
        if session_id in self._sessions:
            return self._sessions[session_id]
        path = SESSION_CONTEXT_DIR / f"{session_id}.json"
        if path.exists():
            try:
                turns = json.loads(path.read_text(encoding="utf-8"))
                self._sessions[session_id] = turns
                return turns
            except Exception:
                log.warning("Failed to load session context for %s", session_id)
        self._sessions[session_id] = []
        return self._sessions[session_id]

    def add(self, session_id: str, question: str, answer: str) -> None:
        turns = self._load(session_id)
        turns.append({"q": question, "a": answer})
        if len(turns) > _SESSION_MAX_TURNS:
            self._sessions[session_id] = turns[-_SESSION_MAX_TURNS:]
        self._persist(session_id)

    def get_context(self, session_id: str) -> str:
        turns = self._load(session_id)
        if not turns:
            return ""
        lines: list[str] = []
        for t in turns:
            lines.append(f"User: {t['q']}")
            lines.append(f"You: {t['a'][:500]}")
        return "\n".join(lines)

    def _persist(self, session_id: str) -> None:
        turns = self._sessions.get(session_id, [])
        path = SESSION_CONTEXT_DIR / f"{session_id}.json"
        try:
            path.write_text(json.dumps(turns, ensure_ascii=False), encoding="utf-8")
        except Exception:
            log.warning("Failed to persist session context for %s", session_id)


_sessions = _SessionHistory()


AGENT_PROMPT_TEMPLATE = (
    "You are CottonMouth — an AI agent observability and investigation specialist. "
    "You analyze agent traces, failures, cost anomalies, and performance regressions. "
    "You also monitor supporting infrastructure via Slack, GitHub, Jira, ArgoCD, Grafana, CloudWatch, and Cloudflare.\n"
    "\n"
    "You have tools to inspect agent traces (get_trace, get_agent_runs, get_span_detail, "
    "get_agent_stats, search_traces) and fetch live data from infrastructure services "
    "(Slack, GitHub, Jira). Use them when the event context alone isn't enough. "
    "For agent events, always fetch the full trace before diagnosing.\n"
    "\n"
    "CONTEXT:\n"
    "- Slack channels: {channel_map}\n"
    "\n"
    "RESPONSE FORMAT:\n"
    "- 3-5 bullet points max. Under 150 words total.\n"
    "- Lead with what happened, then root cause analysis, then suggested next steps.\n"
    "- For agent failures: identify the failing span, explain why it failed, and suggest a fix.\n"
    "- For cost anomalies: break down where tokens were spent and what's abnormal.\n"
    "- No preamble, no greeting, no sign-off.\n"
    "- Never fabricate data you didn't retrieve via a tool or see in the context.\n"
    "- If a tool call fails, note it briefly and work with what you have.\n"
)


def _build_prompt() -> str:
    if _channel_map:
        lines = ", ".join(f"{name}={cid}" for name, cid in sorted(_channel_map.items()))
    else:
        lines = "(no channels resolved yet)"
    return AGENT_PROMPT_TEMPLATE.replace("{channel_map}", lines)


async def _resolve_channel_map() -> dict[str, str]:
    from src.common.slack import resolve_channel_map
    return await resolve_channel_map(SlackConfig())


def _build_event_context_block(event_ctx: dict) -> str:
    """Build a prompt section that grounds the agent on the specific event being investigated."""
    source = event_ctx.get("source", "")
    severity = event_ctx.get("severity", "")
    title = event_ctx.get("title", "")
    message = event_ctx.get("message", "")
    action_url = event_ctx.get("action_url", "")
    event_ts = event_ctx.get("event_ts", "")

    lines = [
        "EVENT CONTEXT — The user is investigating this specific event:",
        f"  Source: {source}",
        f"  Severity: {severity}",
        f"  Title: {title}",
        f"  Message: {message}",
    ]
    if action_url:
        lines.append(f"  URL: {action_url}")
    if event_ts:
        lines.append(f"  Timestamp: {event_ts}")

    source_hints = _source_investigation_hints(source, title, message, action_url)
    if source_hints:
        lines.append("")
        lines.append("INVESTIGATION HINTS:")
        lines.extend(f"  - {h}" for h in source_hints)

    lines.append("")
    lines.append(
        "Use your tools to investigate this event. Fetch the actual data, then provide "
        "a concise summary. Be specific and actionable."
    )
    return "\n".join(lines)


def _source_investigation_hints(source: str, title: str, message: str, url: str) -> list[str]:
    """Provide source-specific investigation guidance using available tools."""
    hints: list[str] = []
    combined = f"{title} {message}".lower()

    if source.startswith("slack"):
        hints.append("Use slack_get_thread or slack_get_channel_history to get the full conversation context")
        if "mention" in source:
            hints.append("The user was @mentioned — fetch the thread to see what's being asked of them")
    elif source == "jira":
        import re
        keys = re.findall(r"[A-Z]+-\d+", f"{title} {message}")
        if keys:
            hints.append(f"Use jira_get_issue to fetch full details for {keys[0]}")
    elif source.startswith("github"):
        if "PR" in title or "pull" in combined:
            hints.append("Use github_get_pr to fetch PR details, files changed, and review status")
        if "Actions" in title or "failed" in combined:
            hints.append("Use github_get_workflow_run to get the failed job and step details")
    elif source == "argocd":
        hints.append("Summarize the sync/health status and whether intervention is needed")
    elif source == "cloudwatch":
        hints.append("Summarize the alarm: what metric, threshold, and likely impact")
    elif source == "grafana":
        hints.append("Summarize the alert state and what metric triggered it")
    elif source == "cloudtrail":
        hints.append("This is a security event — identify the IAM action, actor, and risk level")
    elif source == "cloudflare":
        hints.append("Summarize the zone/worker status and any impact")
    elif source == "agent-trace":
        hints.append("Use get_trace to fetch the full trace and all spans")
        hints.append("Use get_agent_stats to check if this run's duration/cost is anomalous")
    elif source == "agent-error":
        hints.append("Use get_trace to fetch the full trace, then get_span_detail on the failing span")
        hints.append("Identify the root cause: was it an LLM error, tool failure, or timeout?")
        hints.append("Check get_agent_stats to see if this agent has a recurring failure pattern")
    elif source == "agent-anomaly":
        hints.append("Use get_trace to fetch the full trace and identify which spans drove the anomaly")
        hints.append("Use get_agent_stats to compare against historical averages")
        hints.append("For cost spikes, check which LLM calls consumed the most tokens")

    return hints


async def _ask_bedrock(question: str, session_id: str = "", event_context: dict | None = None) -> str:
    system_prompt = _build_prompt()
    if event_context:
        system_prompt += "\n" + _build_event_context_block(event_context) + "\n\n"

    context = _sessions.get_context(session_id) if session_id else ""

    if context:
        user_content = f"Conversation so far:\n{context}\n\nNew question: {question}"
    else:
        user_content = question

    messages: list[dict] = [{"role": "user", "content": [{"text": user_content}]}]
    client = _get_bedrock_client()
    loop = asyncio.get_running_loop()
    total_input = 0
    total_output = 0

    log.info("Bedrock request: model=%s, prompt_len=%d", BEDROCK_MODEL, len(system_prompt) + len(user_content))

    try:
        for round_num in range(BEDROCK_MAX_TOOL_ROUNDS):
            response = await asyncio.wait_for(
                loop.run_in_executor(
                    None,
                    lambda msgs=messages: client.converse(
                        modelId=BEDROCK_MODEL,
                        system=[{"text": system_prompt}],
                        messages=msgs,
                        toolConfig={"tools": TOOL_SPECS},
                        inferenceConfig={"maxTokens": 4096, "temperature": 0.3},
                    ),
                ),
                timeout=120,
            )

            usage = response.get("usage", {})
            total_input += usage.get("inputTokens", 0)
            total_output += usage.get("outputTokens", 0)

            stop_reason = response.get("stopReason", "")
            assistant_message = response.get("output", {}).get("message", {})
            messages.append(assistant_message)

            if stop_reason == "end_turn" or stop_reason == "max_tokens":
                break

            if stop_reason == "tool_use":
                tool_results: list[dict] = []
                for block in assistant_message.get("content", []):
                    if "toolUse" not in block:
                        continue
                    tool_use = block["toolUse"]
                    tool_name = tool_use["name"]
                    tool_id = tool_use["toolUseId"]
                    tool_input = tool_use.get("input", {})

                    log.info("Tool call round %d: %s (id=%s)", round_num + 1, tool_name, tool_id)
                    result_str = await execute_tool(tool_name, tool_input)

                    tool_results.append({
                        "toolResult": {
                            "toolUseId": tool_id,
                            "content": [{"text": result_str}],
                        }
                    })

                messages.append({"role": "user", "content": tool_results})
                continue

            break

        log.info(
            "Bedrock done: rounds=%d, total_input_tokens=%d, total_output_tokens=%d",
            round_num + 1, total_input, total_output,
        )

        text_parts: list[str] = []
        for block in assistant_message.get("content", []):
            if "text" in block:
                text_parts.append(block["text"])
        answer = "\n".join(text_parts).strip()
        return answer if answer else ""

    except asyncio.TimeoutError:
        log.warning("Bedrock request timed out")
        return ""
    except Exception as e:
        log.exception("Bedrock call failed")
        return f"Bedrock call failed: {e}"


def _write_response(query_id: str, answer: str, session_id: str = "") -> None:
    resp = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "query_id": query_id,
        "session_id": session_id,
        "agent": "ticker",
        "answer": answer,
    }
    with open(RESPONSES_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(resp, ensure_ascii=False) + "\n")
        f.flush()


def _is_useful_answer(answer: str) -> bool:
    if not answer or not answer.strip():
        return False
    lower = answer.strip().lower()
    return not any(s in lower for s in ("agent error", "agent call failed", "agent timed out", "bedrock call failed", "api key not configured", "api error"))


async def _process_query(query: dict) -> None:
    query_id = query.get("query_id", "")
    session_id = query.get("session_id", "")
    question = query.get("question", "")
    event_context = query.get("event_context")

    log.info("Processing query %s (session=%s): %s", query_id, session_id[:8], question[:80])

    answer = await _ask_bedrock(question, session_id, event_context=event_context)
    if _is_useful_answer(answer) and session_id:
        _sessions.add(session_id, question, answer)
    if _is_useful_answer(answer):
        _write_response(query_id, answer, session_id)
    else:
        _write_response(query_id, "I couldn't get an answer right now. Try a more specific question.", session_id)
    log.info("Response written for query %s", query_id)


async def run() -> None:
    global _channel_map
    log.info("Query router starting (Bedrock %s), watching %s", BEDROCK_MODEL, QUERIES_FILE)

    try:
        _channel_map = await _resolve_channel_map()
        log.info(
            "Channel map loaded: %s",
            ", ".join(sorted(_channel_map.keys())),
        )
    except Exception:
        log.exception("Failed to resolve channel map — Slack access will be limited")

    QUERIES_FILE.touch(exist_ok=True)
    RESPONSES_FILE.touch(exist_ok=True)

    last_pos = 0
    if QUERIES_FILE.exists():
        last_pos = QUERIES_FILE.stat().st_size

    while True:
        try:
            current_size = QUERIES_FILE.stat().st_size
            if current_size > last_pos:
                with open(QUERIES_FILE, "r", encoding="utf-8") as f:
                    f.seek(last_pos)
                    new_lines = f.readlines()
                last_pos = current_size

                for line in new_lines:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        query = json.loads(line)
                        await _process_query(query)
                    except json.JSONDecodeError:
                        log.warning("Invalid JSON in queries file: %s", line[:100])
        except Exception:
            log.exception("Query router error")

        await asyncio.sleep(0.5)
