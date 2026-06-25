from __future__ import annotations

import asyncio
import functools
from typing import Any, Callable

from .context import set_context, reset_context
from .tracer import Tracer, get_exporter
from .spans import Span


def trace_agent(
    name: str,
    version: str = "",
    metadata: dict[str, Any] | None = None,
) -> Callable:
    """Decorator that wraps a function as a traced agent run.

    Usage:
        @trace_agent(name="my-bot")
        async def handle(query: str):
            ...
    """

    def decorator(fn: Callable) -> Callable:
        tracer = Tracer(agent_name=name, agent_version=version)

        @functools.wraps(fn)
        async def async_wrapper(*args, **kwargs):
            span = tracer.start_trace(
                name=name,
                metadata={**(metadata or {}), "args_count": len(args)},
            )
            tokens = set_context(span.trace_id, span.span_id, name)
            try:
                result = await fn(*args, **kwargs)
                span.finish(status="completed")
                return result
            except Exception as e:
                span.finish(status="failed", error=str(e))
                raise
            finally:
                tracer.emit(span)
                reset_context(tokens)

        @functools.wraps(fn)
        def sync_wrapper(*args, **kwargs):
            span = tracer.start_trace(
                name=name,
                metadata={**(metadata or {}), "args_count": len(args)},
            )
            tokens = set_context(span.trace_id, span.span_id, name)
            try:
                result = fn(*args, **kwargs)
                span.finish(status="completed")
                return result
            except Exception as e:
                span.finish(status="failed", error=str(e))
                raise
            finally:
                tracer.emit(span)
                reset_context(tokens)

        if asyncio.iscoroutinefunction(fn):
            return async_wrapper
        return sync_wrapper

    return decorator
