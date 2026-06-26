"""LiteLLM-routed agent, traced by CottonMouth.

The agent emits the agent_run / decision / tool spans; the LLM call goes through
the shared in-cluster LiteLLM gateway (CORE-10625), which runs the CottonMouth
CustomLogger and emits the ``llm_call`` span nested under this run. The agent
holds NO provider credentials — the gateway does.

Two modes:

* **Gateway** (``LITELLM_BASE_URL`` set): calls ``http://litellm:4000`` with a
  virtual/master key. The gateway logs the llm_call span; correlation is threaded
  via ``with_cottonmouth()`` metadata so it nests under this run. No agent-side
  callback (avoids a duplicate span).
* **Direct** (no ``LITELLM_BASE_URL``): the agent's own LiteLLM SDK calls Bedrock
  and the agent-side logger emits the span. Useful for local dev.

Env vars:
    COTTONMOUTH_ENDPOINT   Collector base URL (required), e.g. http://cottonmouth-backend:8150
    COTTONMOUTH_API_KEY    Optional bearer token (must match the backend's).
    LITELLM_BASE_URL       Gateway URL, e.g. http://litellm:4000 (enables gateway mode).
    LITELLM_API_KEY        Gateway virtual/master key (gateway mode).
    LITELLM_MODEL          Model name. Gateway: a model_name from config (default claude-3-haiku);
                           direct: a full LiteLLM model string.
    AWS_REGION             AWS region for direct Bedrock routing (default us-east-1).
    AGENT_INTERVAL         Seconds between runs (default 25)
    AGENT_ONCE             If truthy, run once and exit.
"""
from __future__ import annotations

import os
import random
import signal
import sys
import time

import litellm

import cottonmouth
from cottonmouth.context import reset_context, set_context
from cottonmouth.integrations.litellm import enable, with_cottonmouth
from cottonmouth.spans import Span
from cottonmouth.tracer import Tracer, get_exporter

GATEWAY_URL = os.environ.get("LITELLM_BASE_URL", "")
GATEWAY_KEY = os.environ.get("LITELLM_API_KEY", "")
REGION = os.environ.get("AWS_REGION", "us-east-1")
MODEL = os.environ.get(
    "LITELLM_MODEL",
    "claude-3-haiku" if GATEWAY_URL else "bedrock/anthropic.claude-3-haiku-20240307-v1:0",
)

AGENT_NAME = "gateway-assistant"
AGENT_VERSION = "1.0.0"

QUESTIONS = [
    "Summarize the trade-offs of optimistic vs pessimistic locking.",
    "When would you reach for a CRDT instead of a lock?",
    "Explain idempotency keys for a payments API.",
    "What is the thundering-herd problem and how do you avoid it?",
    "How does a write-ahead log enable crash recovery?",
    "Why is exactly-once delivery hard in distributed systems?",
]

_tracer = Tracer(agent_name=AGENT_NAME, agent_version=AGENT_VERSION)
_running = True


def _stop(*_: object) -> None:
    global _running
    _running = False


_oai_client = None


def _gateway_client():
    """Lazy OpenAI client pointed at the in-cluster gateway. In gateway mode the
    agent is a plain OpenAI-compatible caller; the GATEWAY runs LiteLLM and emits
    the llm_call span — so the agent needs no litellm on the client side (which
    sidesteps SDK/proxy version-parsing issues)."""
    global _oai_client
    if _oai_client is None:
        from openai import OpenAI
        _oai_client = OpenAI(base_url=GATEWAY_URL.rstrip("/") + "/v1", api_key=GATEWAY_KEY)
    return _oai_client


