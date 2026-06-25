"""Live sample agent — emits realistic CottonMouth traces over HTTP.

This stands in for a real instrumented AI agent. It uses the CottonMouth SDK's HTTP
exporter to continuously ship traces (agent runs with nested LLM + tool spans,
plus occasional failures and cost spikes) to a CottonMouth collector. It's what makes
a live cluster demo move: deploy it alongside the backend and the dashboard
fills with fresh data on its own.

Local:
    COTTONMOUTH_ENDPOINT=http://localhost:8150 python examples/sample_agent.py

Kubernetes:
    Runs as a Deployment with COTTONMOUTH_ENDPOINT=http://cottonmouth-backend:8150.

Env vars:
    COTTONMOUTH_ENDPOINT      Collector base URL (required).
    COTTONMOUTH_API_KEY       Optional bearer token (must match the backend's).
    SAMPLE_INTERVAL    Seconds between runs (default 5).
    SAMPLE_ONCE        If truthy, emit a single burst and exit.
    SAMPLE_BURST       Number of runs in the initial backfill (default 8).
"""
from __future__ import annotations

import os
import random
import signal
import sys
import time

import cottonmouth
from cottonmouth.spans import Span
from cottonmouth.tracer import Tracer, get_exporter

AGENTS = [
    ("support-bot", "1.0.0"),
    ("code-reviewer", "2.1.0"),
    ("data-pipeline", "0.9.3"),
    ("ticket-triager", "1.2.0"),
    ("doc-generator", "0.5.1"),
]

# (model, input $/1k, output $/1k)
MODELS = [
    ("claude-sonnet-4-6", 0.003, 0.015),
    ("claude-haiku-4-5", 0.0008, 0.004),
    ("gpt-4o", 0.0025, 0.01),
    ("claude-opus-4-6", 0.015, 0.075),
]

TOOLS = [
    "search_codebase", "read_file", "run_tests", "query_database",
    "send_slack", "create_jira", "fetch_url", "analyze_logs",
]

# 1 in N runs fails outright; cost spikes happen independently.
_FAIL_RATE = 0.15
_SPIKE_RATE = 0.12

_running = True


def _stop(*_: object) -> None:
    global _running
    _running = False


def _emit_run(tracer: Tracer) -> dict:
    """Build one agent run (root + children), emit every span, return a summary."""
    agent_name, agent_version = random.choice(AGENTS)
    failed = random.random() < _FAIL_RATE
    spike = random.random() < _SPIKE_RATE
    num_llm = random.randint(1, 5)
    num_tools = random.randint(0, 4)

    root = Span(
        trace_id=_new_trace_id(),
        agent_name=agent_name,
        agent_version=agent_version,
        span_type="agent_run",
        name=f"{agent_name}_run",
        status="failed" if failed else "completed",
        metadata={"trigger": random.choice(["api", "cron", "webhook", "manual"])},
    )

    total_cost = 0.0
    total_ms = 0
    children: list[Span] = []

    for i in range(num_llm):
        model, in_rate, out_rate = random.choice(MODELS)
        in_tok = random.randint(200, 4000) * (8 if spike and i == 0 else 1)
        out_tok = random.randint(50, 2000)
        cost = (in_tok / 1000) * in_rate + (out_tok / 1000) * out_rate
        duration = random.randint(500, 8000)
        # Only the last LLM call carries the failure for a failed run.
        llm_failed = failed and i == num_llm - 1
        children.append(Span(
            trace_id=root.trace_id,
            parent_span_id=root.span_id,
            agent_name=agent_name,
            agent_version=agent_version,
            span_type="llm_call",
            name=f"llm_call_{i + 1}",
            status="failed" if llm_failed else "completed",
            duration_ms=duration,
            model=model,
            input_tokens=in_tok,
            output_tokens=out_tok,
            cost_usd=round(cost, 6),
            temperature=random.choice([0.0, 0.3, 0.5, 0.7]),
            error="Rate limit exceeded: too many requests" if llm_failed else "",
        ))
        total_cost += cost
        total_ms += duration

    for _ in range(num_tools):
        tool = random.choice(TOOLS)
        duration = random.randint(50, 3000)
        tool_failed = random.random() < 0.1
        children.append(Span(
            trace_id=root.trace_id,
            parent_span_id=root.span_id,
            agent_name=agent_name,
            agent_version=agent_version,
            span_type="tool_call",
            name=tool,
            status="failed" if tool_failed else "completed",
            duration_ms=duration,
            tool_name=tool,
            tool_input={"query": f"sample {tool} input"},
            tool_output={} if tool_failed else {"result": f"sample {tool} output"},
            error=f"Tool '{tool}' timed out after 30s" if tool_failed else "",
        ))
        total_ms += duration

    root.duration_ms = total_ms + random.randint(100, 500)
    root.input_tokens = sum(c.input_tokens for c in children if c.span_type == "llm_call")
    root.output_tokens = sum(c.output_tokens for c in children if c.span_type == "llm_call")
    root.cost_usd = round(total_cost, 6)
    if failed:
        root.error = next((c.error for c in reversed(children) if c.error), "agent run failed")

    tracer.emit(root)
    for child in children:
        tracer.emit(child)

    return {
        "agent": agent_name,
        "status": root.status,
        "spans": 1 + len(children),
        "cost": root.cost_usd,
        "trace_id": root.trace_id,
    }


def _new_trace_id() -> str:
    import uuid
    return uuid.uuid4().hex[:32]


def main() -> int:
    endpoint = os.environ.get("COTTONMOUTH_ENDPOINT", "")
    if not endpoint:
        print("COTTONMOUTH_ENDPOINT is required (e.g. http://localhost:8150)", file=sys.stderr)
        return 2

    cottonmouth.configure(export="http", endpoint=endpoint, auto_instrument=False)
    print(f"[sample-agent] shipping traces to {endpoint}/api/spans")

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    tracers = {name: Tracer(agent_name=name, agent_version=ver) for name, ver in AGENTS}

    def _pick_tracer() -> Tracer:
        name, _ = random.choice(AGENTS)
        return tracers[name]

    # Initial backfill so the dashboard isn't empty on first load.
    burst = int(os.environ.get("SAMPLE_BURST", "8"))
    for _ in range(burst):
        summary = _emit_run(_pick_tracer())
        print(f"[sample-agent] {summary['agent']:<14} {summary['status']:<9} "
              f"{summary['spans']} spans  ${summary['cost']:.4f}")

    if os.environ.get("SAMPLE_ONCE", "").lower() in ("1", "true", "yes"):
        get_exporter().flush()
        return 0

    interval = float(os.environ.get("SAMPLE_INTERVAL", "5"))
    while _running:
        summary = _emit_run(_pick_tracer())
        print(f"[sample-agent] {summary['agent']:<14} {summary['status']:<9} "
              f"{summary['spans']} spans  ${summary['cost']:.4f}")
        # Sleep in small slices so SIGTERM is honored promptly.
        slept = 0.0
        while _running and slept < interval:
            time.sleep(0.25)
            slept += 0.25

    get_exporter().flush()
    print("[sample-agent] stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
