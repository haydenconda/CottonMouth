# CottonMouth metrics (Prometheus / OpenTelemetry)

CottonMouth exposes its observability data as Prometheus text-format metrics at:

```
GET http://cottonmouth-backend:8150/metrics
```

Platform engineers can scrape this into Prometheus/Grafana and build their own
dashboards and Alertmanager rules, instead of being limited to the built-in
dashboard. The Prometheus exposition format is also what the OpenTelemetry
Collector's `prometheus` receiver scrapes, so this doubles as the OTel path.

## These are real counters — use rate() / increase()

Metrics are **incremented as spans are ingested**, so the `_total` series are
proper monotonic Prometheus counters. Graph and alert on them with `rate()`,
`increase()`, and `histogram_quantile()` — e.g. `sum by (agent) (rate(cottonmouth_agent_runs_total[5m]))`.

Counters **reset to 0 when the backend restarts**; that's expected and
`rate()`/`increase()` handle counter resets automatically. On startup the
counters are seeded once from the existing trace window so `/metrics` isn't
empty right after a (re)deploy. `cottonmouth_up` is a point-in-time gauge.

## Metrics

| Metric | Type | Labels | Meaning |
| --- | --- | --- | --- |
| `cottonmouth_up` | gauge | – | 1 while the backend is serving metrics |
| `cottonmouth_info` | gauge | `version` | Build info, value always 1 |
| `cottonmouth_spans_ingested_total` | counter | `span_type`, `agent` | Every span ingested, by type |
| `cottonmouth_agent_runs_total` | counter | `agent`, `status` | Agent runs; `status` = `completed`/`failed`/`infra_failure` |
| `cottonmouth_llm_calls_total` | counter | `agent`, `model`, `status` | Model calls |
| `cottonmouth_tool_calls_total` | counter | `agent`, `status` | MCP tool calls |
| `cottonmouth_permission_checks_total` | counter | `agent`, `result`, `mode` | Authorization checks; `result` = `allow`/`deny`, `mode` = `enforce`/`monitor` |
| `cottonmouth_tokens_total` | counter | `agent`, `direction` | Tokens; `direction` = `input`/`output` |
| `cottonmouth_cost_usd_total` | counter | `agent` | Spend in USD |
| `cottonmouth_agent_run_duration_seconds` | histogram | `agent` | Run wall-clock duration (use `histogram_quantile` on `_bucket`) |

### Useful queries

```promql
# Runs per minute by agent
sum by (agent) (rate(cottonmouth_agent_runs_total[5m])) * 60

# Agent-logic error rate (infra failures excluded)
sum(rate(cottonmouth_agent_runs_total{status="failed"}[5m]))
  / clamp_min(sum(rate(cottonmouth_agent_runs_total{status=~"completed|failed"}[5m])), 0.0001)

# Spend rate ($/hr)
sum by (agent) (rate(cottonmouth_cost_usd_total[5m])) * 3600

# p95 run duration
histogram_quantile(0.95, sum by (le) (rate(cottonmouth_agent_run_duration_seconds_bucket[5m])))

# Fleet compliance (allow / total)
sum(cottonmouth_permission_checks_total{result="allow"})
  / clamp_min(sum(cottonmouth_permission_checks_total), 1)

# Would-block (shadow) denials in the last hour, by agent
sum by (agent) (increase(cottonmouth_permission_checks_total{result="deny", mode="monitor"}[1h]))
```

The `mode` label distinguishes **enforce** (the action was blocked) from
**monitor** (shadow mode: the verdict was recorded but the action proceeded).
In monitor mode `result="deny"` means "would have been blocked" — this is how
you discover the out-of-compliance population before turning on enforcement.

## Scraping

If you run the Prometheus Operator, apply the `ServiceMonitor`:

```
kubectl apply -f deploy/observability/servicemonitor.yaml
```

Otherwise add a static scrape job (see `prometheus-scrape.yaml`).

## Dashboards & alerts

- `grafana-dashboard.json` — import into Grafana (fleet compliance, per-agent
  cost/error/latency, tool calls, denials).
- `prometheus-alerts.yaml` — example Prometheus/Alertmanager rules (high error
  rate, cost spike, compliance drop, denial surge).
