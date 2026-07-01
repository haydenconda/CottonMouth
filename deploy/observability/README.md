# CottonMouth metrics (Prometheus / OpenTelemetry)

CottonMouth exposes its observability data as Prometheus text-format metrics at:

```
GET http://cottonmouth-backend:8150/metrics
```

Platform engineers can scrape this into Prometheus/Grafana and build their own
dashboards and Alertmanager rules, instead of being limited to the built-in
dashboard. The Prometheus exposition format is also what the OpenTelemetry
Collector's `prometheus` receiver scrapes, so this doubles as the OTel path.

## Important: these are window gauges, not lifetime counters

Every metric is recomputed on each scrape from the same **rolling span window**
the dashboard reads (the most recent ~12k spans). They are exposed as **gauges**
describing the current window — they are *not* monotonic counters, so do **not**
wrap them in `rate()` / `increase()`. Alert and graph on the values directly
(e.g. `cottonmouth_agent_error_rate > 0.1`).

## Metrics

| Metric | Labels | Meaning |
| --- | --- | --- |
| `cottonmouth_up` | – | 1 while the backend is serving metrics |
| `cottonmouth_info` | `version` | Build info, value always 1 |
| `cottonmouth_spans_window` | – | Spans currently in the read window |
| `cottonmouth_agent_runs` | `agent` | Runs observed in the window |
| `cottonmouth_agent_errors` | `agent` | Agent-logic run failures (infra excluded) |
| `cottonmouth_agent_error_rate` | `agent` | Agent-logic error rate, 0–1 |
| `cottonmouth_agent_infra_failures` | `agent` | Infra failures (expired creds, throttling) |
| `cottonmouth_agent_run_duration_ms_avg` | `agent` | Avg run duration (ms) |
| `cottonmouth_agent_cost_usd` | `agent`, `kind` | Cost (USD); `kind` = `run` or `gateway` |
| `cottonmouth_agent_llm_calls` | `agent`, `kind` | Model calls (gateway agents) |
| `cottonmouth_agent_tool_calls` | `agent`, `kind` | MCP tool calls (gateway agents) |
| `cottonmouth_agent_tokens` | `agent`, `kind`, `direction` | Tokens; `direction` = `input`/`output` |
| `cottonmouth_permission_checks` | `agent`, `result`, `mode` | Authorization checks; `result` = `allow`/`deny`, `mode` = `enforce`/`monitor` |
| `cottonmouth_permission_checks_total_window` | `result` | Fleet authorization checks by verdict |
| `cottonmouth_agent_permission_denials` | `agent` | Out-of-compliance actions per agent |
| `cottonmouth_agent_compliance_ratio` | `agent` | Fraction of an agent's checks that were allowed, 0–1 |
| `cottonmouth_compliance_ratio` | – | Fleet-wide compliance ratio, 0–1 |

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
