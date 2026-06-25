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
