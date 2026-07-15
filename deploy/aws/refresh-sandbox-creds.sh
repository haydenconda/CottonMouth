#!/usr/bin/env bash
# Interim mitigation for the sandbox account's SCP blocking IRSA (see README.md
# in this directory): refresh the AWS SSO session and push fresh short-lived
# credentials into the cottonmouth-aws-creds Secret, then restart the gateway
# so it picks them up. Run this whenever the gateway starts throwing
# "security token included in the request is expired" (Bedrock calls failing).
#
# Usage: AWS_PROFILE=sandbox ./refresh-sandbox-creds.sh
set -euo pipefail

AWS_PROFILE="${AWS_PROFILE:?Set AWS_PROFILE to your sandbox SSO profile}"
NAMESPACE="${NAMESPACE:-cottonmouth}"
export AWS_PROFILE

echo "Checking SSO session..."
if ! aws sts get-caller-identity >/dev/null 2>&1; then
  echo "SSO session expired or missing -- logging in (opens a browser)..."
  aws sso login --profile "$AWS_PROFILE"
fi

echo "Exporting fresh short-lived credentials..."
CREDS=$(aws configure export-credentials --profile "$AWS_PROFILE")
AKID=$(echo "$CREDS" | python3 -c "import json,sys; print(json.load(sys.stdin)['AccessKeyId'])")
SECRET=$(echo "$CREDS" | python3 -c "import json,sys; print(json.load(sys.stdin)['SecretAccessKey'])")
TOKEN=$(echo "$CREDS" | python3 -c "import json,sys; print(json.load(sys.stdin)['SessionToken'])")

echo "Updating cottonmouth-aws-creds Secret in namespace $NAMESPACE..."
kubectl -n "$NAMESPACE" create secret generic cottonmouth-aws-creds \
  --from-literal=AWS_ACCESS_KEY_ID="$AKID" \
  --from-literal=AWS_SECRET_ACCESS_KEY="$SECRET" \
  --from-literal=AWS_SESSION_TOKEN="$TOKEN" \
  --dry-run=client -o yaml | kubectl apply -f -

echo "Restarting the gateway to pick up the new credentials..."
kubectl -n "$NAMESPACE" rollout restart deployment/cottonmouth-litellm
kubectl -n "$NAMESPACE" rollout status deployment/cottonmouth-litellm --timeout=120s

echo "Done. Credentials refreshed -- this is only good for the SSO session's"
echo "lifetime (~1h); re-run this script when Bedrock calls start failing"
echo "with an expired-token error, or set up IRSA (see README.md) in any"
echo "account without the sandbox's IAM-write SCP."
