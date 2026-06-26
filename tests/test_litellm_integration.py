"""Tests for the LiteLLM ⇄ CottonMouth integration.

The mapping/correlation/dedupe tests run without litellm installed (the logger
falls back to a stub base class). The Router-async smoke test is skipped unless
litellm is available.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from cottonmouth.context import reset_context, set_context
from cottonmouth.integrations.gateway import classify_gateway_denial
from cottonmouth.integrations.litellm import (
    CottonmouthLogger,
    with_cottonmouth,
)
from cottonmouth.spans import Span
from cottonmouth.tracer import set_exporter


def _perm_checks(capture):
    return [s for s in capture.spans if s.span_type == "permission_check"]


class CaptureExporter:
    def __init__(self) -> None:
        self.spans: list[Span] = []

    def export(self, span: Span) -> None:
        self.spans.append(span)

    def flush(self) -> None:
        pass

    def llm_calls(self) -> list[Span]:
        return [s for s in self.spans if s.span_type == "llm_call"]


@pytest.fixture
def capture():
    exp = CaptureExporter()
    set_exporter(exp)
    return exp


def _kwargs(**over):
    base = {
        "model": "bedrock/anthropic.claude-3-haiku-20240307-v1:0",
        "messages": [{"role": "user", "content": "hi"}],
        "litellm_call_id": "call-1",
        "response_cost": 0.00123,
        "litellm_params": {"metadata": {}, "custom_llm_provider": "bedrock"},
        "standard_logging_object": {"prompt_tokens": 11, "completion_tokens": 22},
        "call_type": "completion",
    }
    base.update(over)
    return base


class _Resp:
    def __init__(self, prompt=11, completion=22):
        self.usage = {"prompt_tokens": prompt, "completion_tokens": completion}


def _times(ms=150):
    start = datetime(2026, 1, 1, 0, 0, 0)
    return start, start + timedelta(milliseconds=ms)


def test_success_span_mapping(capture):
    logger = CottonmouthLogger()
    start, end = _times(150)
    logger.log_success_event(_kwargs(), _Resp(), start, end)

    assert len(capture.llm_calls()) == 1
    s = capture.llm_calls()[0]
    assert s.span_type == "llm_call"
    assert s.status == "completed"
    assert s.model.endswith("claude-3-haiku-20240307-v1:0")
    assert s.input_tokens == 11 and s.output_tokens == 22
    assert s.cost_usd == pytest.approx(0.00123)
    assert s.duration_ms == 150
    assert s.metadata["source"] == "litellm"
    assert s.metadata["provider"] == "bedrock"


def test_cost_prefers_litellm_value(capture):
    logger = CottonmouthLogger()
    start, end = _times()
    logger.log_success_event(_kwargs(response_cost=0.05), _Resp(), start, end)
    assert capture.llm_calls()[0].cost_usd == pytest.approx(0.05)


def test_correlation_via_metadata(capture):
    logger = CottonmouthLogger()
    start, end = _times()
    kwargs = _kwargs(
        litellm_params={
            "metadata": {
                "cottonmouth": {
                    "trace_id": "trace-xyz",
                    "parent_span_id": "span-root",
                    "agent_name": "support-bot",
                }
            }
        }
    )
    logger.log_success_event(kwargs, _Resp(), start, end)
    s = capture.llm_calls()[0]
    assert s.trace_id == "trace-xyz"
    assert s.parent_span_id == "span-root"
    assert s.agent_name == "support-bot"
    assert s.metadata["correlated"] is True


def test_correlation_via_contextvars(capture):
    logger = CottonmouthLogger()
    start, end = _times()
    tokens = set_context("ctx-trace", "ctx-span", "ctx-agent")
    try:
        logger.log_success_event(_kwargs(), _Resp(), start, end)
    finally:
        reset_context(tokens)
    s = capture.llm_calls()[0]
    assert s.trace_id == "ctx-trace"
    assert s.parent_span_id == "ctx-span"
    assert s.agent_name == "ctx-agent"
    assert s.metadata["correlated"] is True


def test_standalone_trace_uses_identity(capture):
    logger = CottonmouthLogger()
    start, end = _times()
    kwargs = _kwargs(
        litellm_params={"metadata": {"user_api_key_alias": "team-payments-key"}}
    )
    logger.log_success_event(kwargs, _Resp(), start, end)
    s = capture.llm_calls()[0]
    assert s.metadata["correlated"] is False
    assert s.trace_id  # a fresh id was minted
    assert s.parent_span_id == ""
    assert s.agent_name == "team-payments-key"


def test_failure_event(capture):
    logger = CottonmouthLogger()
    start, end = _times()
    kwargs = _kwargs(exception="RateLimitError: slow down")
    logger.log_failure_event(kwargs, None, start, end)
    s = capture.llm_calls()[0]
    assert s.status == "failed"
    assert "RateLimitError" in s.error


def test_dedupe_same_call_id(capture):
    logger = CottonmouthLogger()
    start, end = _times()
    logger.log_success_event(_kwargs(litellm_call_id="dup"), _Resp(), start, end)
    logger.log_success_event(_kwargs(litellm_call_id="dup"), _Resp(), start, end)
    assert len(capture.llm_calls()) == 1


def test_with_cottonmouth_reads_context():
    tokens = set_context("t1", "s1", "a1")
    try:
        md = with_cottonmouth()
    finally:
        reset_context(tokens)
    ctx = md["metadata"]["cottonmouth"]
    assert ctx["trace_id"] == "t1"
    assert ctx["parent_span_id"] == "s1"
    assert ctx["agent_name"] == "a1"


def test_exporter_error_never_propagates():
    class Boom:
        def export(self, span):
            raise RuntimeError("exporter down")

        def flush(self):
            pass

    set_exporter(Boom())
    logger = CottonmouthLogger()
    start, end = _times()
    # Must not raise — the LiteLLM request path is never broken by logging.
    logger.log_success_event(_kwargs(), _Resp(), start, end)


def test_async_hooks(capture):
    import asyncio

    logger = CottonmouthLogger()
    start, end = _times()
    asyncio.run(
        logger.async_log_success_event(_kwargs(litellm_call_id="async-1"), _Resp(), start, end)
    )
    assert len(capture.llm_calls()) == 1
    assert capture.llm_calls()[0].status == "completed"


def test_classify_gateway_denial():
    assert classify_gateway_denial("BudgetExceededError: max_budget reached") == (True, "budget")
    assert classify_gateway_denial("key not allowed to access model gpt-4o")[1] == "model-access"
    assert classify_gateway_denial("RateLimitError: rpm limit exceeded")[1] == "rate-limit"
    assert classify_gateway_denial("Invalid proxy server token")[1] == "auth"
    assert classify_gateway_denial("Request flagged by guardrail")[1] == "guardrail"
    # Plain provider/transport failures are NOT governance denials.
    assert classify_gateway_denial("Connection timeout to provider") == (False, "")
    assert classify_gateway_denial("") == (False, "")


def test_gateway_decision_allow_on_success(capture):
    """A successful call records the gateway's ALLOW verdict (it permitted it)."""
    logger = CottonmouthLogger()
    start, end = _times()
    logger.log_success_event(_kwargs(), _Resp(), start, end)
    pcs = _perm_checks(capture)
    assert len(pcs) == 1
    assert pcs[0].permission_result == "allow"
    assert pcs[0].permission_policy == "litellm-gateway"
    assert pcs[0].metadata["enforced_by"] == "litellm"


