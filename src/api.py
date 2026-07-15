"""CottonMouth HTTP API — aiohttp web server for the dashboard frontend."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from aiohttp import web

from src.common.agent_stats import (
    compute_agent_stats,
    compute_all_agent_stats,
    compute_gateway_agent_detail,
    compute_gateway_agent_stats,
)
from src.common.paths import data_dir, traces_file
from src.common.policies import load_policies
from src.tools import (
    _exec_get_trace,
    _exec_get_agent_runs,
    _exec_get_span_detail,
    _exec_search_traces,
    _load_traces_file,
)

log = logging.getLogger("cottonmouth.api")

DATA_DIR = data_dir()
EVENTS_FILE = DATA_DIR / "events.jsonl"
HEALTH_FILE = DATA_DIR / "health.json"
QUERIES_FILE = DATA_DIR / "queries.jsonl"
RESPONSES_FILE = DATA_DIR / "responses.jsonl"

DEFAULT_PORT = 8150

# Optional bearer token for the ingest endpoint. When set, /api/spans requires
# "Authorization: Bearer <token>". Unset = open ingest (fine for demos).
INGEST_API_KEY = os.environ.get("COTTONMOUTH_API_KEY", "")

# Where the interactive agent server lives (for POST /api/agent/run). Empty
# disables the proxy.
AGENT_RUN_URL = os.environ.get("AGENT_RUN_URL", "http://cottonmouth-real-agent:8200/run")

# Span fields the collector accepts on ingest. Anything else is dropped so a
# malicious or buggy client can't bloat the store with arbitrary keys.
_SPAN_FIELDS = {
    "trace_id", "span_id", "parent_span_id", "agent_name", "agent_version",
    "span_type", "name", "status", "start_time", "end_time", "duration_ms",
    "input_data", "output_data", "metadata", "error", "model",
    "input_tokens", "output_tokens", "cost_usd", "temperature",
    "tool_name", "tool_input", "tool_output", "decision_type",
    "options_considered", "chosen_option", "reasoning",
    "permission_result", "permission_policy", "permission_mode",
}

_ingest_lock = asyncio.Lock()


def _traces_file() -> Path:
    """Resolve the traces.jsonl path the same way tools.py / the watcher do."""
    return traces_file()


def _sanitize_span(raw: dict) -> dict | None:
    """Keep only known span fields and require the minimum to be useful."""
    if not isinstance(raw, dict):
        return None
    span = {k: v for k, v in raw.items() if k in _SPAN_FIELDS}
    if not span.get("trace_id") or not span.get("span_id"):
        return None
    return span

# ---------------------------------------------------------------------------
# CORS middleware
# ---------------------------------------------------------------------------


@web.middleware
async def cors_middleware(request: web.Request, handler) -> web.StreamResponse:
    if request.method == "OPTIONS":
        response = web.Response(status=204)
    else:
        try:
            response = await handler(request)
        except web.HTTPException as exc:
            response = exc
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, PATCH, DELETE, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    response.headers["Access-Control-Max-Age"] = "3600"
    return response


# ---------------------------------------------------------------------------
# Auth middleware (CORE-10694) — session cookie or API-key bearer token.
#
# Everything under /api/* requires a logged-in user or a valid API key EXCEPT
# the small allowlist below: health/discovery (so the dashboard can render a
# "backend unreachable" state pre-login), the login endpoint itself, and
# /api/spans (which keeps its own, separate ingest-token check via
# COTTONMOUTH_API_KEY — that's machine-to-machine span ingest from the SDK,
# not a user-facing endpoint, so it isn't part of the user/role system).
# /metrics also stays open: Prometheus scrape configs don't carry a session,
# and this mirrors the existing unauthenticated quickstart scrape job.
# ---------------------------------------------------------------------------

SESSION_COOKIE = "cm_session"
SESSION_TTL_SECONDS = 7 * 24 * 3600

_PUBLIC_PATHS = {"/api", "/api/health", "/api/auth/login", "/api/spans", "/metrics"}


def _authenticate(request: web.Request) -> dict | None:
    from src.common.auth import hash_api_key, verify_session_token
    from src.common import users as users_store

    token = request.cookies.get(SESSION_COOKIE)
    if token:
        payload = verify_session_token(token)
        if payload:
            return {
                "id": payload.get("uid"), "username": payload.get("username", ""),
                "role": payload.get("role", "viewer"), "auth": "session",
            }

    authz = request.headers.get("Authorization", "")
    if authz.startswith("Bearer "):
        raw = authz[len("Bearer "):].strip()
        if raw:
            rec = users_store.get_api_key_by_hash(hash_api_key(raw))
            if rec and not rec["disabled"]:
                users_store.touch_api_key(rec["id"])
                return {"id": None, "username": f"apikey:{rec['name']}", "role": rec["role"], "auth": "api_key"}
    return None


@web.middleware
async def auth_middleware(request: web.Request, handler) -> web.StreamResponse:
    if request.method == "OPTIONS" or not request.path.startswith("/api") or request.path in _PUBLIC_PATHS:
        return await handler(request)
    user = await asyncio.to_thread(_authenticate, request)
    if user is None:
        return _json_response({"error": "unauthorized"}, status=401)
    request["user"] = user
    return await handler(request)


def require_role(minimum: str):
    """Route decorator: 403s unless the authenticated user (set by
    ``auth_middleware``) has at least ``minimum`` role."""
    def deco(fn):
        async def wrapper(request: web.Request) -> web.Response:
            from src.common.auth import role_at_least
            user = request.get("user")
            if not user or not role_at_least(user["role"], minimum):
                return _json_response({"error": f"requires role >= {minimum}"}, status=403)
            return await fn(request)
        return wrapper
    return deco


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


# Never read more than this many bytes from the end of an append-only log when
# serving recent-window reads. Bounds memory/IO regardless of on-disk size so a
# runaway events.jsonl/traces.jsonl can't OOM the backend or block the event
# loop long enough for the liveness probe to kill the pod.
_MAX_TAIL_BYTES = 48 * 1024 * 1024


def _read_jsonl(path: Path, limit: int = 0, **filters: str) -> list[dict]:
    """Read the tail of a JSONL file, apply optional field filters, return
    most-recent-first.

    Reads at most ``_MAX_TAIL_BYTES`` from the end of the file so peak memory
    and IO are independent of the file's total size.
    """
    if not path.exists():
        return []
    records: list[dict] = []
    for line in _read_tail_lines(path, _MAX_TAIL_BYTES):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        skip = False
        for key, val in filters.items():
            if val and obj.get(key, "") != val:
                skip = True
                break
        if not skip:
            records.append(obj)
    records.reverse()
    if limit > 0:
        records = records[:limit]
    return records


def _read_tail_lines(path: Path, max_bytes: int) -> list[str]:
    """Return the lines from the last ``max_bytes`` of ``path`` (dropping the
    partial line at the seek boundary) without reading the whole file."""
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
            tail = deque(f)
    except OSError:
        return []
    return [ln.decode("utf-8", "replace") for ln in tail]


def _json_response(data, *, status: int = 200) -> web.Response:
    return web.json_response(data, status=status, dumps=lambda d: json.dumps(d, ensure_ascii=False, default=str))


# ---------------------------------------------------------------------------
# Route handlers
# ---------------------------------------------------------------------------


async def handle_health(request: web.Request) -> web.Response:
    """GET /api/health -- return health.json contents."""
    if not HEALTH_FILE.exists():
        return _json_response({"status": "unknown", "error": "health.json not found"}, status=404)
    try:
        data = json.loads(HEALTH_FILE.read_text(encoding="utf-8"))
        return _json_response(data)
    except (json.JSONDecodeError, OSError) as exc:
        log.warning("Failed to read health.json: %s", exc)
        return _json_response({"status": "error", "error": str(exc)}, status=500)


async def handle_events(request: web.Request) -> web.Response:
    """GET /api/events -- recent events with optional filters."""
    limit = int(request.query.get("limit", "50"))
    source = request.query.get("source", "")
    severity = request.query.get("severity", "")

    records = _read_jsonl(EVENTS_FILE, limit=limit, source=source, severity=severity)
    return _json_response({"events": records, "total": len(records)})


async def handle_events_stream(request: web.Request) -> web.StreamResponse:
    """GET /api/events/stream -- SSE stream tailing events.jsonl."""
    response = web.StreamResponse(
        status=200,
        headers={
            "Content-Type": "text/event-stream",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "Access-Control-Allow-Origin": "*",
        },
    )
    await response.prepare(request)

    # Start at end of file so we only push new events.
    if EVENTS_FILE.exists():
        offset = EVENTS_FILE.stat().st_size
    else:
        offset = 0

    try:
        while True:
            if not EVENTS_FILE.exists():
                await asyncio.sleep(1)
                continue

            current_size = EVENTS_FILE.stat().st_size
            if current_size < offset:
                # File was truncated/rotated; reset.
                offset = 0

            if current_size > offset:
                with open(EVENTS_FILE, "r", encoding="utf-8") as f:
                    f.seek(offset)
                    new_data = f.read()
                offset = current_size

                for line in new_data.strip().split("\n"):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        json.loads(line)  # validate
                        await response.write(f"data: {line}\n\n".encode())
                    except json.JSONDecodeError:
                        continue

            # Keep-alive comment to prevent proxy timeouts.
            await response.write(b": keepalive\n\n")
            await asyncio.sleep(1)
    except (asyncio.CancelledError, ConnectionResetError):
        pass

    return response


async def handle_traces(request: web.Request) -> web.Response:
    """GET /api/traces -- list recent agent runs."""
    args = {
        "agent_name": request.query.get("agent_name", ""),
        "status": request.query.get("status", ""),
        "limit": int(request.query.get("limit", "20")),
    }
    result = await _exec_get_agent_runs(args)
    return _json_response(result)


async def handle_trace_detail(request: web.Request) -> web.Response:
    """GET /api/traces/:trace_id -- full trace with spans."""
    trace_id = request.match_info["trace_id"]
    result = await _exec_get_trace({"trace_id": trace_id})
    if "error" in result:
        return _json_response(result, status=404)
    return _json_response(result)


async def handle_span_detail(request: web.Request) -> web.Response:
    """GET /api/traces/:trace_id/spans/:span_id -- single span detail."""
    trace_id = request.match_info["trace_id"]
    span_id = request.match_info["span_id"]
    result = await _exec_get_span_detail({"trace_id": trace_id, "span_id": span_id})
    if "error" in result:
        return _json_response(result, status=404)
    return _json_response(result)


async def _get_agent_stats(agent_name: str, all_spans: list[dict] | None = None) -> dict:
    """Agent stats from the retained traces -- single source of truth.

    Replaces the old unbounded SQLite counter, which never decayed and counted
    transient infra failures, so the headline numbers couldn't be reconciled
    with the (rolling) Traces list the user can actually click into.
    """
    spans = all_spans if all_spans is not None else _load_traces_file()
    return compute_agent_stats(agent_name, spans)


async def handle_agents(request: web.Request) -> web.Response:
    """GET /api/agents -- list all agents with stats (single pass).

    ``agents`` are run-instrumented agents (multi-span agent_run traces).
    ``gateway_agents`` are agents seen only via the LiteLLM gateway (e.g. Cursor
    agents keyed by a virtual-key alias) — standalone llm_call spans, no runs.
    """
    spans = await asyncio.to_thread(_load_traces_file)
    agents = compute_all_agent_stats(spans)
    gateway_agents = compute_gateway_agent_stats(spans)
    return _json_response({
        "agents": agents,
        "total": len(agents),
        "gateway_agents": gateway_agents,
        "gateway_total": len(gateway_agents),
    })


async def handle_agent_detail(request: web.Request) -> web.Response:
    """GET /api/agents/:name -- single agent stats.

    Run-instrumented agents return run-based stats; gateway-only agents return a
    gateway rollup with their recent individual calls (model/cost/verdict).
    """
    name = request.match_info["name"]
    spans = await asyncio.to_thread(_load_traces_file)
    result = compute_agent_stats(name, spans)
    gw = compute_gateway_agent_detail(name, spans)
    if "error" in result:
        if gw is not None:
            return _json_response(gw)
        return _json_response(result, status=404)
    # Same identity can both run instrumented traces AND make raw ad-hoc gateway
    # calls (e.g. devils_council.py runs + opencode/Cursor pointed at the same
    # virtual key). Attach the ad-hoc activity instead of silently dropping it.
    if gw is not None:
        result["gateway"] = gw
    return _json_response(result)


async def handle_search(request: web.Request) -> web.Response:
    """GET /api/search -- search traces."""
    query = request.query.get("q", "")
    if not query:
        return _json_response({"error": "query parameter 'q' is required"}, status=400)
    args = {
        "query": query,
        "agent_name": request.query.get("agent_name", ""),
        "status": request.query.get("status", ""),
        "limit": int(request.query.get("limit", "20")),
    }
    result = await _exec_search_traces(args)
    return _json_response(result)


async def handle_ingest_spans(request: web.Request) -> web.Response:
    """POST /api/spans -- ingest one span or a batch of spans from the SDK.

    Accepts either a single span object or a JSON array of spans. Spans are
    appended to traces.jsonl, which the agent-trace watcher tails to emit
    events and which the read APIs serve.
    """
    if INGEST_API_KEY:
        auth = request.headers.get("Authorization", "")
        if auth != f"Bearer {INGEST_API_KEY}":
            return _json_response({"error": "unauthorized"}, status=401)

    try:
        body = await request.json()
    except (json.JSONDecodeError, Exception):
        return _json_response({"error": "invalid JSON body"}, status=400)

    raw_spans = body if isinstance(body, list) else [body]
    spans = [s for s in (_sanitize_span(r) for r in raw_spans) if s is not None]

    if not spans:
        return _json_response({"error": "no valid spans in payload"}, status=400)

    traces_file = _traces_file()
    traces_file.parent.mkdir(parents=True, exist_ok=True)
    lines = "".join(json.dumps(s, ensure_ascii=False, default=str) + "\n" for s in spans)

    async with _ingest_lock:
        await asyncio.to_thread(_append_and_rotate, traces_file, lines)

    # Increment the Prometheus counters for each span as it lands. This is the
    # single ingest chokepoint, so metrics reflect real agent activity over time
    # (rate()/increase()-friendly) rather than a recomputed window snapshot.
    from src.common.metrics import record_span
    for s in spans:
        record_span(s)

    log.info("Ingested %d span(s) -> %s", len(spans), traces_file.name)
    return _json_response({"accepted": len(spans)}, status=202)


# traces.jsonl is append-only and otherwise grows without bound. Once it crosses
# the size threshold we trim it to the most recent lines so it can never grow
# back into OOM/liveness territory. The window kept comfortably exceeds the
# dashboard's read window (_TRACES_WINDOW=12000).
_TRACES_MAX_BYTES = 40 * 1024 * 1024
_TRACES_KEEP_LINES = 30000


def _append_text(path: Path, text: str) -> None:
    with open(path, "a", encoding="utf-8") as f:
        f.write(text)
        f.flush()


def _append_and_rotate(path: Path, text: str) -> None:
    """Append ``text`` then, if the file has grown past the size cap, atomically
    rewrite it with only the most recent ``_TRACES_KEEP_LINES`` lines."""
    _append_text(path, text)
    try:
        if path.stat().st_size <= _TRACES_MAX_BYTES:
            return
    except OSError:
        return
    try:
        kept = _read_tail_lines(path, _MAX_TAIL_BYTES)[-_TRACES_KEEP_LINES:]
        tmp = path.with_suffix(path.suffix + ".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            for ln in kept:
                if not ln.endswith("\n"):
                    ln += "\n"
                f.write(ln)
            f.flush()
        os.replace(tmp, path)
        log.info("Rotated %s -> kept last %d lines", path.name, len(kept))
    except OSError:
        log.exception("Failed to rotate %s", path.name)


async def handle_investigate_submit(request: web.Request) -> web.Response:
    """POST /api/investigate -- submit an investigation query."""
    try:
        body = await request.json()
    except (json.JSONDecodeError, Exception):
        return _json_response({"error": "invalid JSON body"}, status=400)

    question = body.get("question", "").strip()
    if not question:
        return _json_response({"error": "'question' field is required"}, status=400)

    query_id = uuid.uuid4().hex[:12]
    session_id = body.get("session_id", "") or uuid.uuid4().hex[:16]
    event_context = body.get("event_context")

    query_record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "query_id": query_id,
        "session_id": session_id,
        "question": question,
    }
    if event_context:
        query_record["event_context"] = event_context

    QUERIES_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(QUERIES_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(query_record, ensure_ascii=False) + "\n")
        f.flush()

    log.info("Investigation query submitted: %s — %s", query_id, question[:80])
    return _json_response({
        "query_id": query_id,
        "session_id": session_id,
        "status": "pending",
    }, status=202)


async def handle_investigate_poll(request: web.Request) -> web.Response:
    """GET /api/investigate/:query_id -- poll for investigation response."""
    query_id = request.match_info["query_id"]

    if not RESPONSES_FILE.exists():
        return _json_response({"query_id": query_id, "status": "pending"})

    # Scan responses.jsonl from newest to oldest for a matching query_id.
    try:
        lines = RESPONSES_FILE.read_text(encoding="utf-8").strip().split("\n")
    except OSError:
        return _json_response({"query_id": query_id, "status": "pending"})

    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if record.get("query_id") == query_id:
            return _json_response({
                "query_id": query_id,
                "status": "complete",
                "answer": record.get("answer", ""),
                "session_id": record.get("session_id", ""),
                "ts": record.get("ts", ""),
            })

    return _json_response({"query_id": query_id, "status": "pending"})


# ---------------------------------------------------------------------------
# App factory and runner
# ---------------------------------------------------------------------------


API_VERSION = "1"


async def handle_api_root(_: web.Request) -> web.Response:
    """GET /api -- machine-readable capability/endpoint discovery.

    A small, stable contract anchor so clients (including the CottonMouth MCP
    server) can discover the available endpoints and the API version.
    """
    return _json_response({
        "name": "cottonmouth",
        "api_version": API_VERSION,
        "endpoints": {
            "health": "GET /api/health",
            "events": "GET /api/events",
            "events_stream": "GET /api/events/stream",
            "traces": "GET /api/traces",
            "trace_detail": "GET /api/traces/{trace_id}",
            "span_detail": "GET /api/traces/{trace_id}/spans/{span_id}",
            "agents": "GET /api/agents",
            "agent_detail": "GET /api/agents/{name}",
            "search": "GET /api/search?q=",
            "policies": "GET /api/policies",
            "gateway": "GET /api/gateway",
            "permissions": "GET /api/permissions",
            "metrics": "GET /metrics",
            "ingest_spans": "POST /api/spans",
            "investigate_submit": "POST /api/investigate",
            "investigate_poll": "GET /api/investigate/{query_id}",
            "agent_run": "POST /api/agent/run",
            "login": "POST /api/auth/login",
            "logout": "POST /api/auth/logout",
            "me": "GET /api/auth/me",
            "set_agent_mode": "PATCH /api/policies/{name}/mode (operator+)",
            "admin_users": "GET/POST /api/admin/users, PATCH/DELETE /api/admin/users/{id} (admin)",
            "admin_api_keys": "GET/POST /api/admin/api-keys, DELETE /api/admin/api-keys/{id} (admin)",
        },
        "auth": "session cookie (login via /api/auth/login) or 'Authorization: Bearer <api_key>'",
    })


async def handle_policies(_: web.Request) -> web.Response:
    """GET /api/policies -- the policy-as-data document the agents enforce,
    with any runtime mode overrides (set via the admin UI) merged in so the
    Governance view always shows the mode actually in effect."""
    from src.common.users import list_policy_overrides

    doc = load_policies()
    overrides = await asyncio.to_thread(list_policy_overrides)
    for name, ap in (doc.get("agents") or {}).items():
        ov = overrides.get(name)
        if ov:
            ap["mode"] = ov["mode"]
            ap["mode_overridden"] = True
            ap["mode_updated_by"] = ov.get("updated_by")
            ap["mode_updated_at"] = ov.get("updated_at")
    return _json_response(doc)


def _bare_model(m: str) -> str:
    """Normalize a model id for comparison: drop a provider prefix and trim a
    Bedrock version suffix so 'claude-3-haiku', 'bedrock/anthropic.claude-3-haiku-...'
    and 'anthropic.claude-3-haiku-...-v1:0' compare equal on family."""
    m = (m or "").split("/", 1)[-1]
    if "." in m:
        m = m.split(".", 1)[-1]
    for cut in ("-2024", "-2025", "-v1", "-v2", ":0"):
        i = m.find(cut)
        if i != -1:
            m = m[:i]
    return m


def _models_match(a: str, b: str) -> bool:
    ba, bb = _bare_model(a), _bare_model(b)
    return ba == bb or ba.startswith(bb) or bb.startswith(ba)


def _observed_gateway_models() -> dict[str, dict]:
    """Per-agent {models:set, calls:int, cost:float} observed from gateway llm_call
    spans (source == 'litellm') in the recent trace window."""
    out: dict[str, dict] = {}
    for s in _load_traces_file():
        if s.get("span_type") != "llm_call":
            continue
        md = s.get("metadata") or {}
        if md.get("source") != "litellm":
            continue
        agent = s.get("agent_name", "") or "unknown"
        rec = out.setdefault(agent, {"models": set(), "calls": 0, "cost": 0.0})
        if s.get("model"):
            rec["models"].add(s["model"])
        rec["calls"] += 1
        rec["cost"] += float(s.get("cost_usd") or 0.0)
    return out


async def handle_gateway(_: web.Request) -> web.Response:
    """GET /api/gateway -- reconcile each agent's DECLARED gateway model access
    (policy-as-data) against what the gateway actually EXPOSES (/v1/models) and
    what the agent has actually USED (observed llm_call spans). The gateway
    enforces; CottonMouth surfaces the envelope and flags drift.
    """
    from src.common.gateway_client import snapshot

    # snapshot() makes synchronous HTTP calls to the gateway and
    # _observed_gateway_models() scans the trace tail — run both off the event
    # loop so the gateway being slow can't block the loop / liveness probe.
    snap, observed = await asyncio.gather(
        asyncio.to_thread(snapshot),
        asyncio.to_thread(_observed_gateway_models),
    )
    policies = load_policies()
    available = snap.get("models", [])

    agents = []
    for name, ap in (policies.get("agents", {}) or {}).items():
        gw = ap.get("gateway")
        obs = observed.get(name)
        if not gw and not obs:
            continue  # not a gateway-using agent
        declared = list((gw or {}).get("declared_models", []))
        obs_models = sorted((obs or {}).get("models", set()))
        # Drift: declared a model the gateway doesn't expose; or used a model that
        # wasn't declared.
        not_exposed = [m for m in declared if available and not any(_models_match(m, a) for a in available)]
        undeclared_used = [m for m in obs_models if declared and not any(_models_match(m, d) for d in declared)]
        agents.append({
            "agent_name": name,
            "display_name": ap.get("display_name", name),
            "key_alias": (gw or {}).get("key_alias", ""),
            "declared_models": declared,
            "observed_models": obs_models,
            "observed_calls": (obs or {}).get("calls", 0),
            "observed_cost_usd": round((obs or {}).get("cost", 0.0), 6),
            "drift": {"declared_not_exposed": not_exposed, "used_not_declared": undeclared_used},
        })

    return _json_response({
        "enabled": snap.get("enabled", False),
        "reachable": snap.get("reachable", False),
        "endpoint": snap.get("endpoint", ""),
        "db_backed": snap.get("db_backed", False),
        "available_models": available,
        "agents": agents,
    })


async def handle_permissions(request: web.Request) -> web.Response:
    """GET /api/permissions -- live audit aggregated from permission_check spans.

    Answers the "what was it allowed to do" pillar at the fleet level: how many
    authorization checks ran, how many were denied, broken down by agent and by
    action, plus the most recent denials (each links back to its trace).
    """
    try:
        limit = int(request.query.get("limit", "25"))
    except ValueError:
        limit = 25

    # Use the bounded, cached trace loader (tail-only) off the event loop so a
    # large traces.jsonl can't block the loop / trip the liveness probe.
    spans = await asyncio.to_thread(_load_traces_file)  # oldest-first
    checks = [s for s in reversed(spans) if s.get("span_type") == "permission_check"]

    allowed = denied = enforced_denied = monitored_denied = 0
    # Per agent we track allow/deny plus how denies split across enforcement mode:
    # enforced denies were blocked; monitored denies "would have been blocked".
    def _agent_rec() -> dict[str, int]:
        return {"allowed": 0, "denied": 0, "enforced_denied": 0, "monitored_denied": 0}

    by_agent: dict[str, dict[str, int]] = {}
    by_action: dict[str, dict[str, int]] = {}
    recent_denials: list[dict] = []

    for s in checks:
        is_deny = s.get("permission_result") == "deny"
        mode = s.get("permission_mode") or "enforce"
        agent = s.get("agent_name", "unknown")
        a = by_agent.setdefault(agent, _agent_rec())
        action = s.get("tool_name", "") or s.get("name", "unknown")
        act = by_action.setdefault(action, {"allowed": 0, "denied": 0})

        if is_deny:
            denied += 1
            a["denied"] += 1
            act["denied"] += 1
            if mode == "monitor":
                monitored_denied += 1
                a["monitored_denied"] += 1
            else:
                enforced_denied += 1
                a["enforced_denied"] += 1
        else:
            allowed += 1
            a["allowed"] += 1
            act["allowed"] += 1

        if is_deny and len(recent_denials) < limit:
            resource = ""
            ti = s.get("tool_input") or {}
            if isinstance(ti, dict):
                resource = ti.get("resource", "")
            recent_denials.append({
                "trace_id": s.get("trace_id", ""),
                "span_id": s.get("span_id", ""),
                "agent_name": agent,
                "action": action,
                "resource": resource,
                "policy": s.get("permission_policy", ""),
                "mode": mode,
                "would_block": mode == "monitor",
                "ts": s.get("start_time", ""),
            })

    total = allowed + denied
    # The configured enforcement mode per agent (from policy-as-data), so the UI
    # can show which agents are in monitor (shadow) vs enforce.
    from src.common.policies import agent_mode
    return _json_response({
        "summary": {
            "total": total,
            "allowed": allowed,
            "denied": denied,
            "deny_rate": round(denied / total, 4) if total else 0,
            "compliance_rate": round(allowed / total, 4) if total else 1,
            "enforced_denied": enforced_denied,
            "monitored_denied": monitored_denied,
        },
        "by_agent": [
            {
                "agent_name": k,
                **v,
                "compliance_rate": round(v["allowed"] / (v["allowed"] + v["denied"]), 4)
                if (v["allowed"] + v["denied"]) else 1,
                "mode": agent_mode(k),
            }
            for k, v in sorted(by_agent.items())
        ],
        "by_action": [
            {"action": k, **v} for k, v in sorted(by_action.items())
        ],
        "recent_denials": recent_denials,
    })


async def handle_metrics(_: web.Request) -> web.Response:
    """GET /metrics -- Prometheus text-format metrics for platform dashboards.

    Derived from the same rolling span window as the dashboard so the numbers
    reconcile with the Traces/Agents/Governance views. Scrape target for
    Prometheus / the OpenTelemetry Collector prometheus receiver.
    """
    from src.common.metrics import render

    body, content_type = await asyncio.to_thread(render)
    return web.Response(body=body, headers={"Content-Type": content_type})


async def handle_agent_run(request: web.Request) -> web.Response:
    """POST /api/agent/run -- forward a task to the interactive agent server.

    Lets the dashboard drive the live agent on demand. The agent executes the
    task (emitting a full trace) and returns a summary including the trace_id.
    """
    if not AGENT_RUN_URL:
        return _json_response({"error": "interactive agent not configured"}, status=503)
    try:
        body = await request.json()
    except (json.JSONDecodeError, Exception):
        body = {}
    task = (body.get("task") or "").strip()
    if not task:
        return _json_response({"error": "missing 'task'"}, status=400)

    import aiohttp

    try:
        timeout = aiohttp.ClientTimeout(total=120)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(AGENT_RUN_URL, json={"task": task}) as resp:
                data = await resp.json()
                return _json_response(data, status=resp.status)
    except Exception as exc:
        log.warning("agent run proxy failed: %s", exc)
        return _json_response({"error": f"agent unreachable: {exc}"}, status=502)



# ---------------------------------------------------------------------------
# Auth & admin endpoints (CORE-10694)
# ---------------------------------------------------------------------------


def _cookie_secure() -> bool:
    # Default insecure: the primary access path today is `kubectl port-forward`
    # over plain http, and a Secure cookie is silently dropped by the browser
    # on http origins, which would look like login "not working". Opt in once
    # CottonMouth is actually served over https.
    return os.environ.get("COTTONMOUTH_COOKIE_SECURE", "0") == "1"


async def handle_login(request: web.Request) -> web.Response:
    """POST /api/auth/login {username, password} -> sets a signed session cookie."""
    from src.common import users as users_store
    from src.common.auth import create_session_token, verify_password

    try:
        body = await request.json()
    except (json.JSONDecodeError, Exception):
        return _json_response({"error": "invalid JSON body"}, status=400)
    username = (body.get("username") or "").strip()
    password = body.get("password") or ""

    row = await asyncio.to_thread(users_store.get_user_by_username, username)
    if not row or row["disabled"] or not verify_password(password, row["password_hash"]):
        return _json_response({"error": "invalid username or password"}, status=401)

    token = create_session_token(row["id"], row["username"], row["role"], SESSION_TTL_SECONDS)
    resp = _json_response({"username": row["username"], "role": row["role"]})
    resp.set_cookie(
        SESSION_COOKIE, token, max_age=SESSION_TTL_SECONDS, httponly=True,
        samesite="Lax", secure=_cookie_secure(), path="/",
    )
    return resp


async def handle_logout(_: web.Request) -> web.Response:
    resp = _json_response({"ok": True})
    resp.del_cookie(SESSION_COOKIE, path="/")
    return resp


async def handle_me(request: web.Request) -> web.Response:
    user = request.get("user")
    if not user:
        return _json_response({"error": "unauthorized"}, status=401)
    return _json_response({"username": user["username"], "role": user["role"], "auth": user["auth"]})


async def handle_list_users(_: web.Request) -> web.Response:
    from src.common import users as users_store
    rows = await asyncio.to_thread(users_store.list_users)
    return _json_response({"users": rows})


async def handle_create_user(request: web.Request) -> web.Response:
    from src.common import users as users_store
    from src.common.auth import ROLES

    try:
        body = await request.json()
    except (json.JSONDecodeError, Exception):
        return _json_response({"error": "invalid JSON body"}, status=400)
    username = (body.get("username") or "").strip()
    password = body.get("password") or ""
    role = (body.get("role") or "viewer").strip()
    if not username or len(password) < 8:
        return _json_response({"error": "username required; password must be >= 8 chars"}, status=400)
    if role not in ROLES:
        return _json_response({"error": f"role must be one of {ROLES}"}, status=400)
    try:
        uid = await asyncio.to_thread(users_store.create_user, username, password, role)
    except users_store.UserExistsError:
        return _json_response({"error": "username already exists"}, status=409)
    return _json_response({"id": uid, "username": username, "role": role}, status=201)


async def handle_update_user(request: web.Request) -> web.Response:
    from src.common import users as users_store
    from src.common.auth import ROLES

    try:
        uid = int(request.match_info["id"])
    except ValueError:
        return _json_response({"error": "invalid id"}, status=400)
    try:
        body = await request.json()
    except (json.JSONDecodeError, Exception):
        body = {}

    role = body.get("role")
    if role is not None and role not in ROLES:
        return _json_response({"error": f"role must be one of {ROLES}"}, status=400)
    disabled = body.get("disabled")
    password = body.get("password")
    if password is not None and len(password) < 8:
        return _json_response({"error": "password must be >= 8 chars"}, status=400)

    # Guard against locking everyone out: can't demote/disable the last admin.
    if (role is not None and role != "admin") or disabled is True:
        rows = await asyncio.to_thread(users_store.list_users)
        target = next((r for r in rows if r["id"] == uid), None)
        if target and target["role"] == "admin" and not target["disabled"]:
            remaining = await asyncio.to_thread(users_store.count_admins, uid)
            if remaining == 0:
                return _json_response({"error": "cannot demote/disable the last remaining admin"}, status=409)

    ok = await asyncio.to_thread(users_store.update_user, uid, role, disabled, password)
    if not ok:
        return _json_response({"error": "user not found"}, status=404)
    return _json_response({"ok": True})


async def handle_delete_user(request: web.Request) -> web.Response:
    from src.common import users as users_store

    try:
        uid = int(request.match_info["id"])
    except ValueError:
        return _json_response({"error": "invalid id"}, status=400)
    if uid == request["user"].get("id"):
        return _json_response({"error": "cannot delete your own account"}, status=409)
    ok = await asyncio.to_thread(users_store.delete_user, uid)
    if not ok:
        return _json_response({"error": "user not found"}, status=404)
    return _json_response({"ok": True})


async def handle_list_api_keys(_: web.Request) -> web.Response:
    from src.common import users as users_store
    rows = await asyncio.to_thread(users_store.list_api_keys)
    return _json_response({"api_keys": rows})


async def handle_create_api_key(request: web.Request) -> web.Response:
    from src.common import users as users_store
    from src.common.auth import ROLES, generate_api_key

    try:
        body = await request.json()
    except (json.JSONDecodeError, Exception):
        body = {}
    name = (body.get("name") or "").strip()
    role = (body.get("role") or "viewer").strip()
    if not name:
        return _json_response({"error": "name required"}, status=400)
    if role not in ROLES:
        return _json_response({"error": f"role must be one of {ROLES}"}, status=400)

    raw_key, key_hash = generate_api_key()
    kid = await asyncio.to_thread(
        users_store.create_api_key, name, key_hash, role, request["user"]["username"],
    )
    # The raw key is only ever returned here -- it isn't retrievable again.
    return _json_response({"id": kid, "name": name, "role": role, "key": raw_key}, status=201)


async def handle_delete_api_key(request: web.Request) -> web.Response:
    from src.common import users as users_store
    try:
        kid = int(request.match_info["id"])
    except ValueError:
        return _json_response({"error": "invalid id"}, status=400)
    ok = await asyncio.to_thread(users_store.revoke_api_key, kid)
    if not ok:
        return _json_response({"error": "not found"}, status=404)
    return _json_response({"ok": True})


async def handle_set_agent_mode(request: web.Request) -> web.Response:
    """PATCH /api/policies/{name}/mode {mode: "enforce"|"monitor"} -- flip an
    agent's enforcement mode from the admin UI without touching the
    agent_policies.json ConfigMap. Stored as a DB override that always wins
    over the file (see src.common.policies.agent_mode)."""
    from src.common import users as users_store

    name = request.match_info["name"]
    try:
        body = await request.json()
    except (json.JSONDecodeError, Exception):
        return _json_response({"error": "invalid JSON body"}, status=400)
    mode = (body.get("mode") or "").strip().lower()
    if mode not in ("enforce", "monitor"):
        return _json_response({"error": "mode must be 'enforce' or 'monitor'"}, status=400)
    await asyncio.to_thread(users_store.set_policy_override, name, mode, request["user"]["username"])
    return _json_response({"agent_name": name, "mode": mode})


async def _bootstrap_admin(_: web.Application) -> None:
    """Ensure at least one admin user exists so a fresh deploy always has a
    way in. If COTTONMOUTH_ADMIN_PASSWORD isn't set, generate one and log it
    once -- log in and change it, or set the env var and redeploy to pin it."""
    from src.common import users as users_store

    try:
        if await asyncio.to_thread(users_store.count_users) > 0:
            return
        username = os.environ.get("COTTONMOUTH_ADMIN_USERNAME", "admin").strip() or "admin"
        password = os.environ.get("COTTONMOUTH_ADMIN_PASSWORD", "").strip()
        generated = False
        if not password:
            import secrets as _secrets
            password = _secrets.token_urlsafe(12)
            generated = True
        await asyncio.to_thread(users_store.create_user, username, password, "admin")
        if generated:
            log.warning(
                "No COTTONMOUTH_ADMIN_PASSWORD set -- generated a one-time admin login. "
                "username=%s password=%s -- log in and change it, or set "
                "COTTONMOUTH_ADMIN_PASSWORD (from a Secret) and redeploy to pin it.",
                username, password,
            )
        else:
            log.info("Bootstrapped admin user %r from COTTONMOUTH_ADMIN_PASSWORD", username)
    except Exception:
        log.exception("Admin bootstrap failed")


async def create_app() -> web.Application:
    """Build and return the aiohttp application."""
    app = web.Application(middlewares=[cors_middleware, auth_middleware])

    app.router.add_get("/api", handle_api_root)
    app.router.add_get("/api/health", handle_health)
    app.router.add_get("/api/events", handle_events)
    app.router.add_get("/api/events/stream", handle_events_stream)
    app.router.add_get("/api/traces", handle_traces)
    app.router.add_get("/api/traces/{trace_id}", handle_trace_detail)
    app.router.add_get("/api/traces/{trace_id}/spans/{span_id}", handle_span_detail)
    app.router.add_get("/api/agents", handle_agents)
    app.router.add_get("/api/agents/{name}", handle_agent_detail)
    app.router.add_get("/api/search", handle_search)
    app.router.add_post("/api/spans", handle_ingest_spans)
    app.router.add_post("/api/investigate", handle_investigate_submit)
    app.router.add_get("/api/investigate/{query_id}", handle_investigate_poll)
    app.router.add_post("/api/agent/run", handle_agent_run)
    app.router.add_get("/api/policies", handle_policies)
    app.router.add_patch("/api/policies/{name}/mode", require_role("operator")(handle_set_agent_mode))
    app.router.add_get("/api/gateway", handle_gateway)
    app.router.add_get("/api/permissions", handle_permissions)
    app.router.add_get("/metrics", handle_metrics)

    # Auth (CORE-10694): login/logout/me are open to any request (the login
    # check itself is the auth); admin endpoints require the admin role.
    app.router.add_post("/api/auth/login", handle_login)
    app.router.add_post("/api/auth/logout", handle_logout)
    app.router.add_get("/api/auth/me", handle_me)
    app.router.add_get("/api/admin/users", require_role("admin")(handle_list_users))
    app.router.add_post("/api/admin/users", require_role("admin")(handle_create_user))
    app.router.add_patch("/api/admin/users/{id}", require_role("admin")(handle_update_user))
    app.router.add_delete("/api/admin/users/{id}", require_role("admin")(handle_delete_user))
    app.router.add_get("/api/admin/api-keys", require_role("admin")(handle_list_api_keys))
    app.router.add_post("/api/admin/api-keys", require_role("admin")(handle_create_api_key))
    app.router.add_delete("/api/admin/api-keys/{id}", require_role("admin")(handle_delete_api_key))

    # Seed the Prometheus counters once from the existing trace window so /metrics
    # isn't empty right after a (re)deploy and reconciles with the dashboard's
    # run count. New spans then increment the same counters as they're ingested.
    async def _seed_metrics(_: web.Application) -> None:
        try:
            from src.common.metrics import seed_from_window
            spans = await asyncio.to_thread(_load_traces_file)
            await asyncio.to_thread(seed_from_window, spans)
            log.info("Seeded metrics counters from %d spans", len(spans))
        except Exception as exc:
            log.warning("metrics seed skipped: %s", exc)

    app.on_startup.append(_seed_metrics)
    app.on_startup.append(_bootstrap_admin)

    log.info("CottonMouth API routes registered")
    return app


async def start_api(host: str = "0.0.0.0", port: int = DEFAULT_PORT) -> None:
    """Start the aiohttp web server."""
    app = await create_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    log.info("CottonMouth API listening on http://%s:%d", host, port)

    # Keep running until cancelled.
    try:
        while True:
            await asyncio.sleep(3600)
    except asyncio.CancelledError:
        pass
    finally:
        await runner.cleanup()


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    asyncio.run(start_api(port=int(os.environ.get("API_PORT", str(DEFAULT_PORT)))))
