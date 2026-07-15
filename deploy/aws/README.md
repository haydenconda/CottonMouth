# IRSA for the LiteLLM gateway's Bedrock access

The LiteLLM gateway is the **only** thing in CottonMouth that needs AWS
credentials (to call Bedrock). Agents route through it and hold no provider
credentials of their own. There are two ways to give the gateway pod that
access:

| Mode | How | Rotation |
| --- | --- | --- |
| **IRSA** (preferred) | `eks.amazonaws.com/role-arn` annotation on the `cottonmouth-litellm` ServiceAccount | None -- pod identity, no secret |
| **Static creds** (sandbox fallback) | `cottonmouth-aws-creds` Secret + `envFrom` on the Deployment | Manual, every ~1h (SSO token lifetime) |

## Files

- `litellm-bedrock-policy.json` -- IAM permissions policy: Bedrock `Invoke*`/`Converse*` only, scoped to Anthropic foundation models + this account's inference profiles.
- `litellm-irsa-trust.json` -- trust policy template, restricting `AssumeRoleWithWebIdentity` to the `cottonmouth-litellm` ServiceAccount specifically (not any pod in the cluster).
- `setup-irsa.sh` -- renders both templates for the current account/cluster and creates the IAM role. Idempotent.

## Known blocker: Anaconda sandbox account

Confirmed **2026-07-14** against sandbox account `621741996708`:

```
aws iam create-role --role-name cottonmouth-irsa-feasibility-test ...
AccessDenied: ... with an explicit deny in a service control policy:
arn:aws:organizations::982534379572:policy/o-ln8pshp5lv/service_control_policy/p-hhe2a0ko
```

This SCP denies `iam:CreateRole` account-wide, so **IRSA cannot be set up in
the sandbox account** -- this is the same SCP that blocks `LoadBalancer`/ALB
provisioning (see CORE-10693). `setup-irsa.sh` will fail there too.

**Until CottonMouth runs in an account without this SCP** (e.g. a shared
dev/tools cluster via CORE-10693's ArgoCD path), the sandbox fallback
(static creds, manually refreshed) is the only option -- this has been the
single biggest recurring source of "the gateway broke" during demos. Use
`refresh-sandbox-creds.sh` instead of doing this by hand:

```bash
AWS_PROFILE=sandbox ./refresh-sandbox-creds.sh
```

It refreshes the SSO session (logging in again if needed), pushes fresh
short-lived credentials into the `cottonmouth-aws-creds` Secret, and
restarts the gateway. Re-run it whenever Bedrock calls start failing with
an expired-token error (roughly hourly, since that's the SSO session
lifetime) -- this doesn't remove the toil, but it collapses it to one
command instead of the multi-step manual process from past sessions.

## Applying IRSA once it's available

```bash
CLUSTER_NAME=<your-cluster> AWS_REGION=us-east-1 ./setup-irsa.sh
```

Then, in `deploy/k8s/litellm.yaml`:
1. Uncomment/add `eks.amazonaws.com/role-arn: <output ARN>` on the `cottonmouth-litellm` ServiceAccount.
2. Remove the `envFrom: cottonmouth-aws-creds` block from the litellm container.
3. `kubectl apply -k deploy/k8s && kubectl -n cottonmouth rollout restart deployment/cottonmouth-litellm`
4. Delete the `cottonmouth-aws-creds` Secret.
