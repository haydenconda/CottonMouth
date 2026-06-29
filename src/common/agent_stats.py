"""Shared agent-stats logic.

Stats are derived from the retained trace data (the single source of truth that
reconciles with what the Traces view shows), and infrastructure failures
(expired credentials, throttling, transient auth/network) are classified
separately so a credential outage can't make a healthy agent look broken.
"""
from __future__ import annotations

# Substrings that mark an *infrastructure* failure rather than an agent-logic
# error. Matched case-insensitively against a span/run's error text.
INFRA_ERROR_MARKERS = (
    "expired", "credential", "accessdenied", "access denied",
    "unrecognizedclient", "unrecognized client", "invalidsignature",
    "expiredtoken", "security token", "no credentials",
    "unable to locate credentials", "throttl", "rate exceeded", "toomanyrequests",
    "serviceunavailable", "service unavailable", "could not connect",
    "connection reset", "connection aborted", "503", "429",
)


def is_infra_failure(error: str) -> bool:
    e = (error or "").lower()
    return any(marker in e for marker in INFRA_ERROR_MARKERS)


def _finalize(agent_name: str, acc: dict) -> dict:
    total_runs = acc["total_runs"]
    infra = acc["infra"]
    agent_errors = acc["failed"] - infra
    return {
        "agent_name": agent_name,
        "total_runs": total_runs,
        "avg_duration_ms": round(acc["total_ms"] / total_runs, 1),
        "total_cost_usd": round(acc["total_cost"], 6),
        "avg_cost_usd": round(acc["total_cost"] / total_runs, 6),
        "error_count": agent_errors,
        "error_rate": round(agent_errors / total_runs, 4),
        "infra_failure_count": infra,
    }


def compute_agent_stats(agent_name: str, spans: list[dict]) -> dict:
    """Compute one agent's stats from trace spans.

    Returns ``{"error": ...}`` if the agent has no runs. Infra failures are
    excluded from ``error_count`` / ``error_rate`` and reported via
    ``infra_failure_count``.
    """
    acc = {"total_runs": 0, "total_ms": 0, "total_cost": 0.0, "failed": 0, "infra": 0}
    for s in spans:
        if s.get("span_type") != "agent_run" or s.get("agent_name") != agent_name:
            continue
        _accumulate(acc, s)
    if acc["total_runs"] == 0:
        return {"agent_name": agent_name, "error": f"No runs found for agent '{agent_name}'"}
    return _finalize(agent_name, acc)


def compute_all_agent_stats(spans: list[dict]) -> list[dict]:
    """Compute stats for *every* agent in a single pass over the spans.

    O(spans) instead of O(agents x spans) -- the dashboard's /api/agents
    endpoint calls this on every poll.
    """
    accs: dict[str, dict] = {}
    for s in spans:
        if s.get("span_type") != "agent_run":
            continue
        name = s.get("agent_name")
        if not name:
            continue
        acc = accs.get(name)
        if acc is None:
            acc = accs[name] = {
                "total_runs": 0, "total_ms": 0, "total_cost": 0.0,
                "failed": 0, "infra": 0,
            }
        _accumulate(acc, s)
    return [_finalize(name, acc) for name, acc in sorted(accs.items())]


def _accumulate(acc: dict, run: dict) -> None:
    acc["total_runs"] += 1
    acc["total_ms"] += run.get("duration_ms", 0)
    acc["total_cost"] += run.get("cost_usd", 0)
    if run.get("status") == "failed":
        acc["failed"] += 1
        if is_infra_failure(run.get("error", "")):
            acc["infra"] += 1


# ---------------------------------------------------------------------------
# Gateway-only agents (e.g. external Cursor agents keyed by a LiteLLM virtual
# key). These route through the shared gateway and surface as standalone
# ``llm_call`` spans (source == "litellm") with NO ``agent_run`` of their own,
# so the run-based stats above miss them entirely. We roll them up separately:
# their unit of work is a single model call, not a multi-span run.
# ---------------------------------------------------------------------------

def _run_agent_names(spans: list[dict]) -> set[str]:
    return {
        s.get("agent_name")
        for s in spans
        if s.get("span_type") == "agent_run" and s.get("agent_name")
    }


