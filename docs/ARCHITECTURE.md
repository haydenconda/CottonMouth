# CottonMouth — Architecture

CottonMouth is an **agent observability platform**: it captures what AI agents do
(tool calls), why (decisions), what it costs (tokens/$), and what they were
allowed to do (permission checks), then renders that as structured traces and a
governance view. It deliberately follows the distributed-tracing playbook
(OpenTelemetry-style spans, NDJSON storage) applied to non-deterministic agent
runs.

This doc covers the runtime architecture, the Bedrock integration, how policy
enforcement works, and — importantly for the platform team — the **EKS /
Backstage template changes** that the deploy depends on.

---

## 1. Components

```
                 in-cluster (namespace: cottonmouth)

  ops-assistant (real agent) — CottonMouth SDK + boto3
       │
       │ Converse / InvokeModel
       ▼
  AWS Bedrock

  ops-assistant  ──POST /api/spans (batched)──▶  cottonmouth-backend
                 ◀──────POST /run (proxied)──────   • aiohttp API (/api/*)
                                                    • agent-trace watcher
                                                    • PVC /data: traces.jsonl,
                                                      events.jsonl, *.db, policies
                                                           │ /api/*
                                                           ▼
                                                    cottonmouth-web (Next.js SSR)
                                                      • proxies /api/* → backend
                                                           │ port-forward / Ingress
                                                           ▼
                                                        browser
```

| Component | Image | Role |
|---|---|---|
| `cottonmouth-backend` | `cottonmouth-backend` | aiohttp API (`/api/spans`, `/api/traces`, `/api/agents`, `/api/policies`, `/api/permissions`, `/api/agent/run`), the **agent-trace watcher**, and serves the policy doc. Owns the PVC. |
| `cottonmouth-web` | `cottonmouth-web` | Next.js dashboard. Runs in `standalone` mode and **proxies `/api/*`** to `cottonmouth-backend` (so the browser only talks to one origin — no CORS). |
| `cottonmouth-real-agent` (`ops-assistant`) | `cottonmouth-backend` (same image, different command) | A real tool-using Bedrock agent. Runs autonomously on a timer **and** serves `POST /run` for interactive task submission. |
| `cottonmouth-sample-agent` | `cottonmouth-backend` | Synthetic trace generator (scaled to 0 once the real agent is live). |

> The agents reuse the **backend image** because it already bundles the CottonMouth SDK,
> `boto3`, and the `examples/` agents. Only the container `command` differs.

---

## 2. Data flow — the span lifecycle

1. **Instrumentation (in the agent).** The agent calls `cottonmouth.configure(export="http",
   endpoint="http://cottonmouth-backend:8150", auto_instrument=True)`. Each agent run
   opens a root `agent_run` span; child spans (`llm_call`, `tool_call`,
   `decision`, `permission_check`) are created as work happens.
2. **Export (push, async).** Spans go to the `HTTPExporter`, which **queues them
   and flushes from a background daemon thread** in batches to
   `POST {endpoint}/api/spans`. The agent never blocks on the network; the SDK
   uses only the Python stdlib (no extra deps). See `sdk/src/cottonmouth/exporters.py`.
3. **Ingest (backend).** `POST /api/spans` sanitizes each span against an
   allow-list of fields (`_SPAN_FIELDS`) and appends them as NDJSON to
   `traces.jsonl` on the PVC.
4. **Watch.** The `agent-trace` watcher tails `traces.jsonl` and emits **events**
   (`events.jsonl`) for completed runs, failures, cost spikes, slow runs, and
   **permission denials**. Dedup/stats live in a small SQLite DB on the PVC.
5. **Read.** The API reconstructs traces, the agent registry, and the permission
   audit by reading those files; the web app renders them.

**Key property: ingestion is push-based and decoupled.** Agents and the collector
don't share a disk; an agent anywhere that can reach the Service (or, with an
ingress, the internet) can ship traces.

---

## 3. How does CottonMouth know which pods are AI agents?

**It doesn't — and that's by design.** CottonMouth does **not** discover pods, scan the
cluster, or inspect workloads. There is no Kubernetes controller, label
selector, or admission webhook involved in identifying agents.

Instead, **agents self-identify by emitting spans**:

