from __future__ import annotations

import functools
import logging
from typing import Any

from .context import get_trace_id, get_span_id, get_agent_name, set_context, reset_context
from .spans import Span
from .tracer import get_exporter

log = logging.getLogger("cottonmouth.hooks")

MODEL_COSTS_PER_1K = {
    "claude-opus-4-6": {"input": 0.015, "output": 0.075},
    "claude-sonnet-4-6": {"input": 0.003, "output": 0.015},
    "claude-haiku-4-5": {"input": 0.0008, "output": 0.004},
    # Claude 3 family (Bedrock on-demand pricing).
    "claude-3-5-sonnet": {"input": 0.003, "output": 0.015},
    "claude-3-5-haiku": {"input": 0.0008, "output": 0.004},
    "claude-3-opus": {"input": 0.015, "output": 0.075},
    "claude-3-sonnet": {"input": 0.003, "output": 0.015},
    "claude-3-haiku": {"input": 0.00025, "output": 0.00125},
    "gpt-4o": {"input": 0.0025, "output": 0.01},
    "gpt-4o-mini": {"input": 0.00015, "output": 0.0006},
    "gpt-4.1": {"input": 0.002, "output": 0.008},
    "gpt-4.1-mini": {"input": 0.0004, "output": 0.0016},
    "gpt-4.1-nano": {"input": 0.0001, "output": 0.0004},
}

_patched: set[str] = set()


def _estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    for key, costs in MODEL_COSTS_PER_1K.items():
        if key in model.lower():
            return (
                (input_tokens / 1000) * costs["input"]
                + (output_tokens / 1000) * costs["output"]
            )
    return 0.0


def _extract_model_name(model_str: str) -> str:
    parts = model_str.rsplit("/", 1)
    return parts[-1] if parts else model_str


def patch_anthropic() -> None:
    if "anthropic" in _patched:
        return
    try:
        import anthropic
    except ImportError:
        return

    _patched.add("anthropic")
    _original_create = anthropic.resources.messages.Messages.create
    _original_async_create = anthropic.resources.messages.AsyncMessages.create

    @functools.wraps(_original_create)
    def patched_create(self, *args, **kwargs):
        trace_id = get_trace_id()
        if not trace_id:
            return _original_create(self, *args, **kwargs)

        model = kwargs.get("model", args[0] if args else "unknown")
        span = Span(
            trace_id=trace_id,
            parent_span_id=get_span_id() or "",
            agent_name=get_agent_name() or "",
            span_type="llm_call",
            name=_extract_model_name(str(model)),
            model=str(model),
            input_data={"system": str(kwargs.get("system", ""))[:200], "message_count": len(kwargs.get("messages", []))},
        )
        tokens = set_context(trace_id, span.span_id, span.agent_name)
        try:
            result = _original_create(self, *args, **kwargs)
            span.input_tokens = getattr(result.usage, "input_tokens", 0)
            span.output_tokens = getattr(result.usage, "output_tokens", 0)
            span.cost_usd = _estimate_cost(span.model, span.input_tokens, span.output_tokens)
            span.output_data = {"stop_reason": getattr(result, "stop_reason", ""), "content_blocks": len(getattr(result, "content", []))}
            span.finish()
            return result
        except Exception as e:
            span.finish(status="failed", error=str(e))
            raise
        finally:
            get_exporter().export(span)
            reset_context(tokens)

    @functools.wraps(_original_async_create)
    async def patched_async_create(self, *args, **kwargs):
        trace_id = get_trace_id()
        if not trace_id:
            return await _original_async_create(self, *args, **kwargs)

        model = kwargs.get("model", args[0] if args else "unknown")
        span = Span(
            trace_id=trace_id,
            parent_span_id=get_span_id() or "",
            agent_name=get_agent_name() or "",
            span_type="llm_call",
            name=_extract_model_name(str(model)),
            model=str(model),
            input_data={"system": str(kwargs.get("system", ""))[:200], "message_count": len(kwargs.get("messages", []))},
        )
        tokens = set_context(trace_id, span.span_id, span.agent_name)
        try:
            result = await _original_async_create(self, *args, **kwargs)
            span.input_tokens = getattr(result.usage, "input_tokens", 0)
            span.output_tokens = getattr(result.usage, "output_tokens", 0)
            span.cost_usd = _estimate_cost(span.model, span.input_tokens, span.output_tokens)
            span.output_data = {"stop_reason": getattr(result, "stop_reason", ""), "content_blocks": len(getattr(result, "content", []))}
            span.finish()
            return result
        except Exception as e:
            span.finish(status="failed", error=str(e))
            raise
        finally:
            get_exporter().export(span)
            reset_context(tokens)

    anthropic.resources.messages.Messages.create = patched_create
    anthropic.resources.messages.AsyncMessages.create = patched_async_create
    log.info("Patched Anthropic SDK for tracing")


