from __future__ import annotations

from contextvars import ContextVar
from typing import Optional

_current_trace_id: ContextVar[Optional[str]] = ContextVar("cottonmouth_trace_id", default=None)
_current_span_id: ContextVar[Optional[str]] = ContextVar("cottonmouth_span_id", default=None)
_current_agent_name: ContextVar[Optional[str]] = ContextVar("cottonmouth_agent_name", default=None)


def get_trace_id() -> str | None:
    return _current_trace_id.get()


def get_span_id() -> str | None:
    return _current_span_id.get()


def get_agent_name() -> str | None:
    return _current_agent_name.get()


def set_context(trace_id: str, span_id: str, agent_name: str = "") -> tuple:
    t1 = _current_trace_id.set(trace_id)
    t2 = _current_span_id.set(span_id)
    t3 = _current_agent_name.set(agent_name)
    return (t1, t2, t3)


def reset_context(tokens: tuple) -> None:
    t1, t2, t3 = tokens
    _current_trace_id.reset(t1)
    _current_span_id.reset(t2)
    _current_agent_name.reset(t3)
