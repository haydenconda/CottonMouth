from __future__ import annotations

import logging
from typing import Any

from .context import get_trace_id, get_span_id, get_agent_name, set_context, reset_context
from .exporters import Exporter, NoopExporter
from .spans import Span, _uuid

log = logging.getLogger("cottonmouth")

_global_exporter: Exporter = NoopExporter()


def set_exporter(exporter: Exporter) -> None:
    global _global_exporter
    _global_exporter = exporter


def get_exporter() -> Exporter:
    return _global_exporter


class Tracer:
    """Creates and manages spans within a trace context."""

    def __init__(self, agent_name: str, agent_version: str = "") -> None:
        self.agent_name = agent_name
        self.agent_version = agent_version

    def start_trace(self, name: str = "", metadata: dict[str, Any] | None = None) -> Span:
        trace_id = _uuid()
        span = Span(
            trace_id=trace_id,
            agent_name=self.agent_name,
            agent_version=self.agent_version,
            span_type="agent_run",
            name=name or self.agent_name,
            metadata=metadata or {},
        )
        return span

    def start_span(
        self,
        name: str,
        span_type: str = "tool_call",
        input_data: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Span:
        trace_id = get_trace_id()
        parent_id = get_span_id()

        if not trace_id:
            trace_id = _uuid()
            log.warning("start_span called outside trace context, creating orphan span")

        span = Span(
            trace_id=trace_id,
            parent_span_id=parent_id or "",
            agent_name=self.agent_name,
            agent_version=self.agent_version,
            span_type=span_type,
            name=name,
            input_data=input_data or {},
            metadata=metadata or {},
        )
        return span

    def emit(self, span: Span) -> None:
        try:
            _global_exporter.export(span)
        except Exception:
            log.exception("Failed to export span %s", span.span_id)

    def _child(self, span_type: str, name: str) -> Span:
        return Span(
            trace_id=get_trace_id() or _uuid(),
            parent_span_id=get_span_id() or "",
            agent_name=self.agent_name,
            agent_version=self.agent_version,
            span_type=span_type,
            name=name,
        )

    def log_decision(
        self,
        name: str,
        reasoning: str,
        options: list[Any],
        chosen: str,
        decision_type: str = "tool_select",
    ) -> Span:
        """Record why the agent chose a path (the 'why did it do it' pillar)."""
        span = self._child("decision", name)
        span.decision_type = decision_type
        span.reasoning = reasoning
        span.options_considered = [
            o if isinstance(o, dict) else {"option": str(o)} for o in options
        ]
        span.chosen_option = chosen
        span.finish()
        self.emit(span)
        return span

    def log_permission(
        self,
        action: str,
        resource: str,
        allowed: bool,
        policy: str,
    ) -> Span:
        """Record an authorization check (the 'what was it allowed to do' pillar)."""
        span = self._child("permission_check", f"{action}: {resource}")
        span.tool_name = action
        span.tool_input = {"resource": resource}
        span.permission_result = "allow" if allowed else "deny"
        span.permission_policy = policy
        span.finish(status="completed" if allowed else "failed")
        if not allowed:
            span.error = f"Denied by policy: {policy}"
        self.emit(span)
        return span


class SpanContext:
    """Context manager that sets trace context and auto-finishes the span."""

    def __init__(self, tracer: Tracer, span: Span) -> None:
        self.tracer = tracer
        self.span = span
        self._tokens: tuple | None = None

    def __enter__(self) -> Span:
        self._tokens = set_context(
            self.span.trace_id, self.span.span_id, self.tracer.agent_name
        )
        return self.span

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        if exc_type:
            self.span.finish(status="failed", error=str(exc_val))
        elif self.span.status == "started":
            self.span.finish()
        self.tracer.emit(self.span)
        if self._tokens:
            reset_context(self._tokens)

    async def __aenter__(self) -> Span:
        return self.__enter__()

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        self.__exit__(exc_type, exc_val, exc_tb)