def _complete(prompt: str, max_tokens: int) -> str:
    """One completion — emitted as an llm_call span (by the gateway in gateway
    mode, or the agent-side logger in direct mode).

    ``with_cottonmouth()`` threads the active trace context into request metadata
    so the span nests under the current agent_run across the hop to the gateway.
    """
    messages = [{"role": "user", "content": prompt}]
    if GATEWAY_URL:
        resp = _gateway_client().chat.completions.create(
            model=MODEL,
            messages=messages,
            max_tokens=max_tokens,
            temperature=0.5,
            # Forwarded to the proxy body; the gateway logger reads metadata.cottonmouth.
            extra_body=with_cottonmouth(agent_name=AGENT_NAME),
        )
        return resp.choices[0].message.content
    resp = litellm.completion(
        model=MODEL,
        messages=messages,
        max_tokens=max_tokens,
        temperature=0.5,
        aws_region_name=REGION,
        **with_cottonmouth(agent_name=AGENT_NAME),
    )
    return resp["choices"][0]["message"]["content"]


def run(question: str) -> dict:
    """One fully-traced agent run: decide -> retrieve -> answer via the gateway."""
    root = _tracer.start_trace(name=f"{AGENT_NAME}_run", metadata={"question": question})
    tokens = set_context(root.trace_id, root.span_id, AGENT_NAME)
    try:
        # Decision span (the "why"): pick a strategy.
        strategy = "decompose" if len(question) > 60 else "direct"
        _tracer.log_decision(
            name="answer_strategy",
            reasoning="Long questions are decomposed before answering.",
            options=["direct", "decompose"],
            chosen=strategy,
            decision_type="branch",
        )

        if strategy == "decompose":
            plan = _complete(f"List 2 sub-questions, one per line:\n{question}", 120)
            sub_qs = [ln.strip("-* ").strip() for ln in plan.splitlines() if ln.strip()]
        else:
            sub_qs = [question]

        # Tool span: a local "retrieval" step.
        tool = Span(
            trace_id=root.trace_id,
            parent_span_id=root.span_id,
            agent_name=AGENT_NAME,
            agent_version=AGENT_VERSION,
            span_type="tool_call",
            name="knowledge_lookup",
            tool_name="knowledge_lookup",
            tool_input={"sub_questions": sub_qs[:5]},
        )
        tool.tool_output = {"hits": len(sub_qs)}
        tool.finish()
        get_exporter().export(tool)

        answer = _complete(f"Answer in 3-4 sentences:\n{question}", 250)
        root.finish(status="completed")
        return {"status": "completed", "answer": answer}
    except Exception as e:
        root.finish(status="failed", error=str(e))
        return {"status": "failed", "answer": str(e)}
    finally:
        _tracer.emit(root)
        reset_context(tokens)


def main() -> int:
    endpoint = os.environ.get("COTTONMOUTH_ENDPOINT", "")
    if not endpoint:
        print("COTTONMOUTH_ENDPOINT is required (e.g. http://cottonmouth-backend:8150)", file=sys.stderr)
        return 2

    # auto_instrument=False: LiteLLM owns the provider call; the logger is the
    # single source of the llm_call span (avoids double-counting).
    cottonmouth.configure(export="http", endpoint=endpoint, auto_instrument=False)
    if GATEWAY_URL:
        # The gateway runs the CottonmouthLogger and emits the llm_call span;
        # the agent only emits agent_run/decision/tool spans. No agent-side
        # callback => no duplicate span.
        mode = f"gateway {GATEWAY_URL} (model={MODEL})"
    else:
        # Direct Bedrock via the SDK: the agent-side logger emits the span.
        enable()
        mode = f"direct (model={MODEL})"
    print(f"[litellm-agent] {AGENT_NAME} -> {endpoint}/api/spans via {mode}")

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    once = os.environ.get("AGENT_ONCE", "").lower() in ("1", "true", "yes")
    interval = float(os.environ.get("AGENT_INTERVAL", "25"))

    while _running:
        question = random.choice(QUESTIONS)
        summary = run(question)
        preview = summary["answer"][:110].replace("\n", " ")
        print(f"[litellm-agent] {summary['status']:<9} Q: {question[:44]}  A: {preview}...")
        if once:
            break
        slept = 0.0
        while _running and slept < interval:
            time.sleep(0.25)
            slept += 0.25

    get_exporter().flush()
    print("[litellm-agent] stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