def test_gateway_decision_deny_on_budget(capture):
    logger = CottonmouthLogger()
    start, end = _times()
    kwargs = _kwargs(exception="BudgetExceededError: team max_budget of $5 reached")
    logger.log_failure_event(kwargs, None, start, end)
    pcs = _perm_checks(capture)
    assert len(pcs) == 1
    assert pcs[0].permission_result == "deny"
    assert pcs[0].permission_policy == "litellm-gateway:budget"
    assert "BudgetExceeded" in pcs[0].error


def test_gateway_decision_deny_on_model_access(capture):
    logger = CottonmouthLogger()
    start, end = _times()
    kwargs = _kwargs(exception="key not allowed to access model openai/gpt-4o")
    logger.log_failure_event(kwargs, None, start, end)
    pc = _perm_checks(capture)[0]
    assert pc.permission_result == "deny"
    assert pc.permission_policy == "litellm-gateway:model-access"


def test_no_permission_check_on_transport_failure(capture):
    """A plain provider failure is recorded as a failed llm_call only — CottonMouth
    does not invent a governance verdict it didn't observe."""
    logger = CottonmouthLogger()
    start, end = _times()
    kwargs = _kwargs(exception="APIConnectionError: connection reset by peer")
    logger.log_failure_event(kwargs, None, start, end)
    assert len(capture.llm_calls()) == 1
    assert capture.llm_calls()[0].status == "failed"
    assert _perm_checks(capture) == []


def test_origin_capture(capture):
    logger = CottonmouthLogger()
    start, end = _times()
    kwargs = _kwargs(
        litellm_params={"metadata": {"cottonmouth": {
            "trace_id": "t", "agent_name": "a", "caller": "litellm_agent.py:71:run"}}}
    )
    logger.log_success_event(kwargs, _Resp(), start, end)
    origin = capture.llm_calls()[0].metadata["origin"]
    assert origin["caller"] == "litellm_agent.py:71:run"
    assert origin["agent"] == "a"
    assert "host" in origin and "pid" in origin


def test_router_async_smoke(capture):
    """Guards LiteLLM #8842: Router async traffic must still fire our callback."""
    import asyncio

    litellm = pytest.importorskip("litellm", reason="litellm not installed")
    from litellm import Router

    litellm.callbacks = [CottonmouthLogger()]
    router = Router(
        model_list=[
            {
                "model_name": "mock",
                "litellm_params": {"model": "openai/mock", "mock_response": "hello"},
            }
        ]
    )

    async def _go():
        await router.acompletion(
            model="mock", messages=[{"role": "user", "content": "hi"}]
        )
        # LiteLLM runs success logging as a scheduled task; let it settle.
        await asyncio.sleep(1.0)

    asyncio.run(_go())
    # At least one llm_call span produced from async router traffic.
    assert any(s.span_type == "llm_call" for s in capture.spans)
