"""Prometheus / OpenTelemetry metrics for CottonMouth.

Exposes CottonMouth's agent activity as **real Prometheus counters and a
histogram** so platform engineers can see change over time — ``rate()``,
``increase()``, cost/hour, error-rate trends, latency percentiles — and build
Alertmanager rules on top. This is also exactly what the OpenTelemetry
Collector's ``prometheus`` receiver scrapes, so it doubles as the OTel path.

Design: metrics are **incremented as spans are ingested** (``record_span``),
not recomputed from a bounded read window on each scrape. That means the
numbers behave like proper monotonic counters — they only go up as agents do
work — instead of being pinned to a fixed tail of recent spans. On startup we
``seed_from_window`` once from the existing trace window so the counters aren't
empty after a (re)deploy and reconcile with the dashboard's run count.

Counters reset to 0 when the backend restarts; that's normal — ``rate()`` and
``increase()`` handle counter resets. Do NOT wrap the ``_total`` series in
anything that assumes lifetime persistence across restarts.
"""
from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram, generate_latest
from prometheus_client.exposition import CONTENT_TYPE_LATEST

from src.common.agent_stats import is_infra_failure

_VERSION = "0.2.0"

# A dedicated registry so /metrics exposes exactly our series (no default
# process/GC collectors leaking in and confusing dashboards).
REGISTRY = CollectorRegistry()

_up = Gauge("cottonmouth_up", "1 if the CottonMouth backend is serving metrics.", registry=REGISTRY)
_up.set(1)
_info = Gauge("cottonmouth_info", "Build info (value always 1).", ["version"], registry=REGISTRY)
_info.labels(version=_VERSION).set(1)

# Every span, by type — the raw firehose count.
_spans = Counter(
    "cottonmouth_spans_ingested_total",
    "Total spans ingested, by span type and agent.",
    ["span_type", "agent"],
    registry=REGISTRY,
)

# Agent runs, split by terminal status. ``failed`` is agent-logic failure;
# ``infra_failure`` is an environment problem (expired creds, throttling) and is
# kept separate so it doesn't pollute the error rate — matching the UI.
_runs = Counter(
    "cottonmouth_agent_runs_total",
    "Total agent runs, by agent and terminal status (completed|failed|infra_failure).",
    ["agent", "status"],
    registry=REGISTRY,
)

_llm = Counter(
    "cottonmouth_llm_calls_total",
    "Total LLM/model calls, by agent, model and status.",
    ["agent", "model", "status"],
    registry=REGISTRY,
)
_tool = Counter(
    "cottonmouth_tool_calls_total",
    "Total tool (MCP) calls, by agent and status.",
    ["agent", "status"],
    registry=REGISTRY,
)
_perm = Counter(
    "cottonmouth_permission_checks_total",
    "Total authorization checks, by agent, verdict (allow|deny) and enforcement mode (enforce|monitor).",
    ["agent", "result", "mode"],
    registry=REGISTRY,
)
_tokens = Counter(
    "cottonmouth_tokens_total",
    "Total model tokens processed, by agent and direction (input|output).",
    ["agent", "direction"],
    registry=REGISTRY,
)
_cost = Counter(
    "cottonmouth_cost_usd_total",
    "Total spend in USD, by agent.",
    ["agent"],
    registry=REGISTRY,
)

# Wall-clock run duration -> p50/p90/p95/p99 via histogram_quantile().
_run_duration = Histogram(
    "cottonmouth_agent_run_duration_seconds",
    "Agent run wall-clock duration in seconds.",
    ["agent"],
    buckets=(0.25, 0.5, 1, 2, 5, 10, 20, 30, 60, 120, 300, 600),
    registry=REGISTRY,
)


def _agent(span: dict) -> str:
    return span.get("agent_name") or "unknown"


def _num(v) -> float:
    return v if isinstance(v, (int, float)) else 0.0


def record_span(span: dict) -> None:
    """Increment metrics for a single ingested span. Never raises — metrics must
    not be able to break the ingest path."""
    try:
        st = span.get("span_type") or "unknown"
        agent = _agent(span)
        _spans.labels(span_type=st, agent=agent).inc()

        if st == "agent_run":
            status = span.get("status")
            if status in ("completed", "failed"):
                if status == "failed":
                    bucket = "infra_failure" if is_infra_failure(span.get("error", "")) else "failed"
                else:
                    bucket = "completed"
                _runs.labels(agent=agent, status=bucket).inc()
                dur_ms = _num(span.get("duration_ms"))
                if dur_ms > 0:
                    _run_duration.labels(agent=agent).observe(dur_ms / 1000.0)

        elif st == "llm_call":
            _llm.labels(agent=agent, model=span.get("model") or "unknown",
                        status=span.get("status") or "unknown").inc()
            it, ot = _num(span.get("input_tokens")), _num(span.get("output_tokens"))
            if it:
                _tokens.labels(agent=agent, direction="input").inc(it)
            if ot:
                _tokens.labels(agent=agent, direction="output").inc(ot)
            cost = _num(span.get("cost_usd"))
            if cost:
                _cost.labels(agent=agent).inc(cost)

        elif st == "tool_call":
            _tool.labels(agent=agent, status=span.get("status") or "unknown").inc()
            cost = _num(span.get("cost_usd"))
            if cost:
                _cost.labels(agent=agent).inc(cost)

        elif st == "permission_check":
            result = "deny" if span.get("permission_result") == "deny" else "allow"
            mode = span.get("permission_mode") or "enforce"
            _perm.labels(agent=agent, result=result, mode=mode).inc()
    except Exception:
        pass


_seeded = False


def seed_from_window(spans: list[dict]) -> None:
    """One-time seed of the counters from the existing trace window so metrics
    aren't empty right after a (re)deploy. Idempotent per process."""
    global _seeded
    if _seeded:
        return
    _seeded = True
    for s in spans:
        record_span(s)


def render() -> tuple[bytes, str]:
    """Return (body, content_type) for the /metrics response."""
    return generate_latest(REGISTRY), CONTENT_TYPE_LATEST
