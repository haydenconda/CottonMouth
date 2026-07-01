# Alerting

CottonMouth has two complementary ways to get notified when something needs
attention.

## 1. Built-in Slack webhook (no extra stack)

Point CottonMouth at a Slack [Incoming Webhook](https://api.slack.com/messaging/webhooks)
and matching events are pushed straight to a channel. Every event already flows
through one chokepoint (`emit_event`), so this covers agent failures, permission
denials, cost spikes, slow runs, and anomalies with no extra wiring. Delivery is
best-effort and off-thread, so a slow Slack never blocks the backend.

### Enable it

```bash
kubectl -n cottonmouth create secret generic cottonmouth-alerts \
  --from-literal=slack-webhook-url=https://hooks.slack.com/services/XXX/YYY/ZZZ

kubectl -n cottonmouth rollout restart deploy/cottonmouth-backend
```

The backend reads the webhook from that secret (mounted optionally, so the
deployment works with or without it).

### Configuration

| Env var | Default | Meaning |
| --- | --- | --- |
| `COTTONMOUTH_SLACK_WEBHOOK_URL` | – | Slack webhook. Unset = alerting disabled. |
| `COTTONMOUTH_ALERT_MIN_SEVERITY` | `warning` | Minimum severity to send: `info`, `warning`, `critical`. |
| `COTTONMOUTH_ALERT_SOURCES` | – | Comma-separated allowlist of event `source` values (e.g. `agent-error,agent-permission`). Unset = all. |
| `COTTONMOUTH_DASHBOARD_URL` | – | Public dashboard URL; makes the "Open trace" button in each alert clickable. |

The webhook URL is the only secret; the rest live in `cottonmouth-config`.

### What triggers an alert

The trace watcher emits events for:

- `critical` — agent run failed (agent-logic), LLM call failed
- `warning` — tool failed, **permission denied**, cost spike, slow run, infra issue
- `info` — normal run completed (filtered out by the default `warning` threshold)

Permission denials include shadow-mode "would block" verdicts, so you can watch
compliance drift even before enforcement is on.

## 2. Prometheus / Alertmanager (bring your own stack)

Teams already running Grafana/Alertmanager can alert off the metrics exporter
instead. Scrape `GET /metrics` (see [`deploy/observability/`](../deploy/observability/))
and use the example rules in
[`deploy/observability/prometheus-alerts.yaml`](../deploy/observability/prometheus-alerts.yaml)
(high error rate, cost spike, compliance drop, denial surge). This routes through
your existing on-call/Slack integrations.

Use whichever fits — the built-in webhook for a quick start, Alertmanager when
you want alerts alongside the rest of your platform.
