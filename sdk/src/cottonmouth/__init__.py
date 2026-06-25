"""CottonMouth — AI agent observability SDK."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import os

from .decorator import trace_agent
from .tracer import Tracer, SpanContext, set_exporter
from .spans import Span
from .exporters import JSONLExporter, NoopExporter, HTTPExporter
from .context import get_trace_id, get_span_id, get_agent_name

__all__ = [
    "configure",
    "trace_agent",
    "Tracer",
    "Span",
    "SpanContext",
    "HTTPExporter",
    "JSONLExporter",
    "get_trace_id",
    "get_span_id",
    "get_agent_name",
]


def configure(
    export: str = "jsonl",
    path: str | Path = "./traces.jsonl",
    endpoint: str = "",
    api_key: str = "",
    auto_instrument: bool = True,
) -> None:
    """Initialize CottonMouth tracing.

    Args:
        export: Exporter type ("jsonl", "http", or "noop"). Defaults to the
            ``COTTONMOUTH_EXPORT`` env var when unset.
        path: File path for the JSONL exporter (``COTTONMOUTH_TRACES_PATH``).
        endpoint: Collector base URL for the HTTP exporter, e.g.
            ``http://cottonmouth-backend:8150`` (``COTTONMOUTH_ENDPOINT``).
        api_key: Optional bearer token for the HTTP exporter (``COTTONMOUTH_API_KEY``).
        auto_instrument: Patch known LLM SDKs for automatic tracing.

    Env-var fallbacks let the same image run unchanged in local, Docker, and
    Kubernetes contexts. If ``COTTONMOUTH_ENDPOINT`` is set, the HTTP exporter is used
    automatically.
    """
    export = export or os.environ.get("COTTONMOUTH_EXPORT", "jsonl")
    endpoint = endpoint or os.environ.get("COTTONMOUTH_ENDPOINT", "")
    api_key = api_key or os.environ.get("COTTONMOUTH_API_KEY", "")
    path = os.environ.get("COTTONMOUTH_TRACES_PATH", str(path))

    if endpoint and export in ("jsonl", "http"):
        export = "http"

    if export == "http":
        if not endpoint:
            raise ValueError("export='http' requires an endpoint or COTTONMOUTH_ENDPOINT")
        set_exporter(HTTPExporter(endpoint, api_key=api_key))
    elif export == "jsonl":
        set_exporter(JSONLExporter(path))
    else:
        set_exporter(NoopExporter())

    if auto_instrument:
        from .llm_hooks import patch_all
        patch_all()
