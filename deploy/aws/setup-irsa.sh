#!/usr/bin/env bash
# Set up IRSA (IAM Roles for Service Accounts) so the LiteLLM gateway gets
# Bedrock access from its pod identity -- no static AWS credentials, no
# secret to rotate. Renders litellm-irsa-trust.json + litellm-bedrock-policy.json
# for the CURRENT cluster/account and creates the IAM role.
#
# Requires: iam:CreateOpenIDConnectProvider, iam:CreateRole, iam:PutRolePolicy
# (and List/Get equivalents). NOT usable in an account whose Service Control
# Policy explicitly denies iam:CreateRole -- confirmed blocked in the
# Anaconda sandbox account (621741996708) as of 2026-07-14; see README.md in
# this directory. Run this against an account/cluster where IAM writes are
# permitted (e.g. a shared dev/tools cluster), not the sandbox.
#
# Usage: CLUSTER_NAME=<eks-cluster-name> AWS_REGION=us-east-1 ./setup-irsa.sh
set -euo pipefail

CLUSTER_NAME="${CLUSTER_NAME:?Set CLUSTER_NAME to your EKS cluster name}"
AWS_REGION="${AWS_REGION:-us-east-1}"
ROLE_NAME="${ROLE_NAME:-cottonmouth-litellm-bedrock}"
NAMESPACE="cottonmouth"
SERVICE_ACCOUNT="cottonmouth-litellm"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AWS_ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
OIDC_ISSUER="$(aws eks describe-cluster --name "$CLUSTER_NAME" --region "$AWS_REGION" \
  --query "cluster.identity.oidc.issuer" --output text)"
OIDC_PROVIDER="${OIDC_ISSUER#https://}"

echo "Account:      $AWS_ACCOUNT_ID"
echo "Cluster:      $CLUSTER_NAME"
echo "OIDC issuer:  $OIDC_ISSUER"
echo "Role name:    $ROLE_NAME"
echo

# 1. Ensure the cluster's OIDC provider is registered with IAM (idempotent).
if aws iam list-open-id-connect-providers --query "OpenIDConnectProviderList[].Arn" --output text \
    | tr '\t' '\n' | grep -q "$OIDC_PROVIDER"; then
  echo "OIDC provider already registered."
else
  echo "Registering OIDC provider..."
  aws iam create-open-id-connect-provider \
    --url "$OIDC_ISSUER" \
    --client-id-list "sts.amazonaws.com" \
    --thumbprint-list "9e99a48a9960b14926bb7f3b02e22da2b0ab7280"
fi

# 2. Render the trust + permissions policies for this account/cluster/SA.
TRUST_JSON=$(sed -e "s/\${AWS_ACCOUNT_ID}/$AWS_ACCOUNT_ID/g" \
                  -e "s/\${OIDC_PROVIDER}/$OIDC_PROVIDER/g" \
              "$SCRIPT_DIR/litellm-irsa-trust.json")
POLICY_JSON=$(sed -e "s/\${AWS_ACCOUNT_ID}/$AWS_ACCOUNT_ID/g" \
               "$SCRIPT_DIR/litellm-bedrock-policy.json")

# 3. Create (or update) the role.
if aws iam get-role --role-name "$ROLE_NAME" >/dev/null 2>&1; then
  echo "Role $ROLE_NAME exists, updating trust policy..."
  aws iam update-assume-role-policy --role-name "$ROLE_NAME" --policy-document "$TRUST_JSON"
else
  echo "Creating role $ROLE_NAME..."
  aws iam create-role --role-name "$ROLE_NAME" \
    --assume-role-policy-document "$TRUST_JSON" \
    --description "IRSA role for the CottonMouth LiteLLM gateway's Bedrock access"
fi

aws iam put-role-policy --role-name "$ROLE_NAME" \
  --policy-name "BedrockInvoke" \
  --policy-document "$POLICY_JSON"

ROLE_ARN="arn:aws:iam::${AWS_ACCOUNT_ID}:role/${ROLE_NAME}"
echo
echo "Done. Role ARN: $ROLE_ARN"
echo
echo "Next steps:"
echo "  1. Annotate the ServiceAccount in deploy/k8s/litellm.yaml:"
echo "       eks.amazonaws.com/role-arn: $ROLE_ARN"
echo "  2. Remove the envFrom: cottonmouth-aws-creds block from the litellm Deployment."
echo "  3. kubectl apply -k deploy/k8s && kubectl -n $NAMESPACE rollout restart deployment/cottonmouth-litellm"
echo "  4. Delete the cottonmouth-aws-creds Secret and stop rotating SSO credentials into it."
