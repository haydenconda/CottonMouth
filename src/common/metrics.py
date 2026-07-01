"""Prometheus / OpenTelemetry metrics for CottonMouth.

Exposes CottonMouth's observability data as Prometheus text-format metrics so
platform engineers can scrape it into their own Grafana dashboards and build
Alertmanager rules — instead of being limited to the built-in dashboard.

Everything is derived on each scrape from the same rolling span window the
dashboard reads (``_load_traces_file``), so the numbers reconcile exactly with
the Traces / Agents / Governance views. Because the window is a bounded tail
(not a monotonic lifetime counter), metrics are exposed as **gauges** describing
the current window; document this for anyone writing ``rate()`` queries.

The Prometheus exposition format is also what the OpenTelemetry Collector's
``prometheus`` receiver scrapes, so this doubles as the OTel path.
"""
from __future__ import annotations

from prometheus_client import CollectorRegistry, Gauge, generate_latest
from prometheus_client.exposition import CONTENT_TYPE_LATEST

from src.common.agent_stats import (
    compute_all_agent_stats,
    compute_gateway_agent_stats,
)

_VERSION = "0.1.0"


def _permission_breakdown(spans: list[dict]) -> tuple[dict, dict, dict]:
    """Aggregate permission_check spans.

    Returns:
        by_agent_result_mode: {(agent, result, mode): count}
        agent_totals: {agent: {"allow": n, "deny": n}}
        fleet: {"allow": n, "deny": n}
    """
    by_key: dict[tuple[str, str, str], int] = {}
    agent_totals: dict[str, dict[str, int]] = {}
    fleet = {"allow": 0, "deny": 0}
    for s in spans:
        if s.get("span_type") != "permission_check":
            continue
        agent = s.get("agent_name") or "unknown"
        result = "deny" if s.get("permission_result") == "deny" else "allow"
        mode = s.get("permission_mode") or "enforce"
        by_key[(agent, result, mode)] = by_key.get((agent, result, mode), 0) + 1
        at = agent_totals.setdefault(agent, {"allow": 0, "deny": 0})
        at[result] += 1
        fleet[result] += 1
    return by_key, agent_totals, fleet


def render_metrics(spans: list[dict]) -> tuple[bytes, str]:
    """Compute the full metric set from ``spans`` and return (body, content_type)."""
    reg = CollectorRegistry()

    up = Gauge("cottonmouth_up", "1 if the CottonMouth backend is serving metrics.", registry=reg)
    up.set(1)
    info = Gauge("cottonmouth_info", "Build info (value always 1).", ["version"], registry=reg)
    info.labels(version=_VERSION).set(1)
    window = Gauge(
        "cottonmouth_spans_window",
        "Number of spans in the current rolling read window.",
        registry=reg,
    )
    window.set(len(spans))

    # --- Per run-instrumented agent (multi-span agent_run traces) ---
    g_runs = Gauge("cottonmouth_agent_runs", "Agent runs observed in the window.", ["agent"], registry=reg)
    g_run_err = Gauge("cottonmouth_agent_errors", "Agent-logic run failures in the window (infra failures excluded).", ["agent"], registry=reg)
    g_err_rate = Gauge("cottonmouth_agent_error_rate", "Agent-logic error rate over the window (0-1).", ["agent"], registry=reg)
    g_infra = Gauge("cottonmouth_agent_infra_failures", "Infrastructure failures (expired creds, throttling) in the window.", ["agent"], registry=reg)
    g_dur = Gauge("cottonmouth_agent_run_duration_ms_avg", "Average agent run duration (ms) over the window.", ["agent"], registry=reg)

    # --- Cost / tokens / activity (agent + kind) ---
    g_cost = Gauge("cottonmouth_agent_cost_usd", "Total cost (USD) attributed to the agent in the window.", ["agent", "kind"], registry=reg)
    g_llm = Gauge("cottonmouth_agent_llm_calls", "LLM/model calls in the window.", ["agent", "kind"], registry=reg)
    g_tool = Gauge("cottonmouth_agent_tool_calls", "Tool (MCP) calls in the window.", ["agent", "kind"], registry=reg)
    g_tokens = Gauge("cottonmouth_agent_tokens", "Tokens in the window.", ["agent", "kind", "direction"], registry=reg)

    run_stats = compute_all_agent_stats(spans)
    for a in run_stats:
        name = a["agent_name"]
        g_runs.labels(agent=name).set(a["total_runs"])
        g_run_err.labels(agent=name).set(a["error_count"])
        g_err_rate.labels(agent=name).set(a["error_rate"])
        g_infra.labels(agent=name).set(a.get("infra_failure_count", 0))
        g_dur.labels(agent=name).set(a["avg_duration_ms"])
        g_cost.labels(agent=name, kind="run").set(a["total_cost_usd"])

    gw_stats = compute_gateway_agent_stats(spans)
    for a in gw_stats:
        name = a["agent_name"]
        g_cost.labels(agent=name, kind="gateway").set(a["total_cost_usd"])
        g_llm.labels(agent=name, kind="gateway").set(a["call_count"])
        g_tool.labels(agent=name, kind="gateway").set(a["tool_call_count"])
        g_tokens.labels(agent=name, kind="gateway", direction="input").set(a["input_tokens"])
        g_tokens.labels(agent=name, kind="gateway", direction="output").set(a["output_tokens"])

    # --- Governance / compliance (permission_check spans) ---
    g_perm = Gauge(
        "cottonmouth_permission_checks",
        "Authorization checks in the window, by agent / verdict / enforcement mode.",
        ["agent", "result", "mode"],
        registry=reg,
    )
    g_denials = Gauge("cottonmouth_agent_permission_denials", "Denied (out-of-compliance) actions per agent in the window.", ["agent"], registry=reg)
    g_agent_compliance = Gauge(
        "cottonmouth_agent_compliance_ratio",
        "Fraction of an agent's authorization checks that were allowed (0-1).",
        ["agent"],
        registry=reg,
    )
    g_fleet_compliance = Gauge(
        "cottonmouth_compliance_ratio",
        "Fleet-wide fraction of authorization checks that were allowed (0-1).",
        registry=reg,
    )
    g_fleet_checks = Gauge(
        "cottonmouth_permission_checks_total_window",
        "Total authorization checks in the window, by verdict.",
        ["result"],
        registry=reg,
    )

    by_key, agent_totals, fleet = _permission_breakdown(spans)
    for (agent, result, mode), count in by_key.items():
        g_perm.labels(agent=agent, result=result, mode=mode).set(count)
    for agent, tot in agent_totals.items():
        total = tot["allow"] + tot["deny"]
        g_denials.labels(agent=agent).set(tot["deny"])
        g_agent_compliance.labels(agent=agent).set(round(tot["allow"] / total, 4) if total else 1.0)
    fleet_total = fleet["allow"] + fleet["deny"]
    g_fleet_checks.labels(result="allow").set(fleet["allow"])
    g_fleet_checks.labels(result="deny").set(fleet["deny"])
    g_fleet_compliance.set(round(fleet["allow"] / fleet_total, 4) if fleet_total else 1.0)

    return generate_latest(reg), CONTENT_TYPE_LATEST