- An agent becomes "known" to CottonMouth the moment it **POSTs a span** carrying an
  `agent_name` (set via `cottonmouth.configure` / `@trace_agent(name=...)`).
- The **agent registry** (`/api/agents`) is *derived* from the `agent_run` spans
  that have been received — it's an aggregation of observed traces, not a
  registration list.
- Identity is the `agent_name` string, not a pod, Deployment, or ServiceAccount.
  Two replicas of the same agent are one logical agent; a script on a laptop and
  a pod in EKS are indistinguishable to CottonMouth if they use the same name.

Implications:

- **Instrumentation is opt-in.** A pod that doesn't use the SDK is invisible to
  CottonMouth, even if it's an "AI agent." Conversely, anything that speaks the
  `/api/spans` contract shows up.
- **No cluster privileges needed** for observability — CottonMouth needs no RBAC to list
  pods. The trust boundary is "who can POST to `/api/spans`" (optionally gated by
  `COTTONMOUTH_API_KEY`).
- **Portable.** The same instrumentation works in EKS, in Docker Compose, or
  locally against `traces.jsonl`.

This is the same model as application APM: the app emits telemetry; the backend
doesn't go looking for it.

---

## 4. Bedrock integration

Bedrock shows up in **two** places:

### 4a. Agents calling Bedrock for inference
`ops-assistant` (`examples/ops_agent.py`) uses the Bedrock **Converse API with
tool-use** (function calling) against `anthropic.claude-3-haiku`. It loops:
model proposes a tool → CottonMouth records a `decision` → policy check (`permission_check`)
→ if allowed, the tool runs (`tool_call`) → result is fed back → repeat until the
model gives a final answer.

### 4b. Auto-instrumentation (how LLM calls become spans for free)
`cottonmouth.configure(auto_instrument=True)` monkeypatches **`botocore.client.BaseClient._make_api_call`**
— the single chokepoint every boto3 call flows through, stable across botocore
versions (`sdk/src/cottonmouth/llm_hooks.py`). For Bedrock runtime operations
(`Converse`, `ConverseStream`, `InvokeModel`, `InvokeModelWithResponseStream`)
made **inside an active trace context**, it:

1. Opens an `llm_call` child span with the model id.
2. Calls the real API.
3. Extracts token counts — from the `usage` block (Converse) or the
   `x-amzn-bedrock-input/output-token-count` response headers (InvokeModel).
4. Estimates cost from a per-model price table (`MODEL_COSTS_PER_1K`) and records
   tokens + `cost_usd` on the span.

Everything non-Bedrock (or outside a trace) passes straight through untouched.
Root-span cost/tokens are aggregated by the agent so the Agents table shows
per-run totals.

### 4c. Bedrock auth (IRSA vs the Secret workaround)
- **Preferred: IRSA.** Annotate the agent's ServiceAccount with an IAM role that
  trusts the cluster's OIDC provider and grants `bedrock:InvokeModel*` /
  `bedrock:Converse*`. The code uses the **default AWS credential chain** (no
  `AWS_PROFILE`), so IRSA creds are picked up automatically. Trust + policy JSON
  are in `deploy/aws/`.
- **What we actually used in the sandbox:** an org **SCP blocked IAM role
  creation**, so we fell back to injecting short-lived SSO credentials as a
  Kubernetes Secret (`cottonmouth-aws-creds`, consumed via `envFrom`). This works but the
  creds **expire (~1h)** and must be refreshed. *This is a sandbox compromise, not
  the target design* — see the Backstage section.
- The backend's optional **"Investigate"** feature (Bedrock-powered RCA, model in
  `configmap.yaml`) uses the same IRSA path on the `cottonmouth-backend` ServiceAccount.

Also note: **Bedrock model access must be enabled in the account/region** for the
specific model ids, independent of IAM.

---

## 5. Policy enforcement & governance

CottonMouth treats permissions as **policy-as-data** with a single source of truth:
`agent_policies.json` (baked into the image).

```
 agent_policies.json  ──baked into image──┐
        │                                  │
        │ loaded at runtime                │ loaded on request
        ▼                                  ▼
  ops-assistant (enforces)          cottonmouth-backend (serves)
  permission_check spans            GET /api/policies   ──▶  Governance UI
        │                           GET /api/permissions ─┘   (live audit)
        ▼
   traces.jsonl ──▶ agent-trace watcher ──▶ "Permission Denied" events
```