def patch_openai() -> None:
    if "openai" in _patched:
        return
    try:
        import openai
    except ImportError:
        return

    _patched.add("openai")
    _original_create = openai.resources.chat.completions.Completions.create

    @functools.wraps(_original_create)
    def patched_create(self, *args, **kwargs):
        trace_id = get_trace_id()
        if not trace_id:
            return _original_create(self, *args, **kwargs)

        model = kwargs.get("model", "unknown")
        span = Span(
            trace_id=trace_id,
            parent_span_id=get_span_id() or "",
            agent_name=get_agent_name() or "",
            span_type="llm_call",
            name=_extract_model_name(str(model)),
            model=str(model),
            temperature=float(kwargs.get("temperature", 0)),
            input_data={"message_count": len(kwargs.get("messages", []))},
        )
        tokens = set_context(trace_id, span.span_id, span.agent_name)
        try:
            result = _original_create(self, *args, **kwargs)
            usage = getattr(result, "usage", None)
            if usage:
                span.input_tokens = getattr(usage, "prompt_tokens", 0)
                span.output_tokens = getattr(usage, "completion_tokens", 0)
                span.cost_usd = _estimate_cost(span.model, span.input_tokens, span.output_tokens)
            span.output_data = {"finish_reason": getattr(result.choices[0], "finish_reason", "") if result.choices else ""}
            span.finish()
            return result
        except Exception as e:
            span.finish(status="failed", error=str(e))
            raise
        finally:
            get_exporter().export(span)
            reset_context(tokens)

    openai.resources.chat.completions.Completions.create = patched_create
    log.info("Patched OpenAI SDK for tracing")


_BEDROCK_OPS = ("Converse", "ConverseStream", "InvokeModel", "InvokeModelWithResponseStream")


def patch_bedrock() -> None:
    """Auto-trace Amazon Bedrock runtime calls.

    Patches botocore's single API chokepoint, ``BaseClient._make_api_call``,
    which is stable across botocore versions. Only Bedrock runtime
    Converse/InvokeModel operations made within an active trace context are
    wrapped; everything else passes straight through.
    """
    if "bedrock" in _patched:
        return
    try:
        import botocore.client
    except ImportError:
        return

    _patched.add("bedrock")
    _original_make_api_call = botocore.client.BaseClient._make_api_call

    @functools.wraps(_original_make_api_call)
    def patched_make_api_call(self, operation_name, api_params):
        trace_id = get_trace_id()
        service_model = getattr(getattr(self, "meta", None), "service_model", None)
        service_name = getattr(service_model, "service_name", "")
        if (
            not trace_id
            or service_name != "bedrock-runtime"
            or operation_name not in _BEDROCK_OPS
        ):
            return _original_make_api_call(self, operation_name, api_params)

        model = api_params.get("modelId", "unknown")
        span = Span(
            trace_id=trace_id,
            parent_span_id=get_span_id() or "",
            agent_name=get_agent_name() or "",
            span_type="llm_call",
            name=_extract_model_name(str(model)),
            model=str(model),
        )
        tokens = set_context(trace_id, span.span_id, span.agent_name)
        try:
            result = _original_make_api_call(self, operation_name, api_params)
            usage = result.get("usage") or {}
            in_tok = usage.get("inputTokens", 0)
            out_tok = usage.get("outputTokens", 0)
            if not in_tok and not out_tok:
                # InvokeModel reports token counts via response headers.
                headers = result.get("ResponseMetadata", {}).get("HTTPHeaders", {})
                in_tok = int(headers.get("x-amzn-bedrock-input-token-count", 0) or 0)
                out_tok = int(headers.get("x-amzn-bedrock-output-token-count", 0) or 0)
            span.input_tokens = in_tok
            span.output_tokens = out_tok
            span.cost_usd = _estimate_cost(span.model, in_tok, out_tok)
            span.output_data = {"stop_reason": result.get("stopReason", "")}
            span.finish()
            return result
        except Exception as e:
            span.finish(status="failed", error=str(e))
            raise
        finally:
            get_exporter().export(span)
            reset_context(tokens)

    botocore.client.BaseClient._make_api_call = patched_make_api_call
    log.info("Patched Bedrock (botocore) for tracing")


def patch_all() -> None:
    patch_anthropic()
    patch_openai()
    patch_bedrock()
