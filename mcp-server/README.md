# CottonMouth MCP server

Talk to CottonMouth from any MCP client (Cursor, Claude Desktop, your own
agent). This is the inverse of the gateway capture: instead of CottonMouth
observing your agents, this lets an agent **observe CottonMouth** — query traces,
agents, cost, governance/compliance, and metrics, and even kick off an
Investigate.

Ask things like:

- "Which of my agents is out of compliance right now?"
- "Show the last failed trace for `ops-assistant` and why it failed."
- "What did `devils-council` cost today, and which tools did it call?"
- "Investigate: why did error rate spike in the last hour?"

## Tools

| Tool | Backs onto | Purpose |
| --- | --- | --- |
| `health` | `GET /api/health` | Backend status |
| `list_traces` | `GET /api/traces` | Recent runs (filter by agent/status) |
| `get_trace` | `GET /api/traces/{id}` | Full trace with all spans |
| `get_span` | `GET /api/traces/{id}/spans/{id}` | One span's detail |
| `list_agents` | `GET /api/agents` | All agents + rollup stats |
| `get_agent` | `GET /api/agents/{name}` | One agent (+ recent gateway calls) |
| `search_traces` | `GET /api/search` | Substring search across spans |
| `list_events` | `GET /api/events` | Failures / denials / anomalies |
| `get_compliance` | `GET /api/permissions` | Compliance %, per-agent mode, denials |
| `get_policies` | `GET /api/policies` | Policy-as-data document |
| `get_gateway` | `GET /api/gateway` | Gateway model-access reconcile |
| `get_metrics` | `GET /metrics` | Prometheus metrics (text) |
| `investigate` | `POST /api/investigate` | Ask the Investigate agent (waits for answer) |
| `get_investigation` | `GET /api/investigate/{id}` | Poll an investigation |

All tools are read-only except `investigate`, which submits a query.

## Configuration

| Env var | Default | Meaning |
| --- | --- | --- |
| `COTTONMOUTH_ENDPOINT` | `http://localhost:8150` | Backend base URL |
| `COTTONMOUTH_API_KEY` | – | Optional bearer token, sent on every request |
| `COTTONMOUTH_MCP_TIMEOUT` | `30` | Per-request timeout (seconds) |

If the backend runs in-cluster, port-forward it first:

```bash
kubectl -n cottonmouth port-forward svc/cottonmouth-backend 8150:8150
```

## Install & run

```bash
cd mcp-server
uv sync            # or: pip install -e .
COTTONMOUTH_ENDPOINT=http://localhost:8150 cottonmouth-mcp
```

## Register in an MCP client

Add to your client's `mcp.json` (Cursor: `~/.cursor/mcp.json` or project
`.cursor/mcp.json`). See [`examples/mcp.json`](examples/mcp.json):

```json
{
  "mcpServers": {
    "cottonmouth": {
      "command": "uv",
      "args": ["run", "--project", "/absolute/path/to/mole/mcp-server", "cottonmouth-mcp"],
      "env": { "COTTONMOUTH_ENDPOINT": "http://localhost:8150" }
    }
  }
}
```

Once registered, the client lists the tools above and your agent can call them.
