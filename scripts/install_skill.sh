#!/usr/bin/env bash
# Vendor the KubeShark kubernetes-skill so agent/skill_loader.py can ground
# the agent's system prompt in it. Plain git clone (not `npx skills add`) --
# node/npm/npx are not available on this machine.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "${SCRIPT_DIR}")"
SKILL_DIR="${PROJECT_ROOT}/skills/kubernetes-skill"
REPO_URL="https://github.com/LukasNiessen/kubernetes-skill.git"

if ! command -v git >/dev/null 2>&1; then
  echo "git is not installed." >&2
  exit 1
fi

mkdir -p "${PROJECT_ROOT}/skills"

if [ -d "${SKILL_DIR}/.git" ]; then
  echo "Skill already vendored, pulling latest..."
  git -C "${SKILL_DIR}" pull --ff-only
else
  echo "Cloning ${REPO_URL}..."
  git clone --depth 1 "${REPO_URL}" "${SKILL_DIR}"
fi

if [ ! -f "${SKILL_DIR}/SKILL.md" ]; then
  echo "Warning: SKILL.md not found at ${SKILL_DIR}/SKILL.md -- agent/skill_loader.py will fail to load it." >&2
fi

echo "Skill vendored at ${SKILL_DIR}"
