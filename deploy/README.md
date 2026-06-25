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