- **Enforcement (agent side).** Before *every* tool call, the agent evaluates the
  request against the policy and emits a `permission_check` span with
  `permission_result = allow|deny` and the policy text. It's **deny-by-default**:
  - `read_file` / `write_file`: path must resolve **inside the workspace**.
  - `run_command`: binary must be on the **command allowlist** and not on the
    **deny-list** (`rm`, `sudo`, `chmod`, …).
  - `http_fetch`: host must be on the **network allowlist**.
  On a denial, the tool does **not** run; the denial is fed back to the model,
  which adapts. The agent loads these rules *from the JSON* (`_load_policy`), so
  enforcement and the UI can't drift.
- **Serving (backend side).** `GET /api/policies` returns the document verbatim.
  `GET /api/permissions` aggregates all `permission_check` spans into a live audit
  (allow/deny totals, per-action breakdown, recent denials linking to traces).
- **UI.** The **Governance** page shows the policy cards (tools granted, filesystem
  scope, command allow/deny, network allowlist) next to the live audit.

This maps to the four "agent observability gap" questions: *what it did*
(`tool_call`), *why* (`decision`), *what it cost* (`llm_call` tokens/$), and
*what it was allowed to do* (`permission_check` + Governance).

> Today the policy is **baked into the image** (edit JSON → rebuild → redeploy). The
> natural next step is to mount it as a **ConfigMap** (set `COTTONMOUTH_POLICIES_FILE`) so
> rules change without a rebuild — the loader already supports that env override.

---

## 6. Persistence & state

All mutable state lives under **`COTTONMOUTH_DATA_DIR=/data`**, mounted from a single
PVC (`cottonmouth-data`). This is what survives pod restarts:

| File | Purpose |
|---|---|
| `traces.jsonl` | All spans (the trace store) |
| `events.jsonl` | Watcher-emitted events |
| `agent_state.db` (SQLite) | Watcher dedup + rolling stats |
| `health.json`, queue files | Watcher health / Investigate queue |

Consequences baked into the manifests:

- The trace store is a **file on a ReadWriteOnce volume**, so `cottonmouth-backend` runs
  **`replicas: 1`** with **`strategy: Recreate`** (no two pods writing the same
  file). Scaling the backend horizontally would require swapping the file store
  for a shared DB.
- `fsGroup: 0` so the mounted volume is writable.

---

## 7. EKS / Backstage template changes

The app manifests are standard, but the deploy depended on several **cluster-level
prerequisites** that weren't all present on the Backstage-provisioned sandbox
cluster. These are the things to fold into the template (or a post-provision
step), roughly in order of how much they bit us.

### 7.1 Cluster access (this blocked us first)
The cluster came up in **EKS API authentication mode** with **no human/SSO admin
access entry**, so `kubectl` returned 401 even with valid AWS creds. We had to add
an access entry for the SSO role:

```bash
aws eks create-access-entry \
  --cluster-name <cluster> --region <region> \
  --principal-arn arn:aws:iam::<acct>:role/<sso-or-provisioner-role>

aws eks associate-access-policy \
  --cluster-name <cluster> --region <region> \
  --principal-arn arn:aws:iam::<acct>:role/<sso-or-provisioner-role> \
  --policy-arn arn:aws:eks::aws:cluster-access-policy/AmazonEKSClusterAdminPolicy \
  --access-scope type=cluster
```

**Template change:** when `authentication_mode` is `API` (or `API_AND_CONFIG_MAP`),
the template should automatically create **access entries** for the roles humans
actually use (the SSO permission-set role(s) and the provisioner role), with an
appropriate access policy (`AmazonEKSClusterAdminPolicy` for admins, or a scoped
policy). Don't ship a cluster nobody can `kubectl` into.

### 7.2 EBS CSI driver + a default StorageClass (PVCs)
CottonMouth needs a PVC. The PVC pins `storageClassName: gp2`, which assumes both a
`gp2` StorageClass **and** the EBS CSI driver being installed and able to call
AWS. If the cluster has neither, the PVC sits **Pending** forever.

