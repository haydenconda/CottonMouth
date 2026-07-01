"""CottonMouth MCP server.

Exposes CottonMouth's read APIs as MCP tools so an agent or IDE (Cursor, Claude
Desktop, etc.) can ask about its own runs directly:

    "which of my agents is out of compliance?"
    "show the last failed trace for ops-assistant"
    "what did devils-council cost today?"

This is the inverse of the gateway capture (CORE-10641): instead of CottonMouth
observing agents, this lets agents observe CottonMouth. Read-first — the only
mutating tool submits an Investigate query.

Config (env):
    COTTONMOUTH_ENDPOINT   Backend base URL (default http://localhost:8150).
    COTTONMOUTH_API_KEY    Optional bearer token (sent on every request).
    COTTONMOUTH_MCP_TIMEOUT  Per-request timeout seconds (default 30).

Run:
    COTTONMOUTH_ENDPOINT=http://localhost:8150 cottonmouth-mcp
"""
from __future__ import annotations

import asyncio
import os
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP

ENDPOINT = os.environ.get("COTTONMOUTH_ENDPOINT", "http://localhost:8150").rstrip("/")
API_KEY = os.environ.get("COTTONMOUTH_API_KEY", "").strip()
TIMEOUT = float(os.environ.get("COTTONMOUTH_MCP_TIMEOUT", "30"))

mcp = FastMCP("cottonmouth")


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {API_KEY}"} if API_KEY else {}


async def _get(path: str, params: dict[str, Any] | None = None) -> Any:
    clean = {k: v for k, v in (params or {}).items() if v not in (None, "", 0) or v == 0}
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        r = await client.get(f"{ENDPOINT}{path}", params=clean, headers=_headers())
        r.raise_for_status()
        ctype = r.headers.get("content-type", "")
        return r.json() if "application/json" in ctype else r.text


async def _post(path: str, body: dict[str, Any]) -> Any:
    async with httpx.AsyncClient(timeout=TIMEOUT) as client:
        r = await client.post(f"{ENDPOINT}{path}", json=body, headers=_headers())
        r.raise_for_status()
        return r.json()


# ---------------------------------------------------------------------------
# Read tools
# ---------------------------------------------------------------------------
@mcp.tool()
async def health() -> Any:
    """Backend health/status snapshot."""
    return await _get("/api/health")


@mcp.tool()
async def list_traces(agent_name: str = "", status: str = "", limit: int = 20) -> Any:
    """List recent agent runs (traces). Optionally filter by agent_name and status
    ('completed'|'failed'). Returns run summaries with cost, duration, span count."""
    return await _get("/api/traces", {"agent_name": agent_name, "status": status, "limit": limit})


@mcp.tool()
async def get_trace(trace_id: str) -> Any:
    """Full trace for a trace_id: every span (agent_run, llm_call, tool_call,
    decision, permission_check) with cost, tokens, verdicts, and timing."""
    return await _get(f"/api/traces/{trace_id}")


@mcp.tool()
async def get_span(trace_id: str, span_id: str) -> Any:
    """A single span's raw detail within a trace."""
    return await _get(f"/api/traces/{trace_id}/spans/{span_id}")


@mcp.tool()
async def list_agents() -> Any:
    """All agents with rollup stats: run-instrumented agents plus gateway-only
    agents (LiteLLM virtual keys) with calls, tool calls, cost, and denials."""
    return await _get("/api/agents")


@mcp.tool()
async def get_agent(name: str) -> Any:
    """Stats for one agent. Gateway-only agents include their recent individual
    model/tool calls with allow/deny verdicts."""
    return await _get(f"/api/agents/{name}")


@mcp.tool()
async def search_traces(query: str, agent_name: str = "", status: str = "", limit: int = 20) -> Any:
    """Substring search across span fields (names, tools, models, errors, reasoning)."""
    return await _get("/api/search", {"q": query, "agent_name": agent_name, "status": status, "limit": limit})


@mcp.tool()
async def list_events(severity: str = "", source: str = "", limit: int = 50) -> Any:
    """Recent events (failures, permission denials, cost spikes, anomalies).
    Filter by severity ('info'|'warning'|'critical') and/or source."""
    return await _get("/api/events", {"severity": severity, "source": source, "limit": limit})


@mcp.tool()
async def get_compliance() -> Any:
    """Governance audit: overall compliance rate, per-agent compliance and mode
    (enforce vs monitor), blocked vs would-block denials, and recent denials.
    Answers 'what % of agents/actions are in vs out of compliance?'."""
    return await _get("/api/permissions")


@mcp.tool()
async def get_policies() -> Any:
    """The policy-as-data document the agents are bound by (rules, tools, gateway
    model access, and each agent's enforcement mode)."""
    return await _get("/api/policies")


@mcp.tool()
async def get_gateway() -> Any:
    """LiteLLM gateway reconcile: declared vs exposed vs observed model access per
    agent, with drift detection."""
    return await _get("/api/gateway")


@mcp.tool()
async def get_metrics() -> str:
    """Raw Prometheus-format metrics (runs, errors, cost, tokens, tool calls,
    compliance) — the same series scraped for Grafana/Alertmanager."""
    return await _get("/metrics")


# ---------------------------------------------------------------------------
# Investigate (the one mutating tool): asks CottonMouth's Bedrock agent.
# ---------------------------------------------------------------------------
@mcp.tool()
async def investigate(question: str, wait_seconds: int = 60) -> Any:
    """Ask CottonMouth's Investigate agent a natural-language question about the
    observed data and wait for the answer (polls up to wait_seconds). Returns the
    answer, or the query_id if it's still pending."""
    submitted = await _post("/api/investigate", {"question": question})
    query_id = submitted.get("query_id", "")
    if not query_id:
        return submitted
    deadline = asyncio.get_event_loop().time() + max(1, wait_seconds)
    while asyncio.get_event_loop().time() < deadline:
        res = await _get(f"/api/investigate/{query_id}")
        if res.get("status") == "complete":
            return res
        await asyncio.sleep(2)
    return {"query_id": query_id, "status": "pending",
            "note": f"still running after {wait_seconds}s; poll get_investigation('{query_id}')"}


@mcp.tool()
async def get_investigation(query_id: str) -> Any:
    """Poll a previously submitted Investigate query by its query_id."""
    return await _get(f"/api/investigate/{query_id}")


def main() -> None:
    """Console entry point — runs the server over stdio (for mcp.json clients)."""
    mcp.run()


if __name__ == "__main__":
    main()
