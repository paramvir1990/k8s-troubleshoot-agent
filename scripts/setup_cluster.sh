#!/usr/bin/env bash
# Create (or reuse) the local kind cluster this whole project targets, and
# deploy the baseline healthy demo app that the chaos script breaks and the
# agent fixes.
set -euo pipefail

CLUSTER_NAME="k8s-troubleshoot"
CONTEXT="kind-${CLUSTER_NAME}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "${SCRIPT_DIR}")"

if ! command -v kind >/dev/null 2>&1; then
  echo "kind is not installed. Install it first (e.g. 'brew install kind')." >&2
  exit 1
fi
if ! command -v kubectl >/dev/null 2>&1; then
  echo "kubectl is not installed. Install it first (e.g. 'brew install kubectl')." >&2
  exit 1
fi

if kind get clusters 2>/dev/null | grep -qx "${CLUSTER_NAME}"; then
  echo "Cluster '${CLUSTER_NAME}' already exists, reusing it."
else
  echo "Creating kind cluster '${CLUSTER_NAME}'..."
  kind create cluster --name "${CLUSTER_NAME}"
fi

echo "Waiting for node to be Ready..."
kubectl --context "${CONTEXT}" wait --for=condition=Ready node --all --timeout=120s

echo "Applying baseline demo app..."
kubectl --context "${CONTEXT}" apply -f "${PROJECT_ROOT}/manifests/demo-app.yaml"

echo "Waiting for demo app rollout..."
kubectl --context "${CONTEXT}" -n demo rollout status deployment/web --timeout=120s

echo "Cluster ready. Context: ${CONTEXT}"
kubectl --context "${CONTEXT}" -n demo get pods
