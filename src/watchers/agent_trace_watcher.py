"""Watches agent trace JSONL files and emits events for failures, anomalies, and cost spikes."""
from __future__ import annotations

import json
import logging
from pathlib import Path

import aiohttp

from src.common.agent_stats import is_infra_failure
from src.common.events import emit_event
from src.common.paths import traces_file
from src.common.state import has_seen, mark_seen, get_value, set_value, prune_old
from src.watchers import BaseWatcher, WatcherResult, WATCHER

log = logging.getLogger("cottonmouth.agent-trace")


class AgentTraceWatcher(BaseWatcher):
    name = "agent-trace"

    def __init__(self, traces_dir: str, cost_threshold: float = 1.0, latency_multiplier: float = 2.0) -> None:
        self._traces_file = Path(traces_dir) / "traces.jsonl" if traces_dir else traces_file()
        self._traces_dir = self._traces_file.parent
        self._cost_threshold = cost_threshold
        self._latency_multiplier = latency_multiplier
        self._file_offset: int = 0
        if self._traces_file.exists():
            self._file_offset = self._traces_file.stat().st_size

    async def poll(self, session: aiohttp.ClientSession) -> WatcherResult:
        try:
            if not self._traces_file.exists():
                return WatcherResult()

            current_size = self._traces_file.stat().st_size
            if current_size <= self._file_offset:
                if current_size < self._file_offset:
                    self._file_offset = 0
                return WatcherResult()

            with open(self._traces_file, "r", encoding="utf-8") as f:
                f.seek(self._file_offset)
                new_data = f.read()
            self._file_offset = current_size

            spans: list[dict] = []
            for line in new_data.strip().split("\n"):
                if not line.strip():
                    continue
                try:
                    spans.append(json.loads(line))
                except json.JSONDecodeError:
                    continue

            traces: dict[str, list[dict]] = {}
            for span in spans:
                tid = span.get("trace_id", "")
                if tid:
                    traces.setdefault(tid, []).append(span)

            for trace_id, trace_spans in traces.items():
                self._process_trace(trace_id, trace_spans)

            prune_old(self.name, keep_latest=1000)
            return WatcherResult()

        except Exception as e:
            log.exception("Agent trace watcher error")
            return WatcherResult(ok=False, error=str(e))

    def _process_trace(self, trace_id: str, spans: list[dict]) -> None:
        root_spans = [s for s in spans if s.get("span_type") == "agent_run"]
        agent_name = spans[0].get("agent_name", "unknown")

        for span in spans:
            self._check_span(trace_id, span, agent_name)

        for root in root_spans:
            self._check_agent_run(trace_id, root, spans, agent_name)

    def _check_span(self, trace_id: str, span: dict, agent_name: str) -> None:
        span_id = span.get("span_id", "")
        status = span.get("status", "")
        span_type = span.get("span_type", "")

        if status == "failed" and span_type == "tool_call":
            item_id = f"tool-fail-{span_id}"
            if not has_seen(self.name, item_id):
                mark_seen(self.name, item_id)
                tool_name = span.get("tool_name", "") or span.get("name", "unknown")
                error = span.get("error", "")[:200]
                emit_event(
                    agent=WATCHER,
                    severity="warning",
                    title=f"Tool Failed: {tool_name}",
                    message=f"Agent '{agent_name}' tool call failed: {error}",
                    source="agent-trace",
                    action_url=f"cottonmouth://trace/{trace_id}/span/{span_id}",
                )

        if span_type == "permission_check" and span.get("permission_result") == "deny":
            item_id = f"perm-deny-{span_id}"
            if not has_seen(self.name, item_id):
                mark_seen(self.name, item_id)
                action = span.get("tool_name", "") or span.get("name", "action")
                policy = span.get("permission_policy", "")[:200]
                emit_event(
                    agent=WATCHER,
                    severity="warning",
                    title=f"Permission Denied: {action}",
                    message=f"Agent '{agent_name}' was blocked: {policy}",
                    source="agent-permission",
                    action_url=f"cottonmouth://trace/{trace_id}/span/{span_id}",
                )

        if status == "failed" and span_type == "llm_call":
            item_id = f"llm-fail-{span_id}"
            if not has_seen(self.name, item_id):
                mark_seen(self.name, item_id)
                model = span.get("model", "unknown")
                error = span.get("error", "")[:200]
                emit_event(
                    agent=WATCHER,
                    severity="critical",
                    title=f"LLM Call Failed: {model}",
                    message=f"Agent '{agent_name}' LLM call failed: {error}",
                    source="agent-error",
                    action_url=f"cottonmouth://trace/{trace_id}/span/{span_id}",
                )

    def _check_agent_run(self, trace_id: str, root: dict, spans: list[dict], agent_name: str) -> None:
        status = root.get("status", "")
        if status not in ("completed", "failed"):
            return

        item_id = f"run-{trace_id}"
        if has_seen(self.name, item_id):
            return
        mark_seen(self.name, item_id)

        duration_ms = root.get("duration_ms", 0)
        total_cost = sum(s.get("cost_usd", 0) for s in spans)
        total_tokens = sum(s.get("input_tokens", 0) + s.get("output_tokens", 0) for s in spans)
        llm_calls = sum(1 for s in spans if s.get("span_type") == "llm_call")
        tool_calls = sum(1 for s in spans if s.get("span_type") == "tool_call")
        perm_denials = sum(
            1 for s in spans
            if s.get("span_type") == "permission_check" and s.get("permission_result") == "deny"
        )

        if status == "failed":
            error = root.get("error", "")[:200]
            if is_infra_failure(error):
                # Infra failures (expired creds, throttling) are an environment
                # problem, not an agent error -- surface them separately and
                # don't let them pollute the agent's error rate.
                emit_event(
                    agent=WATCHER,
                    severity="warning",
                    title=f"Infrastructure Issue: {agent_name}",
                    message=f"Run blocked by infrastructure (not agent logic): {error}",
                    source="agent-infra",
                    action_url=f"cottonmouth://trace/{trace_id}",
                )
                self._update_stats(agent_name, duration_ms, total_cost, success=True)
                return
            emit_event(
                agent=WATCHER,
                severity="critical",
                title=f"Agent Failed: {agent_name}",
                message=f"Error: {error} | {llm_calls} LLM calls, {tool_calls} tool calls, ${total_cost:.4f}",
                source="agent-error",
                action_url=f"cottonmouth://trace/{trace_id}",
            )
            self._update_stats(agent_name, duration_ms, total_cost, success=False)
            return

        self._update_stats(agent_name, duration_ms, total_cost, success=True)

        if total_cost >= self._cost_threshold:
            emit_event(
                agent=WATCHER,
                severity="warning",
                title=f"Cost Spike: {agent_name}",
                message=f"Run cost ${total_cost:.4f} (threshold ${self._cost_threshold:.2f}) | {total_tokens} tokens, {duration_ms}ms",
                source="agent-anomaly",
                action_url=f"cottonmouth://trace/{trace_id}",
            )

        avg_ms = self._get_avg_duration(agent_name)
        if avg_ms > 0 and duration_ms > avg_ms * self._latency_multiplier:
            emit_event(
                agent=WATCHER,
                severity="warning",
                title=f"Slow Run: {agent_name}",
                message=f"Took {duration_ms}ms (avg {avg_ms:.0f}ms, {self._latency_multiplier}x threshold) | {llm_calls} LLM calls",
                source="agent-anomaly",
                action_url=f"cottonmouth://trace/{trace_id}",
            )

        emit_event(
            agent=WATCHER,
            severity="info",
            title=f"Agent Run: {agent_name}",
            message=(
                f"Completed in {duration_ms}ms | {llm_calls} LLM, {tool_calls} tools, "
                f"{perm_denials} denied, ${total_cost:.4f}"
            ),
            source="agent-trace",
            action_url=f"cottonmouth://trace/{trace_id}",
        )

    def _update_stats(self, agent_name: str, duration_ms: int, cost: float, success: bool) -> None:
        key_prefix = f"agent-stats-{agent_name}"
        count = int(get_value(self.name, f"{key_prefix}-count", "0")) + 1
        total_ms = int(get_value(self.name, f"{key_prefix}-total-ms", "0")) + duration_ms
        total_cost = float(get_value(self.name, f"{key_prefix}-total-cost", "0")) + cost
        errors = int(get_value(self.name, f"{key_prefix}-errors", "0")) + (0 if success else 1)

        set_value(self.name, f"{key_prefix}-count", str(count))
        set_value(self.name, f"{key_prefix}-total-ms", str(total_ms))
        set_value(self.name, f"{key_prefix}-total-cost", str(total_cost))
        set_value(self.name, f"{key_prefix}-errors", str(errors))

    def _get_avg_duration(self, agent_name: str) -> float:
        """Average duration of recent *successful* runs, read from the retained
        traces. Deliberately not the cumulative counter: fast-failing infra
        errors (e.g. an expired-credential outage) used to drag the average
        down and trigger spurious "slow run" alerts on healthy runs."""
        try:
            durations: list[int] = []
            with open(self._traces_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        s = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if (
                        s.get("span_type") == "agent_run"
                        and s.get("agent_name") == agent_name
                        and s.get("status") == "completed"
                    ):
                        durations.append(s.get("duration_ms", 0))
        except OSError:
            return 0
        recent = durations[-50:]
        if len(recent) < 3:
            return 0
        return sum(recent) / len(recent)
