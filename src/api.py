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

from src.common.agent_stats import compute_agent_stats, compute_all_agent_stats
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
    "permission_result", "permission_policy",
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
    response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    response.headers["Access-Control-Max-Age"] = "3600"
    return response


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
    """GET /api/agents -- list all agents with stats (single pass)."""
    agents = compute_all_agent_stats(_load_traces_file())
    return _json_response({"agents": agents, "total": len(agents)})


async def handle_agent_detail(request: web.Request) -> web.Response:
    """GET /api/agents/:name -- single agent stats."""
    name = request.match_info["name"]
    result = await _get_agent_stats(name)
    if "error" in result:
        return _json_response(result, status=404)
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


async def handle_policies(_: web.Request) -> web.Response:
    """GET /api/policies -- the policy-as-data document the agents enforce."""
    return _json_response(load_policies())


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

    allowed = denied = 0
    by_agent: dict[str, dict[str, int]] = {}
    by_action: dict[str, dict[str, int]] = {}
    recent_denials: list[dict] = []

    for s in checks:
        is_deny = s.get("permission_result") == "deny"
        if is_deny:
            denied += 1
        else:
            allowed += 1

        agent = s.get("agent_name", "unknown")
        a = by_agent.setdefault(agent, {"allowed": 0, "denied": 0})
        a["denied" if is_deny else "allowed"] += 1

        action = s.get("tool_name", "") or s.get("name", "unknown")
        act = by_action.setdefault(action, {"allowed": 0, "denied": 0})
        act["denied" if is_deny else "allowed"] += 1

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
                "ts": s.get("start_time", ""),
            })

    total = allowed + denied
    return _json_response({
        "summary": {
            "total": total,
            "allowed": allowed,
            "denied": denied,
            "deny_rate": round(denied / total, 4) if total else 0,
        },
        "by_agent": [
            {"agent_name": k, **v} for k, v in sorted(by_agent.items())
        ],
        "by_action": [
            {"action": k, **v} for k, v in sorted(by_action.items())
        ],
        "recent_denials": recent_denials,
    })


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


async def create_app() -> web.Application:
    """Build and return the aiohttp application."""
    app = web.Application(middlewares=[cors_middleware])

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
    app.router.add_get("/api/gateway", handle_gateway)
    app.router.add_get("/api/permissions", handle_permissions)

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
