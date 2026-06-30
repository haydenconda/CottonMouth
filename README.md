# CottonMouth

AI agent observability & governance platform. Explore every agent run — LLM
calls, tool calls, decisions, cost, latency, and policy checks — in a live
dashboard. Two ways to get there: route agents through a shared **LiteLLM
gateway** and CottonMouth observes them with **zero agent code** (model *and*
MCP tool calls, attributed per virtual key), or instrument directly with the SDK
(`@trace_agent`) for the richest in-process reasoning. An AWS Bedrock–backed
"Investigate" agent explains failures and cost anomalies on demand.

CottonMouth is built to close the [agent observability gap](https://siddhantkhare.com/writing/agent-observability-gap):
for every agent run, answer the four questions that matter.

| Pillar | Question | How CottonMouth answers it |
|--------|----------|----------------------------|
| **What it did** | Which calls and tools ran? | `agent_run` / `llm_call` / `tool_call` spans (incl. MCP tool calls via the gateway) |
| **Why it did it** | What was the reasoning? | `decision` spans capture branch points |
| **What it cost** | Tokens, dollars, latency? | Per-call token/cost accounting + spend anomalies |
| **What it was allowed to do** | Did it stay in policy? | `permission_check` spans — the SDK's policies-as-data **and** the LiteLLM gateway's allow/deny verdicts |

```
┌─────────────────┐   spans over HTTP    ┌──────────────────────┐         ┌──────────────┐
│   Your agent    │ ───────────────────▶ │  CottonMouth backend │ ◀────── │     Web      │
│ + CottonMouth   │   POST /api/spans    │  API + watchers      │  /api   │  dashboard   │
│ @trace_agent    │                      │  Bedrock investigate │         │  (Next.js)   │
└─────────────────┘                      └──────────────────────┘         └──────────────┘
```

## Components

| Path | What it is |
|------|------------|
| `sdk/` | The `cottonmouth` tracing SDK — `@trace_agent` decorator, auto-instrumentation for Anthropic/OpenAI/Bedrock, JSONL + HTTP exporters. Zero runtime dependencies. |
| `src/` | Backend: aiohttp API (`:8150`), watchers (turn traces into events/alerts), policy/permission enforcement, and the Bedrock investigate agent. |
| `src/common/agent_stats.py` | Shared, recent-window agent stats with infra-failure classification (single source of truth for the dashboard). |
| `web/` | Next.js dashboard — overview, traces, agents, governance, and events (with live drill-downs and an interactive task runner). |
| `agent_policies.json` | Policies-as-data: what each agent is allowed to do, enforced at runtime and surfaced in the governance view. |
| `examples/` | Instrumented agents. **`devils_council.py`** is the flagship: a gateway-routed multi-persona reviewer that exercises all four pillars (LLM gateway + MCP gateway + SDK decision/permission spans). `ops_agent.py` is a Bedrock tool-using agent; `litellm_agent.py` shows the SDK callback. |
| `deploy/k8s/` | Kustomize manifests to run the platform on Kubernetes / EKS, including the **LiteLLM gateway** (`litellm` + `litellm-db`). |
| `docs/ARCHITECTURE.md` | System design centered on the LiteLLM gateway integration: the four pillars, two-span-source reconciliation, policy enforcement, agent identification, and EKS setup. |
| `CondaMon/` | Optional macOS menu-bar ticker (local-only; not part of the cluster deploy). |

## Quick start (SDK)

```python
import cottonmouth

cottonmouth.configure(export="http", endpoint="http://localhost:8150")  # or COTTONMOUTH_ENDPOINT

@cottonmouth.trace_agent(name="support-bot", version="1.0.0")
def handle(ticket: str) -> str:
    ...
```

With `auto_instrument=True` (default), CottonMouth patches the Anthropic/OpenAI/Bedrock
clients so every LLM call is captured as a child span (model, tokens, cost).

Exporters:
- `export="http", endpoint=...` — ship spans to a remote collector (multi-pod / cluster).
- `export="jsonl", path=...` — write spans to a local file (single host).
- Env fallbacks: `COTTONMOUTH_ENDPOINT`, `COTTONMOUTH_EXPORT`, `COTTONMOUTH_TRACES_PATH`, `COTTONMOUTH_API_KEY`.

## Run it locally (Docker Compose)

Backend + dashboard + a live sample agent:

```bash
git clone git@github.com:haydenconda/CottonMouth.git
cd CottonMouth
docker compose up --build
# dashboard:  http://localhost:3000
# backend:    http://localhost:8150/api/health
```

## Run the backend directly (uv)

```bash
uv sync
COTTONMOUTH_WATCHERS=agent-trace uv run python -m src.main      # API on :8150

# In another shell, generate live traces:
COTTONMOUTH_ENDPOINT=http://127.0.0.1:8150 PYTHONPATH=sdk/src \
  uv run python examples/sample_agent.py
```

Seed one-off sample data instead of a live agent:

```bash
uv run python scripts/seed_traces.py
```

## Deploy to a cluster

See **[deploy/README.md](deploy/README.md)** for building/pushing images and
deploying to EKS (with optional Bedrock Investigate via IRSA).

```bash
kubectl apply -k deploy/k8s
kubectl -n cottonmouth port-forward svc/cottonmouth-web 8080:3000
# dashboard: http://localhost:8080
```

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `COTTONMOUTH_DATA_DIR` | repo root | Directory for all state (traces, events, health, SQLite). Set to a mounted volume in containers. |
| `COTTONMOUTH_WATCHERS` | (all configured) | Comma list to whitelist watchers, e.g. `agent-trace` for the core platform. |
| `COTTONMOUTH_DISABLE_RELOAD` | unset | Set in containers to disable the dev code-reload watcher. |
| `COTTONMOUTH_API_KEY` | unset | If set, `POST /api/spans` requires `Authorization: Bearer <key>`. |
| `AGENT_TRACES_DIR` | `COTTONMOUTH_DATA_DIR` | Override only the traces file location. |
| `AGENT_COST_ALERT_THRESHOLD` | `1.0` | USD per run that triggers a cost-spike event. |
| `BEDROCK_MODEL` / `BEDROCK_REGION` | opus / us-east-1 | Investigate model. Uses IRSA / default AWS creds in-cluster, or `AWS_PROFILE` locally. |

## API

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/spans` | Ingest one span or a batch (used by the SDK HTTP exporter). |
| `GET`  | `/api/traces` | Recent agent runs. |
| `GET`  | `/api/traces/{id}` | Full trace with spans. |
| `GET`  | `/api/agents` | Per-agent recent-window stats (infra failures classified separately). |
| `GET`  | `/api/agents/{name}` | Single-agent detail. |
| `GET`  | `/api/events` | Events/alerts (`/api/events/stream` for SSE). |
| `GET`  | `/api/policies` | Agent policies (what each agent is allowed to do). |
| `GET`  | `/api/permissions` | Recent permission checks / denials. |
| `GET`  | `/api/search?q=` | Search across traces. |
| `POST` | `/api/investigate` | Ask the Bedrock investigate agent (poll `GET /api/investigate/{id}`). |
| `GET`  | `/api/health` | Health snapshot. |

## Integrations

CottonMouth meets agents where they already are. If your calls already flow
through a gateway or another SDK, you can stream them in without rewriting agent
code.

| Integration | Mode | Install | Register |
|-------------|------|---------|----------|
| **LiteLLM** | SDK + Proxy | `pip install "cottonmouth[litellm]"` | `litellm.callbacks = [CottonmouthLogger()]` |
| Anthropic / OpenAI / Bedrock | SDK auto-instrument | `cottonmouth[all]` | `cottonmouth.configure(auto_instrument=True)` |

### LiteLLM gateway (the recommended path)

Put a shared LiteLLM gateway in front of every model and tool call and let
CottonMouth observe it. The gateway holds the provider credentials, issues
per-agent **virtual keys**, and brokers **MCP tools**; CottonMouth plugs in as a
callback and turns that traffic into spans. Any agent that routes through the
gateway is observed automatically — **no per-agent instrumentation and no
provider credentials in the agent**.

- Every completion becomes an `llm_call` span (model, tokens, cost, latency,
  status) nested under the owning `agent_run`.
- Every brokered MCP tool call becomes a `tool_call` span with a nested
  `permission_check` for the gateway's allow/deny verdict.
- Spans are attributed per agent via the virtual key's `user_api_key_alias`, and
  the **Agents** view rolls up gateway-only agents (model + MCP tool usage).

> **Flagship demo:** [`examples/devils_council.py`](examples/devils_council.py) —
> a gateway-routed multi-persona code reviewer whose run lands in CottonMouth as
> one trace: `agent_run` + per-persona `decision`/`permission_check` (SDK) + the
> gateway-emitted `llm_call`s and a GitHub `tool_call`. See the
> [LiteLLM Gateway Integration](https://anaconda.atlassian.net/wiki/spaces/IN/pages/6490783762)
> page for topology and developer setup.

#### SDK callback (single agent)

```python
import litellm, cottonmouth
from cottonmouth.integrations.litellm import CottonmouthLogger, with_cottonmouth

cottonmouth.configure(export="http", endpoint="http://cottonmouth-backend:8150",
                      auto_instrument=False)  # the callback is the single span source
litellm.callbacks = [CottonmouthLogger()]

# Thread the active agent_run context into the call so the span nests correctly:
litellm.completion(model="bedrock/anthropic.claude-3-haiku-20240307-v1:0",
                   messages=[...], **with_cottonmouth())
```

Proxy mode — reference the ready-made instance in `config.yaml`:

```yaml
litellm_settings:
  callbacks: cottonmouth.integrations.litellm.cottonmouth_callback
```

**Enforcement vs. observation.** CottonMouth does **not** duplicate LiteLLM's
gateway controls. Model access, budgets, rate limits, and guardrails are enforced
by the LiteLLM gateway via virtual keys; CottonMouth *observes* those decisions —
when the gateway rejects a call it records the verdict as a `permission_check`
(deny, attributed to LiteLLM, classified as budget / model-access / rate-limit /
auth / guardrail) feeding the governance and events views. Successful calls
record the gateway's allow. CottonMouth's own policy layer governs what LiteLLM
can't see: the agent's tool/file/command/network permissions.

```python
from cottonmouth.integrations.litellm import enable
enable()   # observability only — the gateway owns enforcement
```

**Call provenance.** Every `llm_call` span carries an `origin` block — the
agent/identity, the resolved provider, the host/pod/pid, and the `file:line:func`
call site — surfaced in the trace detail view so you can see exactly where a
gateway call came from.

Notes:
- **Correlation** is resolved in three tiers: explicit `metadata.cottonmouth`
  (via `with_cottonmouth()`, survives thread/process hops) → in-process
  contextvars → a standalone trace (a call is never dropped).
- **Cost** uses LiteLLM's computed `response_cost` (authoritative across
  providers), falling back to CottonMouth's estimate.
- **Identity**: LiteLLM virtual-key / team / user tags are mapped onto agent
  identity and surfaced in the governance view.
- **Streaming** is aggregated into one span per call (not one per token), and the
  callback is non-blocking — exporter errors never break the LiteLLM request path.
- Runnable demos: [`examples/devils_council.py`](examples/devils_council.py)
  (gateway-routed, all four pillars) and
  [`examples/litellm_agent.py`](examples/litellm_agent.py) (SDK callback).

## Optional: macOS ticker

The Swift menu-bar app in `CondaMon/` is a local-only UI and is not part of the
cluster deployment. See the `Makefile` (`make build-ticker`, `make start`).
