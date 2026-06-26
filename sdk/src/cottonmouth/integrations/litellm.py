"""LiteLLM ⇄ CottonMouth integration (observability).

LiteLLM (the open-source LLM gateway) gets your requests to the model and
enforces the gateway-level controls — model access, budgets, rate limits,
guardrails — via virtual keys. CottonMouth does **not** duplicate that. This
module provides a ``CustomLogger`` that turns every completed LiteLLM call into a
CottonMouth ``llm_call`` span (model, tokens, cost, latency, origin) and, when
the gateway *rejects* a call, records the gateway's own verdict as a
``permission_check`` — observing enforcement, not re-implementing it.

Works in both LiteLLM modes:

SDK::

    import cottonmouth
    from cottonmouth.integrations.litellm import enable

    cottonmouth.configure(export="http", endpoint="http://cottonmouth-backend:8150")
    enable()   # registers the logger

Proxy (``config.yaml``)::

    litellm_settings:
      callbacks: cottonmouth.integrations.litellm.cottonmouth_callback

Correlation: gateway calls nest under the owning ``agent_run`` when CottonMouth
context is available, resolved in three tiers:

1. Explicit LiteLLM ``metadata.cottonmouth`` (survives thread/process hops) --
   inject it at the call site with :func:`with_cottonmouth`.
2. In-process contextvars (same-thread SDK usage).
3. A standalone trace, so a call is never dropped for lack of context.
"""
from __future__ import annotations

import logging
import os
import socket
import sys
from collections import OrderedDict
from datetime import datetime
from typing import Any

from ..context import get_agent_name, get_span_id, get_trace_id
from ..spans import Span, _now_iso, _uuid
from ..tracer import get_exporter
from .gateway import classify_gateway_denial, infer_provider

log = logging.getLogger("cottonmouth.litellm")

try:  # The base class only exists when litellm is installed (the extra).
    from litellm.integrations.custom_logger import CustomLogger as _CustomLogger
except Exception:  # pragma: no cover - exercised only without the extra installed
    class _CustomLogger:  # type: ignore[no-redef]
        """Fallback base so importing this module never hard-fails."""


_METADATA_KEY = "cottonmouth"


def _caller_location() -> str:
    """Best-effort 'file.py:line:func' of the call site that invoked LiteLLM,
    skipping frames inside this SDK and litellm itself."""
    try:
        frame = sys._getframe(2)
    except (ValueError, AttributeError):
        return ""
    while frame is not None:
        name = frame.f_globals.get("__name__", "")
        if not (name.startswith("cottonmouth") or name.startswith("litellm")):
            fn = frame.f_code.co_filename.rsplit("/", 1)[-1]
            return f"{fn}:{frame.f_lineno}:{frame.f_code.co_name}"
        frame = frame.f_back
    return ""


_HOST_INFO: dict[str, Any] | None = None


def _ensure_configured() -> None:
    """Self-configure the exporter from env when nobody called
    ``cottonmouth.configure()`` — e.g. the LiteLLM proxy process that just loaded
    this callback. No-op if an exporter is already set or no endpoint is given.
    """
    if type(get_exporter()).__name__ != "NoopExporter":
        return
    if not os.environ.get("COTTONMOUTH_ENDPOINT"):
        return
    try:
        import cottonmouth  # noqa: PLC0415 - lazy to avoid import cycles
        # auto_instrument=False is critical: this callback IS the single source of
        # the llm_call span. Patching the SDKs here too would double-log every
        # gateway call (one span from the callback, one from the patched SDK).
        cottonmouth.configure(
            export=os.environ.get("COTTONMOUTH_EXPORT", "http"),
            auto_instrument=False,
        )
    except Exception:  # pragma: no cover - defensive
        log.exception("CottonmouthLogger auto-configure failed")


def _host_info() -> dict[str, Any]:
    global _HOST_INFO
    if _HOST_INFO is None:
        _HOST_INFO = {
            "host": socket.gethostname(),
            # In Kubernetes the pod name is injected as HOSTNAME by default.
            "pod": os.environ.get("HOSTNAME", ""),
            "pid": os.getpid(),
        }
    return _HOST_INFO


