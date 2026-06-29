# Deploying CottonMouth to a cluster

This deploys the **observability core**: backend (API + agent-trace watcher),
the web dashboard, and a live sample agent that continuously emits traces so the
dashboard has fresh data. Bedrock "Investigate" is optional (see [IRSA](#optional-bedrock-investigate-irsa)).

```
┌───────────────┐   POST /api/spans    ┌──────────────┐   /api/* (proxy)   ┌────────────┐
│ sample-agent  │ ───────────────────▶ │ cottonmouth-backend │ ◀───────────────── │  cottonmouth-web  │
│ (CottonMouth SDK)    │                      │  + watcher   │                    │  (Next.js) │
└───────────────┘                      │   PVC /data  │                    └─────┬──────┘
                                       └──────────────┘                          │ Ingress / port-forward
                                                                                 ▼
                                                                              browser
```

## Prerequisites

- An EKS cluster (e.g. spun up in the **Sandbox** account) and `kubectl` context pointing at it.
- Docker, and an image registry the cluster can pull from (ECR recommended).
- `kubectl` with kustomize (built in: `kubectl apply -k`).

## 1. Build & push images

The web image bakes in `BACKEND_ORIGIN` (defaults to `http://cottonmouth-backend:8150`,
which matches the in-cluster Service) and `NEXT_PUBLIC_API_URL=self`. Use the
defaults for Kubernetes.

```bash
export REGION=us-east-1
export ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
export ECR=$ACCOUNT_ID.dkr.ecr.$REGION.amazonaws.com
export TAG=v1

# One-time: create repos
aws ecr create-repository --repository-name cottonmouth-backend --region $REGION || true
aws ecr create-repository --repository-name cottonmouth-web --region $REGION || true

# Login
aws ecr get-login-password --region $REGION | docker login --username AWS --password-stdin $ECR

# Build (linux/amd64 for EKS nodes) and push
docker build --platform linux/amd64 -t $ECR/cottonmouth-backend:$TAG .
docker push $ECR/cottonmouth-backend:$TAG

docker build --platform linux/amd64 -t $ECR/cottonmouth-web:$TAG ./web
docker push $ECR/cottonmouth-web:$TAG
```

> On Apple Silicon, `--platform linux/amd64` matters — EKS nodes are usually x86.

## 2. Point kustomize at your images

Edit `deploy/k8s/kustomization.yaml` `images:` to your ECR repos and tag:

```yaml
images:
  - name: cottonmouth-backend
    newName: <ACCOUNT_ID>.dkr.ecr.us-east-1.amazonaws.com/cottonmouth-backend
    newTag: v1
  - name: cottonmouth-web
    newName: <ACCOUNT_ID>.dkr.ecr.us-east-1.amazonaws.com/cottonmouth-web
    newTag: v1
```

## 3. Deploy

```bash
kubectl apply -k deploy/k8s
kubectl -n cottonmouth rollout status deploy/cottonmouth-backend
kubectl -n cottonmouth rollout status deploy/cottonmouth-web
kubectl -n cottonmouth get pods
```

## 4. Access the dashboard

**With an ingress controller (AWS LB Controller):**

```bash
kubectl -n cottonmouth get ingress cottonmouth-web   # grab the ALB address
```

**Without one (simplest for a sandbox demo):**

```bash
kubectl -n cottonmouth port-forward svc/cottonmouth-web 3000:3000
# open http://localhost:3000
```

You should see agents, traces, and events populating within ~30s (the watcher
ticks every 30s; traces/agents read live).

## Verify the pipeline

```bash
# Backend health
kubectl -n cottonmouth exec deploy/cottonmouth-backend -- \
  python -c "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8150/api/health').read().decode())"

# Sample agent shipping traces
kubectl -n cottonmouth logs deploy/cottonmouth-sample-agent --tail=5
```

## LiteLLM gateway (CORE-10625)

A single shared **LiteLLM proxy** runs in-cluster as the LLM gateway. It holds
the only Bedrock credentials, exposes one OpenAI-format endpoint
(`http://litellm:4000`), and runs the CottonMouth `CustomLogger` so every gateway
call becomes an `llm_call` span in the backend (and the gateway's own denials
become `permission_check`s). Agents drop their direct Bedrock creds and call the
gateway with a key.

```
┌──────────────┐  OpenAI fmt + key   ┌─────────────┐  Bedrock (IRSA/Secret)  ┌─────────┐
│ litellm-agent │ ──────────────────▶ │   litellm    │ ──────────────────────▶ │ Bedrock │
└──────────────┘  metadata.cottonmouth└──────┬──────┘                          └─────────┘
                                             │ CottonmouthLogger callback
                                             ▼  POST /api/spans
                                       cottonmouth-backend
```

Build & push the gateway image (official LiteLLM + the CottonMouth SDK, so the
callback is importable). Build from the **repo root** so `sdk/` is in context:

```bash
aws ecr create-repository --repository-name cottonmouth-litellm --region $REGION || true
docker build --platform linux/amd64 -f deploy/litellm/Dockerfile -t $ECR/cottonmouth-litellm:v1 .
docker push $ECR/cottonmouth-litellm:v1
```

Create the gateway key Secret (not in git), then deploy:

```bash
kubectl -n cottonmouth create secret generic cottonmouth-litellm-secret \
  --from-literal=master-key="sk-$(openssl rand -hex 16)"
kubectl apply -k deploy/k8s
kubectl -n cottonmouth rollout status deploy/cottonmouth-litellm
```

Bedrock creds for the gateway: prefer **IRSA** on the `cottonmouth-litellm`
ServiceAccount (annotate the SA, drop the `cottonmouth-aws-creds` envFrom in
`litellm.yaml`). In this sandbox an SCP blocks IAM role creation, so the gateway
reuses the short-lived `cottonmouth-aws-creds` Secret like the other agents.

Verify the acceptance criteria:

```bash
# Models the gateway exposes
kubectl -n cottonmouth exec deploy/cottonmouth-litellm -- \
  curl -s http://127.0.0.1:4000/v1/models -H "Authorization: Bearer $KEY"

# An in-cluster completion using the gateway's creds (caller has none)
kubectl -n cottonmouth exec deploy/cottonmouth-litellm -- curl -s \
  http://127.0.0.1:4000/v1/chat/completions -H "Authorization: Bearer $KEY" \
  -H 'Content-Type: application/json' \
  -d '{"model":"claude-3-haiku","messages":[{"role":"user","content":"hi"}]}'

# ...and confirm the matching llm_call span landed in the backend
kubectl -n cottonmouth logs deploy/cottonmouth-litellm-agent --tail=5
```

### Per-agent virtual keys (Postgres) + onboarding Cursor

Per-agent **virtual keys, budgets, and spend tracking** are backed by Postgres
(`postgres.yaml` → `litellm-db`). The gateway runs its prisma migrations on
startup; create the DB secret before applying:

```bash
PW=$(openssl rand -hex 16)
kubectl -n cottonmouth create secret generic litellm-db-secret \
  --from-literal=password="$PW" \
  --from-literal=database-url="postgresql://litellm:$PW@litellm-db:5432/litellm"
kubectl apply -k deploy/k8s
kubectl -n cottonmouth rollout status deploy/litellm-db
kubectl -n cottonmouth rollout restart deploy/cottonmouth-litellm   # re-run migrations
```

Mint a key per agent/team — the alias is the identity CottonMouth attributes the
calls to and reconciles on the governance page:

```bash
MK=$(kubectl -n cottonmouth get secret cottonmouth-litellm-secret -o jsonpath='{.data.master-key}' | base64 -d)
kubectl -n cottonmouth exec deploy/cottonmouth-litellm -- curl -s \
  http://127.0.0.1:4000/key/generate -H "Authorization: Bearer $MK" \
  -H 'Content-Type: application/json' \
  -d '{"key_alias":"devils-council","models":["claude-3-haiku","claude-3-sonnet"],"max_budget":50}'
# -> {"key":"sk-...", ...}  hand THIS virtual key (not the master key) to the dev
```

Declare the agent in `agent_policies.json` so it appears on the governance page
with declared-vs-observed reconciliation. The policy is mounted from a ConfigMap
(no image rebuild) — edit the file, then:

```bash
kubectl -n cottonmouth create configmap cottonmouth-policies \
  --from-file=agent_policies.json=agent_policies.json \
  --dry-run=client -o yaml | kubectl apply -f -
kubectl -n cottonmouth rollout restart deploy/cottonmouth-backend
```

**Point Cursor at the gateway.** Expose the gateway externally first — enable
`ingress.yaml` (needs the AWS Load Balancer Controller) for a stable HTTPS URL,
or for a quick local test `kubectl -n cottonmouth port-forward svc/litellm 4000:4000`.
Then in Cursor: **Settings → Models → Override OpenAI Base URL** = `<gateway>/v1`,
**API key** = the virtual key, and add a custom model matching the gateway's names
(`claude-3-haiku` / `claude-3-sonnet`). Every Cursor model call now routes through
LiteLLM and lands in CottonMouth tagged `devils-council`.

> **What you get vs. don't (Cursor built-in agents).** Cursor's agent loop runs
> client-side, so the gateway only sees *model-completion traffic*. CottonMouth
> captures every call (model, tokens, cost, gateway allow/deny) attributed to the
> key alias, and reconciles it on the governance page. It does **not** see Cursor's
> internal decisions/tool steps — those would need MCP routed through the gateway's
> MCP server, or the agent instrumented with the CottonMouth SDK for full
> `agent_run` traces. Gateway-only calls appear as standalone `llm_call` spans, not
> multi-span run waterfalls.

## Optional: Bedrock Investigate (IRSA)

The "Investigate" feature calls AWS Bedrock. To enable it in-cluster:

1. Create an IAM role trusting the cluster's OIDC provider with `bedrock:InvokeModel`
   / `bedrock:Converse` permissions.
2. Set the role ARN on the ServiceAccount in `deploy/k8s/serviceaccount.yaml`:
   ```yaml
   annotations:
     eks.amazonaws.com/role-arn: arn:aws:iam::<ACCOUNT_ID>:role/cottonmouth-bedrock
   ```
3. `kubectl apply -k deploy/k8s` and restart the backend.

The backend uses the default AWS credential chain (no `AWS_PROFILE`), so IRSA
credentials are picked up automatically.

## Instrumenting a real agent

Point any agent at the backend instead of the sample one:

```python
import cottonmouth
cottonmouth.configure(export="http", endpoint="http://cottonmouth-backend:8150")  # or COTTONMOUTH_ENDPOINT env

@cottonmouth.trace_agent(name="my-agent", version="1.0.0")
def run(task): ...
```

`cottonmouth.configure(auto_instrument=True)` also patches the Anthropic/OpenAI/Bedrock
SDKs so LLM calls become child spans automatically.

## Teardown

```bash
kubectl delete -k deploy/k8s
```