**Template change:**

- Install the **`aws-ebs-csi-driver`** EKS add-on, with its **IRSA role**
  (`AmazonEBSCSIDriverPolicy`) — the add-on can't provision volumes otherwise.
- Ship a **default StorageClass** (prefer **`gp3`**) so workloads don't have to
  hardcode a class. (We can then drop the explicit `storageClassName` from the PVC,
  or switch it to `gp3`.)

### 7.3 IAM OIDC provider + IRSA (so we don't need credential Secrets)
Bedrock access *should* be IRSA, but we couldn't create the role due to an org
**SCP deny**, and fell back to a short-lived-credential Secret.

**Template change:**

- Ensure the cluster's **IAM OIDC provider is enabled/associated** (needed for any
  IRSA, including the EBS CSI driver above).
- Provide a sanctioned path to create **IRSA roles** in sandbox accounts — i.e. an
  **SCP exception** or a delegated/pre-created role pattern for workload identities
  (e.g. a Bedrock-invoke role). Without this, every team re-invents the
  expiring-Secret hack.

### 7.4 Ingress / load balancing (external access)
There's **no AWS Load Balancer Controller** on the cluster, so we couldn't use an
Ingress and accessed the dashboard via `kubectl port-forward` (which drops on
every pod rollout). The `ingress.yaml` is therefore omitted from kustomize.

**Template change (optional):** offer an opt-in to install the **AWS Load Balancer
Controller** (+ its IRSA role and an `IngressClass`) for clusters that need
real external endpoints. If enabled, remember LBs are AWS resources that must be
cleaned up before the cluster's TTL.

### 7.5 ECR & node pull permissions
We created ECR repos (`cottonmouth-backend`, `cottonmouth-web`) and pushed **`linux/amd64`**
images (EKS nodes are x86; an arm64 image from Apple Silicon fails with
`no match for platform in manifest`). Image pulls worked because the managed node
group's role already has ECR read.

**Template note:** if the template uses custom node roles, ensure
`AmazonEC2ContainerRegistryReadOnly` (or equivalent) is attached. Optionally
pre-create app ECR repos. (The amd64 build is a CI/build concern, not a template
one, but worth documenting.)

### 7.6 TTL / teardown
The sandbox cluster has a **72h TTL**, and **PVs (EBS) and LoadBalancers created
inside the cluster are orphaned** if not deleted first.

**Template change:** make the TTL-destroy step (or a documented teardown:
`kubectl delete -k deploy/k8s` before cluster destroy) delete in-cluster AWS-backed
resources (EBS volumes from PVCs, any LBs) to avoid orphans.

### Summary checklist for the template
- [ ] Auto-create **EKS access entries** for SSO/provisioner roles (API auth mode).
- [ ] Install **EBS CSI driver** add-on **+ IRSA**, and a **default `gp3` StorageClass**.
- [ ] Associate the **IAM OIDC provider**; provide a sanctioned **IRSA role** path (SCP exception) for workload identities like Bedrock.
- [ ] (Optional) Install **AWS Load Balancer Controller** + IngressClass for external access.
- [ ] Ensure node role has **ECR read**; (optional) pre-create ECR repos.
- [ ] Teardown step deletes **PVC/EBS + LBs** before TTL expiry.

---

## 8. Where things live (quick map)

| Concern | Path |
|---|---|
| SDK (instrumentation, exporters, auto-instrument) | `sdk/src/cottonmouth/` |
| Bedrock auto-instrumentation | `sdk/src/cottonmouth/llm_hooks.py` |
| Tool-using agent | `examples/ops_agent.py` |
| Policy source of truth | `agent_policies.json` |
| Policy loader (shared) | `src/common/policies.py` |
| API (ingest, traces, policies, permissions, agent proxy) | `src/api.py` |
| Trace watcher (events, denials) | `src/watchers/agent_trace_watcher.py` |
| Data paths (PVC) | `src/common/paths.py` |
| K8s manifests | `deploy/k8s/` |
| IRSA trust/policy JSON | `deploy/aws/` |
| Deploy runbook | `deploy/README.md` |
| Web (dashboard, Governance page) | `web/src/` |