def with_cottonmouth(agent_name: str = "", **extra: Any) -> dict[str, Any]:
    """Return kwargs that thread the *current* CottonMouth context into a
    LiteLLM call so the resulting span nests under the active ``agent_run`` and
    records where the call originated.

    Usage::

        litellm.completion(model=..., messages=..., **with_cottonmouth())

    Falls back to whatever context is set; pass ``agent_name`` to override.
    The returned ``metadata`` is merged by LiteLLM, so existing metadata on the
    call is preserved.
    """
    host = _host_info()
    ctx = {
        "trace_id": get_trace_id() or "",
        "parent_span_id": get_span_id() or "",
        "agent_name": agent_name or get_agent_name() or "",
        "caller": _caller_location(),
        # Where the call ORIGINATES (the caller's process). When routed through
        # the in-cluster gateway, the span is built in the gateway pod, so this
        # preserves the true origin.
        "host": host["host"],
        "pod": host["pod"],
        "pid": host["pid"],
    }
    ctx.update(extra)
    return {"metadata": {_METADATA_KEY: ctx}}


def _as_dict(obj: Any) -> dict[str, Any]:
    if obj is None:
        return {}
    if isinstance(obj, dict):
        return obj
    for attr in ("model_dump", "dict"):
        fn = getattr(obj, attr, None)
        if callable(fn):
            try:
                return fn()
            except Exception:
                pass
    return {}


def _duration_ms(start: Any, end: Any) -> int:
    try:
        if isinstance(start, datetime) and isinstance(end, datetime):
            return max(0, int((end - start).total_seconds() * 1000))
        return max(0, int((float(end) - float(start)) * 1000))
    except Exception:
        return 0


def _metadata(kwargs: dict[str, Any]) -> dict[str, Any]:
    """Merge the request metadata LiteLLM exposes across call phases:
    top-level ``metadata`` (present pre-call), ``litellm_params.metadata`` and
    ``litellm_metadata`` (post-call)."""
    merged: dict[str, Any] = {}
    top = kwargs.get("metadata")
    if isinstance(top, dict):
        merged.update(top)
    params = kwargs.get("litellm_params") or {}
    md = params.get("metadata")
    if isinstance(md, dict):
        merged.update(md)
    lm = kwargs.get("litellm_metadata") or params.get("litellm_metadata")
    if isinstance(lm, dict):
        merged.update(lm)
    return merged


def _call_identity(kwargs: dict[str, Any], slo: dict[str, Any] | None = None) -> str:
    """Resolve a non-empty identity for the call, or '' if anonymous.

    Order: explicit cottonmouth agent_name -> in-process contextvar agent ->
    LiteLLM virtual-key / team / user.
    """
    slo = slo or {}
    md = _metadata(kwargs)
    cm = md.get(_METADATA_KEY)
    if isinstance(cm, dict) and cm.get("agent_name"):
        return str(cm["agent_name"])
    if get_agent_name():
        return str(get_agent_name())
    for key in ("user_api_key_alias", "user_api_key_team_alias",
                "user_api_key_team_id", "user_api_key_user_id"):
        val = md.get(key) or slo.get(key)
        if val:
            return str(val)
    return ""


def _resolve_context(kwargs: dict[str, Any]) -> tuple[str, str, str, bool]:
    """Resolve (trace_id, parent_span_id, agent_name, correlated)."""
    md = _metadata(kwargs)
    cm = md.get(_METADATA_KEY)
    if isinstance(cm, dict) and cm.get("trace_id"):
        return (
            str(cm.get("trace_id")),
            str(cm.get("parent_span_id", "")),
            str(cm.get("agent_name", "")),
            True,
        )

    trace_id = get_trace_id()
    if trace_id:
        return trace_id, (get_span_id() or ""), (get_agent_name() or ""), True

    return _uuid(), "", "", False


def _identity_agent_name(kwargs: dict[str, Any], slo: dict[str, Any]) -> str:
    """Best-effort agent identity from LiteLLM virtual-key / team / user."""
    md = _metadata(kwargs)
    for key in ("user_api_key_alias", "user_api_key_team_alias",
                "user_api_key_team_id", "user_api_key_user_id"):
        val = md.get(key) or slo.get(key) or (slo.get("metadata") or {}).get(key)
        if val:
            return str(val)
    return "litellm-gateway"


