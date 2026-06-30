"""Devil's Council — a gateway-routed, fully-observable code-review agent.

Unlike Cursor's built-in agent (a closed client whose model loop you can't see),
this is an agent you *control*, wired so CottonMouth captures all four pillars of
agent observability for the ``devils-council`` identity:

  1. What did it do?      -> tool_call spans  (GitHub fetch via the MCP gateway)
  2. Why did it do it?    -> decision spans   (which persona, what verdict, why)
  3. What did it cost?    -> llm_call spans   (per-persona model calls via the LLM gateway)
  4. What was it allowed? -> permission_check (repo allowlist + the gateway's own verdicts)

A "council" of reviewer personas (Security / Performance / Maintainability) each
critiques the target file; a chair synthesizes a final verdict. Every model call
goes through the LiteLLM **LLM gateway** and every tool call through the LiteLLM
**MCP gateway**, both authenticated with the devils-council virtual key — so the
gateway governs (budgets, access, allow/deny) and CottonMouth observes.

Run (with the gateway + backend port-forwarded locally):

    export COTTONMOUTH_ENDPOINT=http://localhost:8150
    export LITELLM_BASE=http://localhost:4000
    export DEVILS_COUNCIL_KEY=sk-...            # the devils-council virtual key
    python examples/devils_council.py --owner haydenconda --repo cottonmouth --path README.md

Env vars:
    COTTONMOUTH_ENDPOINT   Collector base URL (required), e.g. http://localhost:8150
    LITELLM_BASE           LiteLLM gateway base URL (default http://localhost:4000)
    DEVILS_COUNCIL_KEY     LiteLLM virtual key with alias 'devils-council' (required)
    DEVILS_COUNCIL_MODEL   Gateway model name (default claude-3-haiku)
    DEVILS_COUNCIL_OWNERS  Comma-separated repo-owner allowlist (default haydenconda)
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import urllib.error
import urllib.request

import cottonmouth
from cottonmouth.context import reset_context, set_context
from cottonmouth.tracer import Tracer, get_exporter

AGENT_NAME = "devils-council"
AGENT_VERSION = "1.0.0"

LITELLM_BASE = os.environ.get("LITELLM_BASE", "http://localhost:4000").rstrip("/")
LITELLM_KEY = os.environ.get("DEVILS_COUNCIL_KEY", "")
MODEL = os.environ.get("DEVILS_COUNCIL_MODEL", "claude-3-haiku")
ALLOWED_OWNERS = {
    o.strip().lower()
    for o in os.environ.get("DEVILS_COUNCIL_OWNERS", "haydenconda").split(",")
    if o.strip()
}

_tracer = Tracer(agent_name=AGENT_NAME, agent_version=AGENT_VERSION)

# The council. Each persona is a single reviewer with a narrow mandate so its
# critique (and the decision to consult it) reads as a distinct step.
PERSONAS = [
    {
        "name": "Security Devil",
        "mandate": "Scrutinize for injection, secret leakage, authz gaps, unsafe deserialization.",
        "system": "You are a ruthless application-security reviewer. Flag only real, "
                  "specific vulnerabilities with file-relevant detail. Be concise.",
    },
    {
        "name": "Performance Devil",
        "mandate": "Hunt for hot-path allocations, N+1 patterns, blocking I/O, unbounded work.",
        "system": "You are a performance reviewer. Call out concrete inefficiencies and "
                  "their impact. No style nits. Be concise.",
    },
    {
        "name": "Maintainability Devil",
        "mandate": "Assess clarity, coupling, testability, and naming.",
        "system": "You are a staff engineer reviewing for long-term maintainability. "
                  "Be specific and pragmatic. Be concise.",
    },
]


# ---------------------------------------------------------------------------
# Gateway clients. Model calls -> LLM gateway; tool calls -> MCP gateway.
# Both thread the current CottonMouth trace context so the gateway-emitted spans
# attribute to devils-council (LLM calls also nest under this run).
# ---------------------------------------------------------------------------
def _cm_metadata() -> dict:
    """Trace-context block the gateway's CottonMouth callback reads (key
    'cottonmouth') to nest the resulting llm_call under the active agent_run."""
    return {
        "cottonmouth": {
            "trace_id": cottonmouth.get_trace_id() or "",
            "parent_span_id": cottonmouth.get_span_id() or "",
            "agent_name": cottonmouth.get_agent_name() or AGENT_NAME,
        }
    }


def gateway_llm(system: str, user: str, *, max_tokens: int = 400,
                temperature: float = 0.4) -> tuple[str, int, int, float]:
    """One chat completion through the LiteLLM LLM gateway (OpenAI-compatible).

    Returns (text, prompt_tokens, completion_tokens, cost_usd). The gateway emits
    the llm_call span; we pass cottonmouth metadata so it nests under this run.
    """
    body = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "max_tokens": max_tokens,
        "temperature": temperature,
        "metadata": _cm_metadata(),
    }
    req = urllib.request.Request(
        f"{LITELLM_BASE}/v1/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {LITELLM_KEY}", "Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=90) as r:
        cost_hdr = r.headers.get("x-litellm-response-cost")
        data = json.loads(r.read())
    text = (data.get("choices") or [{}])[0].get("message", {}).get("content", "") or ""
    usage = data.get("usage", {}) or {}
    cost = float(cost_hdr) if cost_hdr else 0.0
    return text.strip(), int(usage.get("prompt_tokens", 0)), int(usage.get("completion_tokens", 0)), cost


async def _mcp_fetch(owner: str, repo: str, path: str) -> tuple[str, bool]:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client

    headers = {"x-litellm-api-key": f"Bearer {LITELLM_KEY}"}
    async with streamablehttp_client(f"{LITELLM_BASE}/mcp", headers=headers) as (read, write, *_):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = [t.name for t in tools.tools]
            target = next((n for n in names if n.endswith("get_file_contents")), None)
            if not target:
                raise RuntimeError(
                    "github get_file_contents not visible to this key "
                    "(is the MCP server allow_all_keys / is the key granted access?)"
                )
            result = await session.call_tool(target, {"owner": owner, "repo": repo, "path": path})
            text = ""
            for chunk in result.content:
                text = getattr(chunk, "text", "") or text
            return text, bool(result.isError)


def gateway_fetch_file(owner: str, repo: str, path: str) -> tuple[str, bool]:
    """Fetch a file via the LiteLLM MCP gateway (GitHub server). The gateway emits
    the tool_call span tagged devils-council."""
    return asyncio.run(_mcp_fetch(owner, repo, path))


# ---------------------------------------------------------------------------
# The review: one fully-traced agent_run.
# ---------------------------------------------------------------------------
def review(owner: str, repo: str, path: str) -> dict:
    target = f"{owner}/{repo}/{path}"
    root = _tracer.start_trace(name="devils-council review", metadata={"target": target})
    tokens = set_context(root.trace_id, root.span_id, AGENT_NAME)

    total_in = total_out = 0
    total_cost = 0.0
    try:
        # Pillar 4 — what it's allowed to do: repo-owner allowlist.
        allowed = owner.lower() in ALLOWED_OWNERS
        policy = f"devils-council may review repos owned by {sorted(ALLOWED_OWNERS)}"
        _tracer.log_permission(action="read_repo", resource=target, allowed=allowed, policy=policy)
        if not allowed:
            root.metadata.update({"verdict": "DENIED", "reason": "owner not on allowlist"})
            root.finish(status="completed")
            return {"trace_id": root.trace_id, "verdict": "DENIED",
                    "answer": f"Refused: {owner} is not an allowed repo owner."}

        # Pillar 2 — why: decide to pull the source via the gateway's MCP tool.
        _tracer.log_decision(
            name="fetch source",
            reasoning="Pull the committed file from GitHub through the MCP gateway so the "
                      "council reviews the real repo state, not a stale local copy.",
            options=["fetch via github MCP", "skip fetch"],
            chosen="fetch via github MCP",
            decision_type="tool_select",
        )
        # Pillar 1 — what it did: the GitHub fetch (gateway emits the tool_call span).
        try:
            code, tool_err = gateway_fetch_file(owner, repo, path)
        except Exception as exc:  # keep the run observable even if the tool is unreachable
            code, tool_err = f"(could not fetch {target}: {exc})", True
            _tracer.log_decision(
                name="degrade: tool unavailable",
                reasoning=f"GitHub MCP fetch failed ({exc}); reviewing with reduced context.",
                options=["abort", "review with partial context"],
                chosen="review with partial context",
                decision_type="recovery",
            )

        # The council deliberates — one model call per persona, via the LLM gateway.
        critiques: list[tuple[str, str]] = []
        for persona in PERSONAS:
            _tracer.log_decision(
                name=f"consult {persona['name']}",
                reasoning=persona["mandate"],
                options=[p["name"] for p in PERSONAS],
                chosen=persona["name"],
                decision_type="route",
            )
            text, pin, pout, pcost = gateway_llm(
                persona["system"],
                f"Review this file `{path}` from {owner}/{repo}:\n\n{code[:6000]}",
            )
            critiques.append((persona["name"], text))
            total_in += pin
            total_out += pout
            total_cost += pcost

        # Chair synthesizes a verdict — another gateway model call + a decision span.
        synth = "\n\n".join(f"## {name}\n{text}" for name, text in critiques)
        verdict_text, sin, sout, scost = gateway_llm(
            "You are the chair of a code-review council. Weigh the reviews and return a "
            "final verdict. Start your reply with exactly APPROVE or REQUEST_CHANGES, then "
            "give up to 3 bullet justifications.",
            f"Reviews of {target}:\n\n{synth}",
            max_tokens=300,
        )
        total_in += sin
        total_out += sout
        total_cost += scost
        chosen = "REQUEST_CHANGES" if "REQUEST_CHANGES" in verdict_text.upper() else "APPROVE"
        _tracer.log_decision(
            name="final verdict",
            reasoning=verdict_text[:800],
            options=["APPROVE", "REQUEST_CHANGES"],
            chosen=chosen,
            decision_type="verdict",
        )

        root.input_tokens = total_in
        root.output_tokens = total_out
        root.cost_usd = round(total_cost, 6)
        root.metadata.update({
            "verdict": chosen,
            "personas": len(PERSONAS),
            "tool_error": tool_err,
        })
        root.finish(status="completed")
        return {
            "trace_id": root.trace_id,
            "verdict": chosen,
            "cost": root.cost_usd,
            "tokens": {"in": total_in, "out": total_out},
            "answer": verdict_text,
        }
    except Exception as exc:
        root.finish(status="failed", error=str(exc))
        return {"trace_id": root.trace_id, "verdict": "ERROR", "answer": str(exc)}
    finally:
        _tracer.emit(root)
        reset_context(tokens)
        get_exporter().flush()


def main() -> int:
    parser = argparse.ArgumentParser(description="Devil's Council code-review agent")
    parser.add_argument("--owner", default="haydenconda")
    parser.add_argument("--repo", default="cottonmouth")
    parser.add_argument("--path", default="README.md")
    args = parser.parse_args()

    endpoint = os.environ.get("COTTONMOUTH_ENDPOINT", "")
    if not endpoint:
        print("COTTONMOUTH_ENDPOINT is required (e.g. http://localhost:8150)", file=sys.stderr)
        return 2
    if not LITELLM_KEY:
        print("DEVILS_COUNCIL_KEY is required (the devils-council virtual key)", file=sys.stderr)
        return 2

    # auto_instrument=False: we drive the gateway over HTTP ourselves and emit
    # spans explicitly; there's no local LLM SDK to patch.
    cottonmouth.configure(export="http", endpoint=endpoint, auto_instrument=False)
    print(f"[devils-council] -> {endpoint} via gateway {LITELLM_BASE} (model={MODEL})")

    summary = review(args.owner, args.repo, args.path)
    print(json.dumps(summary, indent=2))
    return 0 if summary.get("verdict") not in ("ERROR",) else 1


if __name__ == "__main__":
    raise SystemExit(main())
