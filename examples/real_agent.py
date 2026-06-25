"""Real instrumented agent — performs live AWS Bedrock inference, traced by CottonMouth.

Unlike examples/sample_agent.py (which fabricates spans), this agent runs an
actual multi-step LLM workflow against Amazon Bedrock. The CottonMouth SDK
auto-instruments every Bedrock Converse call, so each model invocation becomes
an ``llm_call`` span with real token counts and cost, nested under the
``agent_run``. A local "retrieval" step is traced as a ``tool_call``.

Auth:
    In EKS the pod assumes an IAM role via IRSA (service account annotated with
    a role that allows ``bedrock:InvokeModel``). Locally it uses your default
    AWS credentials / profile.

Env vars:
    COTTONMOUTH_ENDPOINT      Collector base URL (required), e.g. http://cottonmouth-backend:8150
    COTTONMOUTH_API_KEY       Optional bearer token (must match the backend's).
    BEDROCK_MODEL_ID   Bedrock model (default anthropic.claude-3-haiku-20240307-v1:0)
    AWS_REGION         AWS region (default us-east-1)
    AGENT_INTERVAL     Seconds between runs (default 20)
    AGENT_ONCE         If truthy, run once and exit.
"""
from __future__ import annotations

import os
import random
import signal
import sys
import time

import boto3

import cottonmouth
from cottonmouth.context import reset_context, set_context
from cottonmouth.spans import Span
from cottonmouth.tracer import Tracer, get_exporter

REGION = os.environ.get("AWS_REGION", "us-east-1")
MODEL_ID = os.environ.get(
    "BEDROCK_MODEL_ID", "anthropic.claude-3-haiku-20240307-v1:0"
)

AGENT_NAME = "research-assistant"
AGENT_VERSION = "1.0.0"

QUESTIONS = [
    "What is the difference between a process and a thread?",
    "Explain eventual consistency in distributed systems.",
    "Why use a message queue instead of direct service calls?",
    "What are the trade-offs of microservices vs a monolith?",
    "How does a bloom filter work and when is it useful?",
    "What is backpressure in streaming systems?",
    "When should you choose a columnar database?",
    "Explain the CAP theorem in plain terms.",
    "What problem does a service mesh solve?",
    "How does consistent hashing help with sharding?",
]

_bedrock = boto3.client("bedrock-runtime", region_name=REGION)
_tracer = Tracer(agent_name=AGENT_NAME, agent_version=AGENT_VERSION)

_running = True


def _stop(*_: object) -> None:
    global _running
    _running = False


def _converse(prompt: str, max_tokens: int) -> tuple[str, float, int, int]:
    """Make one real Bedrock Converse call (auto-traced as an llm_call span).

    Returns (text, cost_usd, input_tokens, output_tokens) so the caller can
    tally totals onto the root agent_run span.
    """
    from cottonmouth.llm_hooks import _estimate_cost

    resp = _bedrock.converse(
        modelId=MODEL_ID,
        messages=[{"role": "user", "content": [{"text": prompt}]}],
        inferenceConfig={"maxTokens": max_tokens, "temperature": 0.5},
    )
    usage = resp.get("usage", {})
    in_tok = usage.get("inputTokens", 0)
    out_tok = usage.get("outputTokens", 0)
    cost = _estimate_cost(MODEL_ID, in_tok, out_tok)
    text = resp["output"]["message"]["content"][0]["text"]
    return text, cost, in_tok, out_tok


def run_research(question: str) -> dict:
    """One agent run: decompose -> retrieve -> synthesize, fully traced."""
    root = _tracer.start_trace(name=f"{AGENT_NAME}_run", metadata={"question": question})
    tokens = set_context(root.trace_id, root.span_id, AGENT_NAME)

    total_cost = 0.0
    total_in = 0
    total_out = 0
    try:
        # Step 1 (real LLM): break the question into sub-questions.
        plan, cost, ti, to = _converse(
            f"Break this question into 2 concise sub-questions, one per line:\n{question}",
            max_tokens=150,
        )
        total_cost += cost
        total_in += ti
        total_out += to

        # Step 2 (tool): a local "retrieval" step, traced as a tool_call.
        sub_qs = [ln.strip("-* ").strip() for ln in plan.splitlines() if ln.strip()]
        tool_span = Span(
            trace_id=root.trace_id,
            parent_span_id=root.span_id,
            agent_name=AGENT_NAME,
            agent_version=AGENT_VERSION,
            span_type="tool_call",
            name="knowledge_lookup",
            tool_name="knowledge_lookup",
            tool_input={"sub_questions": sub_qs[:5]},
        )
        tool_span.tool_output = {"hits": len(sub_qs)}
        tool_span.finish()
        get_exporter().export(tool_span)

        # Step 3 (real LLM): synthesize the final answer.
        answer, cost, ti, to = _converse(
            f"Answer this clearly in 3-4 sentences:\n{question}",
            max_tokens=250,
        )
        total_cost += cost
        total_in += ti
        total_out += to

        root.cost_usd = round(total_cost, 6)
        root.input_tokens = total_in
        root.output_tokens = total_out
        root.finish(status="completed")
        return {"status": "completed", "cost": root.cost_usd, "answer": answer}
    except Exception as e:
        root.cost_usd = round(total_cost, 6)
        root.finish(status="failed", error=str(e))
        return {"status": "failed", "cost": root.cost_usd, "answer": str(e)}
    finally:
        _tracer.emit(root)
        reset_context(tokens)


def main() -> int:
    endpoint = os.environ.get("COTTONMOUTH_ENDPOINT", "")
    if not endpoint:
        print("COTTONMOUTH_ENDPOINT is required (e.g. http://cottonmouth-backend:8150)", file=sys.stderr)
        return 2

    # auto_instrument=True patches botocore so Bedrock calls are traced.
    cottonmouth.configure(export="http", endpoint=endpoint, auto_instrument=True)
    print(
        f"[real-agent] {AGENT_NAME} -> {endpoint}/api/spans "
        f"(model={MODEL_ID}, region={REGION})"
    )

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    once = os.environ.get("AGENT_ONCE", "").lower() in ("1", "true", "yes")
    interval = float(os.environ.get("AGENT_INTERVAL", "20"))

    while _running:
        question = random.choice(QUESTIONS)
        summary = run_research(question)
        preview = summary["answer"][:110].replace("\n", " ")
        print(
            f"[real-agent] {summary['status']:<9} ${summary['cost']:.5f}  "
            f"Q: {question[:48]}  A: {preview}..."
        )
        if once:
            break
        slept = 0.0
        while _running and slept < interval:
            time.sleep(0.25)
            slept += 0.25

    get_exporter().flush()
    print("[real-agent] stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
