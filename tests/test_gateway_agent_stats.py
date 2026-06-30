"""Gateway-agent rollup including MCP tool calls (CORE-10641).

Verifies that an agent seen ONLY through the LiteLLM gateway is rolled up from
both its model calls (llm_call) and its MCP tool calls (tool_call), and that an
MCP-only agent (no model calls) still appears.
"""
from __future__ import annotations

from src.common.agent_stats import (
    compute_gateway_agent_detail,
    compute_gateway_agent_stats,
)


def _llm(agent, span_id, cost=0.001, in_tok=10, out_tok=20, model="claude-3-haiku"):
    return {
        "span_type": "llm_call", "agent_name": agent, "span_id": span_id,
        "trace_id": "t-" + span_id, "model": model, "cost_usd": cost,
        "input_tokens": in_tok, "output_tokens": out_tok, "duration_ms": 100,
        "status": "completed", "start_time": "2026-06-29T10:00:00",
        "metadata": {"source": "litellm"},
    }


def _tool(agent, span_id, tool="github/get_file_contents", server="github",
          status="completed", started="2026-06-29T10:01:00"):
    return {
        "span_type": "tool_call", "agent_name": agent, "span_id": span_id,
        "trace_id": "t-" + span_id, "tool_name": tool, "name": tool,
        "tool_input": {"owner": "haydenconda", "repo": "CottonMouth", "path": "README.md"},
        "cost_usd": 0.0, "duration_ms": 50, "status": status,
        "start_time": started, "metadata": {"source": "litellm-mcp", "mcp_server": server},
    }


def _deny(agent, parent, tool="github/delete_file"):
    return {
        "span_type": "permission_check", "agent_name": agent,
        "span_id": "pc-" + parent, "parent_span_id": parent,
        "permission_result": "deny", "permission_policy": "litellm-mcp:mcp-access",
        "metadata": {"source": "litellm-mcp"},
    }


def test_gateway_rollup_includes_tool_calls():
    spans = [
        _llm("devils-council", "l1"),
        _tool("devils-council", "tc1"),
        _tool("devils-council", "tc2", tool="github/list_pull_requests"),
    ]
    stats = compute_gateway_agent_stats(spans)
    assert len(stats) == 1
    g = stats[0]
    assert g["agent_name"] == "devils-council"
    assert g["call_count"] == 1
    assert g["tool_call_count"] == 2
    assert "github/get_file_contents" in g["tools"]
    assert "github/list_pull_requests" in g["tools"]


def test_mcp_only_agent_appears():
    """An agent that ONLY calls MCP tools (no model calls) must still roll up."""
    spans = [_tool("devils-council", "tc1"), _tool("devils-council", "tc2")]
    stats = compute_gateway_agent_stats(spans)
    assert len(stats) == 1
    g = stats[0]
    assert g["call_count"] == 0
    assert g["tool_call_count"] == 2
    assert g["avg_duration_ms"] == 50.0  # averaged over tool calls, no div-by-zero


def test_agent_with_own_run_is_excluded_from_gateway_rollup():
    spans = [
        {"span_type": "agent_run", "agent_name": "ops-assistant", "status": "completed"},
        _tool("ops-assistant", "tc1"),  # instrumented agent -> not a gateway-only agent
    ]
    assert compute_gateway_agent_stats(spans) == []


def test_gateway_detail_lists_tool_calls_and_verdicts():
    spans = [
        _tool("devils-council", "tc1"),
        {**_tool("devils-council", "tc2", tool="github/delete_file",
                 status="failed", started="2026-06-29T10:02:00"),
         "error": "tool not permitted for this key"},
        _deny("devils-council", "tc2"),
    ]
    detail = compute_gateway_agent_detail("devils-council", spans)
    assert detail is not None
    assert detail["tool_call_count"] == 2
    assert detail["denied_count"] == 1
    tcs = detail["tool_calls"]
    assert len(tcs) == 2
    # newest first
    assert tcs[0]["tool_name"] == "github/delete_file"
    assert tcs[0]["verdict"] == "deny"
    assert tcs[1]["verdict"] == "allow"


def test_unknown_agent_detail_returns_none():
    assert compute_gateway_agent_detail("nope", [_tool("devils-council", "tc1")]) is None
