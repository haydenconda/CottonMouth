# CottonMouth API

The backend (aiohttp, port `8150`) serves a small, stable HTTP API. It backs the
dashboard, the metrics exporter, and the [CottonMouth MCP server](../mcp-server/).
`GET /api` returns a machine-readable list of endpoints and the API version.

Base URL in-cluster: `http://cottonmouth-backend:8150`. Locally, port-forward:

```bash
kubectl -n cottonmouth port-forward svc/cottonmouth-backend 8150:8150
```

## Auth

Reads are open by default. The ingest endpoint (`POST /api/spans`) requires
`Authorization: Bearer <token>` when `COTTONMOUTH_API_KEY` is set. The MCP server
sends that bearer on every request if `COTTONMOUTH_API_KEY` is configured.

## Endpoints

| Method | Path | Description |
| --- | --- | --- |
| GET | `/api` | Discovery: name, `api_version`, endpoint map |
| GET | `/api/health` | Health snapshot |
| GET | `/api/events` | Recent events; `?severity=&source=&limit=` |
| GET | `/api/events/stream` | SSE stream of new events |
| GET | `/api/traces` | Recent runs; `?agent_name=&status=&limit=` |
| GET | `/api/traces/{trace_id}` | Full trace with spans |
| GET | `/api/traces/{trace_id}/spans/{span_id}` | Single span |
| GET | `/api/agents` | All agents + gateway-only rollups |
| GET | `/api/agents/{name}` | One agent (gateway agents include recent calls) |
| GET | `/api/search` | Substring search; `?q=&agent_name=&status=&limit=` |
| GET | `/api/policies` | Policy-as-data document |
| GET | `/api/gateway` | LiteLLM model-access reconcile / drift |
| GET | `/api/permissions` | Governance audit + compliance rollup |
| GET | `/metrics` | Prometheus metrics (see `deploy/observability/`) |
| POST | `/api/spans` | Ingest spans (SDK/gateway); bearer-gated |
| POST | `/api/investigate` | Submit an Investigate query → `query_id` |
| GET | `/api/investigate/{query_id}` | Poll an investigation |
| POST | `/api/agent/run` | Proxy a task to the interactive agent |

## Compliance fields (`/api/permissions`)

```jsonc
{
  "summary": {
    "total": 42, "allowed": 40, "denied": 2,
    "compliance_rate": 0.952,     // allowed / total  ("% in compliance")
    "enforced_denied": 1,         // denies that blocked the action
    "monitored_denied": 1         // shadow-mode "would have blocked"
  },
  "by_agent": [
    { "agent_name": "devils-council", "compliance_rate": 0.8,
      "mode": "monitor", "monitored_denied": 3, "enforced_denied": 0 }
  ],
  "recent_denials": [ { "would_block": true, "mode": "monitor", "...": "..." } ]
}
```

`mode` is `enforce` (denied actions are blocked) or `monitor` (shadow mode:
verdicts recorded, actions allowed to proceed) — set per agent in
`agent_policies.json`. See [governance & monitor mode](../README.md).

> Full OpenAPI generation is tracked as a follow-up; `GET /api` is the current
> discovery contract.