class CottonmouthLogger(_CustomLogger):
    """Maps completed LiteLLM calls to CottonMouth ``llm_call`` spans.

    Non-blocking and defensive: the CottonMouth HTTP exporter ships spans from a
    background thread, and every hook is wrapped so an error here can never break
    the LiteLLM request path.
    """

    def __init__(self, *, dedupe_window: int = 4096) -> None:
        super().__init__()
        # Guard against the same call being logged twice (e.g. a hook firing on
        # both the sync and async path). Bounded LRU of litellm_call_id.
        self._seen: "OrderedDict[str, None]" = OrderedDict()
        self._dedupe_window = dedupe_window
        _ensure_configured()

    # ---- LiteLLM hooks --------------------------------------------------

    def log_success_event(self, kwargs, response_obj, start_time, end_time):
        self._record(kwargs, response_obj, start_time, end_time, failed=False)

    def log_failure_event(self, kwargs, response_obj, start_time, end_time):
        self._record(kwargs, response_obj, start_time, end_time, failed=True)

    async def async_log_success_event(self, kwargs, response_obj, start_time, end_time):
        self._record(kwargs, response_obj, start_time, end_time, failed=False)

    async def async_log_failure_event(self, kwargs, response_obj, start_time, end_time):
        self._record(kwargs, response_obj, start_time, end_time, failed=True)

    # Streaming: deliberately NOT handled per-chunk. LiteLLM fires a single
    # (async_)log_success_event at stream end with the aggregated response, so we
    # emit exactly one span per call instead of one span per token.

    # ---- mapping --------------------------------------------------------

    def _record(self, kwargs, response_obj, start_time, end_time, *, failed: bool) -> None:
        try:
            span = self._build_span(kwargs, response_obj, start_time, end_time, failed)
            if span is not None:
                get_exporter().export(span)
                self._emit_gateway_decision(span)
        except Exception:  # never break the LiteLLM request path
            log.exception("CottonmouthLogger failed to record a span")

    def _emit_gateway_decision(self, llm_span: Span) -> None:
        """Record the GATEWAY's verdict for this call as a permission_check.

        On success -> the gateway allowed the call. On a failure that the gateway
        rejected for a policy reason (budget / model-access / rate-limit / auth /
        guardrail) -> a deny, attributed to LiteLLM. Plain provider/transport
        failures are left as the failed llm_call span only (no false verdict).
        """
        denied = reason = None
        if llm_span.status == "failed":
            is_denial, reason = classify_gateway_denial(llm_span.error or "")
            if not is_denial:
                return  # not a governance decision; the failed llm_call says it all
            denied = True

        result = "deny" if denied else "allow"
        policy = f"litellm-gateway:{reason}" if denied else "litellm-gateway"
        check = Span(
            trace_id=llm_span.trace_id,
            # Nest the verdict UNDER the llm_call it authorized (not beside it):
            # the gateway's allow/deny is part of that one call, so the waterfall
            # should read as a single step with its authorization stamp.
            parent_span_id=llm_span.span_id,
            agent_name=llm_span.agent_name,
            span_type="permission_check",
            name=f"gateway {'denied' if denied else 'allowed'}: {llm_span.name}",
            tool_name="llm_call",
            tool_input={"model": llm_span.model, "resource": llm_span.model},
            permission_result=result,
            permission_policy=policy,
            start_time=llm_span.start_time,
            end_time=llm_span.end_time,
            status="failed" if denied else "completed",
        )
        if denied:
            check.error = (llm_span.error or "")[:300]
        check.metadata = {"source": "litellm", "enforced_by": "litellm", "reason": reason or ""}
        get_exporter().export(check)

    def _is_duplicate(self, call_id: str) -> bool:
        if not call_id:
            return False
        if call_id in self._seen:
            return True
        self._seen[call_id] = None
        while len(self._seen) > self._dedupe_window:
            self._seen.popitem(last=False)
        return False

    def _build_span(self, kwargs, response_obj, start_time, end_time, failed: bool) -> Span | None:
        kwargs = kwargs or {}
        slo = kwargs.get("standard_logging_object") or {}
        if not isinstance(slo, dict):
            slo = {}

        call_id = str(kwargs.get("litellm_call_id") or slo.get("id") or "")
        if self._is_duplicate(call_id):
            return None

        trace_id, parent_span_id, agent_name, correlated = _resolve_context(kwargs)
        if not agent_name:
            agent_name = _identity_agent_name(kwargs, slo)

        model = str(kwargs.get("model") or slo.get("model") or "unknown")
        params = kwargs.get("litellm_params") or {}
        provider = str(
            params.get("custom_llm_provider")
            or slo.get("custom_llm_provider")
            or kwargs.get("custom_llm_provider")
            or ""
        )

        in_tok, out_tok = self._tokens(response_obj, slo)
        cost = self._cost(kwargs, slo)

        span = Span(
            trace_id=trace_id,
            parent_span_id=parent_span_id,
            agent_name=agent_name,
            span_type="llm_call",
            name=model.rsplit("/", 1)[-1],
            model=model,
            input_tokens=in_tok,
            output_tokens=out_tok,
            cost_usd=round(float(cost), 6),
            start_time=self._iso(start_time),
            end_time=self._iso(end_time),
            duration_ms=_duration_ms(start_time, end_time),
            status="failed" if failed else "completed",
        )
        identity = _call_identity(kwargs, slo)

        span.input_data = {
            "message_count": len(kwargs.get("messages") or []),
            "call_type": kwargs.get("call_type", ""),
        }
        span.metadata = {
            "source": "litellm",
            "provider": provider or infer_provider(model),
            "correlated": correlated,
            "litellm_call_id": call_id,
            "litellm_identity": self._identity_block(kwargs, slo),
            "origin": self._origin(kwargs, identity, provider or infer_provider(model)),
        }
        if failed:
            span.error = str(
                kwargs.get("exception")
                or _as_dict(response_obj).get("error")
                or slo.get("error_str")
                or "LiteLLM call failed"
            )[:300]
        return span

    @staticmethod
    def _iso(t: Any) -> str:
        if isinstance(t, datetime):
            return t.isoformat()
        return _now_iso()

    @staticmethod
    def _tokens(response_obj: Any, slo: dict[str, Any]) -> tuple[int, int]:
        usage = _as_dict(getattr(response_obj, "usage", None)) or _as_dict(response_obj).get("usage", {})
        in_tok = usage.get("prompt_tokens") or slo.get("prompt_tokens") or 0
        out_tok = usage.get("completion_tokens") or slo.get("completion_tokens") or 0
        try:
            return int(in_tok), int(out_tok)
        except (TypeError, ValueError):
            return 0, 0

    @staticmethod
    def _cost(kwargs: dict[str, Any], slo: dict[str, Any]) -> float:
        """Prefer LiteLLM's computed cost (authoritative across providers)."""
        cost = kwargs.get("response_cost")
        if cost is None:
            cost = slo.get("response_cost")
        try:
            return float(cost) if cost is not None else 0.0
        except (TypeError, ValueError):
            return 0.0

    @staticmethod
    def _identity_block(kwargs: dict[str, Any], slo: dict[str, Any]) -> dict[str, Any]:
        md = _metadata(kwargs)
        out = {}
        for key in ("user_api_key_alias", "user_api_key_team_alias",
                    "user_api_key_team_id", "user_api_key_user_id"):
            val = md.get(key) or slo.get(key)
            if val:
                out[key] = str(val)
        tags = md.get("tags") or slo.get("request_tags")
        if tags:
            out["tags"] = tags
        return out

    @staticmethod
    def _origin(kwargs: dict[str, Any], identity: str, provider: str) -> dict[str, Any]:
        """Where the call came from: agent/identity, originating host/pod/pid, and
        the caller site. When the span is built in a different process than the
        caller (gateway/proxy mode), also record where it was executed."""
        md = _metadata(kwargs)
        cm = md.get(_METADATA_KEY)
        cm = cm if isinstance(cm, dict) else {}
        local = _host_info()
        info = {
            "agent": cm.get("agent_name") or identity,
            "identity": identity,
            "provider": provider,
            "caller": cm.get("caller", ""),
            # Prefer the caller-supplied origin (survives the hop to the gateway).
            "host": cm.get("host") or local["host"],
            "pod": cm.get("pod") or local["pod"],
            "pid": cm.get("pid") or local["pid"],
        }
        if cm.get("pod") and cm.get("pod") != local["pod"]:
            info["executed_at"] = local["pod"] or local["host"]
        return info


def enable() -> CottonmouthLogger:
    """One-call setup: register the CottonMouth logger as a LiteLLM callback.

    Observability only — the gateway (LiteLLM virtual keys / proxy config) owns
    enforcement; this records what happened and the gateway's verdicts. Returns
    the registered logger. Idempotent w.r.t. our own callback.
    """
    import litellm  # noqa: PLC0415 - optional dependency, imported on use
    logger = CottonmouthLogger()
    existing = [c for c in (litellm.callbacks or []) if not isinstance(c, CottonmouthLogger)]
    litellm.callbacks = [*existing, logger]
    return logger


# Ready-to-use instance for proxy config.yaml:
#   litellm_settings:
#     callbacks: cottonmouth.integrations.litellm.cottonmouth_callback
cottonmouth_callback = CottonmouthLogger()

__all__ = [
    "CottonmouthLogger",
    "cottonmouth_callback",
    "with_cottonmouth",
    "enable",
]
