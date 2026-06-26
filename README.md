# CottonMouth

AI agent observability & governance platform. Instrument any agent with one
decorator, ship traces to a collector, and explore every run — LLM calls, tool
calls, decisions, cost, latency, and policy checks — in a live dashboard. An AWS
Bedrock–backed "Investigate" agent explains failures and cost anomalies on demand.

CottonMouth is built to close the [agent observability gap](https://siddhantkhare.com/writing/agent-observability-gap):
for every agent run, answer the four questions that matter.

| Pillar | Question | How CottonMouth answers it |
|--------|----------|----------------------------|
| **What it did** | Which calls and tools ran? | `agent_run` / `llm_call` / `tool_call` spans |
| **Why it did it** | What was the reasoning? | `decision` spans capture branch points |
| **What it cost** | Tokens, dollars, latency? | Per-call token/cost accounting + spend anomalies |
| **What it was allowed to do** | Did it stay in policy? | `permission_check` spans + policies-as-data |

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
| `examples/` | Live instrumented agents (`sample_agent.py`, `real_agent.py`, `ops_agent.py`) that continuously emit realistic traces — drive the demo. |
| `deploy/k8s/` | Kustomize manifests to run the platform on Kubernetes / EKS. |
| `docs/ARCHITECTURE.md` | System design: Bedrock integration, policy enforcement, agent identification, EKS setup. |
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

### LiteLLM gateway

LiteLLM gets your requests to the model; CottonMouth tells you what your agent
did with them, and whether it was allowed. Every LiteLLM completion becomes an
`llm_call` span (model, tokens, cost, latency, status) that nests under the
owning `agent_run`.

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
- Runnable demo: [`examples/litellm_agent.py`](examples/litellm_agent.py).

## Optional: macOS ticker

The Swift menu-bar app in `CondaMon/` is a local-only UI and is not part of the
cluster deployment. See the `Makefile` (`make build-ticker`, `make start`).
