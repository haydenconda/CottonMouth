"""Generate sample trace data for CottonMouth dashboard development."""
import json
import random
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
TRACES_FILE = BASE_DIR / "traces.jsonl"
EVENTS_FILE = BASE_DIR / "events.jsonl"

AGENTS = [
    ("support-bot", "1.0.0"),
    ("code-reviewer", "2.1.0"),
    ("data-pipeline", "0.9.3"),
    ("ticket-triager", "1.2.0"),
    ("doc-generator", "0.5.1"),
]

MODELS = [
    ("claude-sonnet-4-20250514", 0.003, 0.015),
    ("claude-haiku-4-20250414", 0.001, 0.005),
    ("gpt-4o", 0.005, 0.015),
    ("claude-opus-4-20250514", 0.015, 0.075),
]

TOOLS = [
    "search_codebase", "read_file", "run_tests", "query_database",
    "send_slack", "create_jira", "fetch_url", "analyze_logs",
]

STATUSES = ["completed", "completed", "completed", "completed", "failed"]


def _ts(offset_minutes: int) -> str:
    t = datetime.now(timezone.utc) - timedelta(minutes=offset_minutes)
    return t.isoformat()


def _span_id() -> str:
    return uuid.uuid4().hex[:16]


def _trace_id() -> str:
    return uuid.uuid4().hex[:32]


def generate_trace(agent_name: str, agent_version: str, offset_minutes: int) -> list[dict]:
    trace_id = _trace_id()
    status = random.choice(STATUSES)
    num_llm_calls = random.randint(1, 5)
    num_tool_calls = random.randint(0, 4)

    root_span_id = _span_id()
    base_time = offset_minutes
    total_duration = 0
    spans = []

    llm_spans = []
    for i in range(num_llm_calls):
        model, in_cost, out_cost = random.choice(MODELS)
        input_tokens = random.randint(200, 4000)
        output_tokens = random.randint(50, 2000)
        cost = (input_tokens / 1000 * in_cost) + (output_tokens / 1000 * out_cost)
        duration = random.randint(500, 8000)
        llm_status = "completed" if status == "completed" or i < num_llm_calls - 1 else status

        span = {
            "trace_id": trace_id,
            "span_id": _span_id(),
            "parent_span_id": root_span_id,
            "agent_name": agent_name,
            "agent_version": agent_version,
            "span_type": "llm_call",
            "name": f"llm_call_{i + 1}",
            "status": llm_status,
            "start_time": _ts(base_time),
            "duration_ms": duration,
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cost_usd": round(cost, 6),
            "temperature": random.choice([0.0, 0.3, 0.5, 0.7]),
            "error": "Rate limit exceeded: too many requests" if llm_status == "failed" else "",
            "metadata": {},
        }
        llm_spans.append(span)
        total_duration += duration

    tool_spans = []
    for i in range(num_tool_calls):
        tool = random.choice(TOOLS)
        duration = random.randint(50, 3000)
        tool_status = "completed" if random.random() > 0.1 else "failed"

        span = {
            "trace_id": trace_id,
            "span_id": _span_id(),
            "parent_span_id": root_span_id,
            "agent_name": agent_name,
            "agent_version": agent_version,
            "span_type": "tool_call",
            "name": tool,
            "status": tool_status,
            "start_time": _ts(base_time),
            "duration_ms": duration,
            "tool_name": tool,
            "tool_input": json.dumps({"query": f"sample {tool} input"}),
            "tool_output": json.dumps({"result": f"sample {tool} output"}) if tool_status == "completed" else "",
            "error": f"Tool '{tool}' timed out after 30s" if tool_status == "failed" else "",
            "metadata": {},
        }
        tool_spans.append(span)
        total_duration += duration

    root_span = {
        "trace_id": trace_id,
        "span_id": root_span_id,
        "parent_span_id": "",
        "agent_name": agent_name,
        "agent_version": agent_version,
        "span_type": "agent_run",
        "name": f"{agent_name}_run",
        "status": status,
        "start_time": _ts(base_time),
        "duration_ms": total_duration + random.randint(100, 500),
        "input_tokens": sum(s.get("input_tokens", 0) for s in llm_spans),
        "output_tokens": sum(s.get("output_tokens", 0) for s in llm_spans),
        "cost_usd": round(sum(s.get("cost_usd", 0) for s in llm_spans), 6),
        "error": llm_spans[-1].get("error", "") if status == "failed" else "",
        "metadata": {"trigger": random.choice(["api", "cron", "webhook", "manual"])},
    }

    spans.append(root_span)
    spans.extend(llm_spans)
    spans.extend(tool_spans)
    return spans


def generate_events(traces: list[list[dict]]) -> list[dict]:
    events = []
    for trace_spans in traces:
        root = next((s for s in trace_spans if s["span_type"] == "agent_run"), None)
        if not root:
            continue

        agent_name = root["agent_name"]
        status = root["status"]
        cost = root.get("cost_usd", 0)
        duration = root.get("duration_ms", 0)
        trace_id = root["trace_id"]

        if status == "failed":
            events.append({
                "ts": root["start_time"],
                "agent": "ticker",
                "severity": "critical",
                "title": f"Agent Failed: {agent_name}",
                "message": f"Error: {root.get('error', 'unknown')[:100]} | ${cost:.4f}",
                "source": "agent-error",
                "action_url": f"cottonmouth://trace/{trace_id}",
            })
        elif cost > 0.5:
            events.append({
                "ts": root["start_time"],
                "agent": "ticker",
                "severity": "warning",
                "title": f"Cost Spike: {agent_name}",
                "message": f"Run cost ${cost:.4f} | {duration}ms",
                "source": "agent-anomaly",
                "action_url": f"cottonmouth://trace/{trace_id}",
            })
        else:
            events.append({
                "ts": root["start_time"],
                "agent": "ticker",
                "severity": "info",
                "title": f"Agent Run: {agent_name}",
                "message": f"Completed in {duration}ms | ${cost:.4f}",
                "source": "agent-trace",
                "action_url": f"cottonmouth://trace/{trace_id}",
            })

    return events


def main():
    all_traces = []
    for i in range(30):
        agent_name, agent_version = random.choice(AGENTS)
        offset = random.randint(1, 1440)
        trace = generate_trace(agent_name, agent_version, offset)
        all_traces.append(trace)

    with open(TRACES_FILE, "w", encoding="utf-8") as f:
        for trace in all_traces:
            for span in trace:
                f.write(json.dumps(span, ensure_ascii=False) + "\n")

    events = generate_events(all_traces)
    with open(EVENTS_FILE, "w", encoding="utf-8") as f:
        for event in events:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")

    total_spans = sum(len(t) for t in all_traces)
    print(f"Generated {len(all_traces)} traces ({total_spans} spans) -> {TRACES_FILE}")
    print(f"Generated {len(events)} events -> {EVENTS_FILE}")


if __name__ == "__main__":
    main()