def _is_gateway_llm_call(s: dict) -> bool:
    return (
        s.get("span_type") == "llm_call"
        and (s.get("metadata") or {}).get("source") == "litellm"
    )


def compute_gateway_agent_stats(spans: list[dict]) -> list[dict]:
    """Roll up agents seen ONLY through the LiteLLM gateway (no agent_run).

    One entry per agent identity (the virtual-key alias the gateway reported),
    aggregating model usage, tokens, cost, and the gateway's allow/deny verdicts.
    """
    run_agents = _run_agent_names(spans)
    accs: dict[str, dict] = {}
    for s in spans:
        if not _is_gateway_llm_call(s):
            continue
        name = s.get("agent_name")
        if not name or name in run_agents:
            continue
        acc = accs.get(name)
        if acc is None:
            acc = accs[name] = {
                "calls": 0, "cost": 0.0, "in_tok": 0, "out_tok": 0,
                "failed": 0, "denied": 0, "ms": 0, "models": set(),
                "first": None, "last": None,
            }
        acc["calls"] += 1
        acc["cost"] += s.get("cost_usd", 0) or 0
        acc["in_tok"] += s.get("input_tokens", 0) or 0
        acc["out_tok"] += s.get("output_tokens", 0) or 0
        acc["ms"] += s.get("duration_ms", 0) or 0
        if s.get("model"):
            acc["models"].add(s["model"])
        if s.get("status") == "failed":
            acc["failed"] += 1
        st = s.get("start_time")
        if st:
            if acc["first"] is None or st < acc["first"]:
                acc["first"] = st
            if acc["last"] is None or st > acc["last"]:
                acc["last"] = st
    # Gateway verdicts: a denied call is recorded as a permission_check (deny).
    for s in spans:
        if (
            s.get("span_type") == "permission_check"
            and s.get("permission_result") == "deny"
            and s.get("agent_name") in accs
        ):
            accs[s["agent_name"]]["denied"] += 1
    return [_finalize_gateway(name, a) for name, a in sorted(accs.items())]


def _finalize_gateway(name: str, a: dict) -> dict:
    calls = a["calls"]
    return {
        "agent_name": name,
        "kind": "gateway",
        "call_count": calls,
        "total_cost_usd": round(a["cost"], 6),
        "avg_cost_usd": round(a["cost"] / calls, 6) if calls else 0.0,
        "avg_duration_ms": round(a["ms"] / calls, 1) if calls else 0.0,
        "input_tokens": a["in_tok"],
        "output_tokens": a["out_tok"],
        "models": sorted(a["models"]),
        "error_count": a["failed"],
        "denied_count": a["denied"],
        "first_seen": a["first"],
        "last_seen": a["last"],
    }


def compute_gateway_agent_detail(agent_name: str, spans: list[dict]) -> dict | None:
    """Detail for one gateway-only agent: its rollup + recent individual calls.

    Returns ``None`` if the agent has no gateway calls (so callers can fall back
    to run-based stats / 404).
    """
    summary = next(
        (g for g in compute_gateway_agent_stats(spans) if g["agent_name"] == agent_name),
        None,
    )
    if summary is None:
        return None
    # Map each gateway llm_call to its verdict (nested permission_check, if any).
    verdict_by_parent: dict[str, str] = {
        s.get("parent_span_id"): s.get("permission_result", "")
        for s in spans
        if s.get("span_type") == "permission_check" and s.get("parent_span_id")
    }
    calls = []
    for s in spans:
        if not _is_gateway_llm_call(s) or s.get("agent_name") != agent_name:
            continue
        calls.append({
            "trace_id": s.get("trace_id", ""),
            "span_id": s.get("span_id", ""),
            "model": s.get("model", ""),
            "input_tokens": s.get("input_tokens", 0) or 0,
            "output_tokens": s.get("output_tokens", 0) or 0,
            "cost_usd": round(float(s.get("cost_usd", 0) or 0), 6),
            "duration_ms": s.get("duration_ms", 0) or 0,
            "status": s.get("status", ""),
            "verdict": verdict_by_parent.get(s.get("span_id"), "allow"),
            "started_at": s.get("start_time", ""),
        })
    # newest first
    calls.sort(key=lambda c: c["started_at"], reverse=True)
    summary["calls"] = calls[:50]
    return summary
