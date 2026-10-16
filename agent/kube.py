"""Guarded kubectl wrapper.

Every kubectl invocation anywhere in this project (agent, chaos script,
tests) goes through this module so the safety rules apply uniformly and in
exactly one place. A prompt instruction is not a security boundary -- the
checks here are enforced in code regardless of what the model was told.
"""

from __future__ import annotations

import datetime
import json
import subprocess
from pathlib import Path

CONTEXT = "kind-k8s-troubleshoot"
NAMESPACE = "demo"

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKUP_DIR = PROJECT_ROOT / "backups"
AUDIT_LOG = BACKUP_DIR / "audit.log"

READONLY_VERBS = {"get", "describe", "logs", "events", "top", "explain"}

# Structured mutating actions available to the agent's fix tool. No raw
# "apply arbitrary manifest" action is offered -- patch/scale/restart/delete
# on an allow-listed kind/namespace covers every chaos scenario's fix.
FIX_ACTIONS = {"patch", "scale", "restart", "delete"}

ALLOWED_KINDS = {"deployment", "pod", "service", "configmap", "secret", "replicaset"}
FORBIDDEN_KINDS = {
    "namespace",
    "node",
    "persistentvolume",
    "clusterrole",
    "clusterrolebinding",
    "customresourcedefinition",
}


class GuardrailViolation(Exception):
    """Raised when a request fails a safety check, before any kubectl call is made."""


def _base_args() -> list[str]:
    return ["kubectl", "--context", CONTEXT]


def _run(args: list[str]) -> str:
    result = subprocess.run(_base_args() + args, capture_output=True, text=True)
    if result.returncode != 0:
        return f"ERROR: {result.stderr.strip()}"
    return result.stdout


def run_readonly(args: list[str]) -> str:
    """Execute a read-only kubectl command. Auto-executed, no approval needed."""
    if not args or args[0] not in READONLY_VERBS:
        got = args[0] if args else "<empty>"
        raise GuardrailViolation(f"'{got}' is not an allowed read-only verb {sorted(READONLY_VERBS)}")
    return _run(args)


def _check_guardrails(kind: str, name: str, action: str, *, replicas: int | None) -> None:
    kind = kind.lower()
    if action not in FIX_ACTIONS:
        raise GuardrailViolation(f"'{action}' is not an allowed fix action {sorted(FIX_ACTIONS)}")
    if kind in FORBIDDEN_KINDS:
        raise GuardrailViolation(f"resource kind '{kind}' may never be mutated by this agent")
    if kind not in ALLOWED_KINDS:
        raise GuardrailViolation(f"resource kind '{kind}' is not on the allowed list {sorted(ALLOWED_KINDS)}")
    if not name:
        raise GuardrailViolation("a resource name is required")
    if action == "scale":
        if replicas is None:
            raise GuardrailViolation("action 'scale' requires replicas")
        if replicas < 1:
            raise GuardrailViolation("scaling a workload to 0 replicas is never treated as a fix")


def _resource_ref(kind: str, name: str) -> str:
    return f"{kind}/{name}"


def _build_command(kind: str, name: str, action: str, *, patch: str | None, replicas: int | None, dry_run: bool) -> list[str] | None:
    ref = _resource_ref(kind, name)
    ns = ["-n", NAMESPACE]
    if action == "patch":
        if not patch:
            raise GuardrailViolation("action 'patch' requires a JSON patch")
        try:
            json.loads(patch)  # fail fast on malformed patch JSON before it reaches kubectl
        except json.JSONDecodeError as exc:
            raise GuardrailViolation(f"invalid patch JSON: {exc}") from exc
        # No --type flag: kubectl defaults to a strategic merge patch for
        # built-in types, which merges the containers list by name instead
        # of replacing it wholesale (as a JSON Merge Patch would).
        cmd = ["patch", ref, *ns, "-p", patch]
    elif action == "scale":
        cmd = ["scale", ref, *ns, f"--replicas={replicas}"]
    elif action == "delete":
        cmd = ["delete", ref, *ns]
    elif action == "restart":
        if kind != "deployment":
            raise GuardrailViolation("action 'restart' only applies to deployments")
        cmd = ["rollout", "restart", ref, *ns]
        return None if dry_run else cmd  # rollout has no --dry-run support
    else:  # pragma: no cover - guarded by _check_guardrails
        raise GuardrailViolation(f"unhandled action '{action}'")
    if dry_run:
        cmd = cmd + ["--dry-run=server"]
    return cmd


def dry_run_fix(kind: str, name: str, action: str, *, patch: str | None = None, replicas: int | None = None) -> str:
    """Preview a fix without changing the cluster. Raises GuardrailViolation if disallowed."""
    _check_guardrails(kind, name, action, replicas=replicas)
    cmd = _build_command(kind, name, action, patch=patch, replicas=replicas, dry_run=True)
    if cmd is None:
        return "(rollout restart has no dry-run preview -- it only recreates pods from the current spec)"
    return _run(cmd)


def _backup_resource(kind: str, name: str) -> Path | None:
    yaml_output = _run(["get", _resource_ref(kind, name), "-n", NAMESPACE, "-o", "yaml"])
    if yaml_output.startswith("ERROR:"):
        return None
    BACKUP_DIR.mkdir(exist_ok=True)
    timestamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = BACKUP_DIR / f"{timestamp}-{kind}-{name}.yaml"
    backup_path.write_text(yaml_output)
    return backup_path


def _audit(entry: dict) -> None:
    BACKUP_DIR.mkdir(exist_ok=True)
    entry = {"timestamp": datetime.datetime.now().isoformat(), **entry}
    with AUDIT_LOG.open("a") as f:
        f.write(json.dumps(entry) + "\n")


def execute_fix(kind: str, name: str, action: str, *, patch: str | None = None, replicas: int | None = None) -> str:
    """Apply an already-approved fix for real. Takes a backup first (except
    for 'restart', which changes no spec) and always writes an audit entry.
    """
    try:
        _check_guardrails(kind, name, action, replicas=replicas)
    except GuardrailViolation as exc:
        _audit({"kind": kind, "name": name, "action": action, "outcome": "blocked", "reason": str(exc)})
        raise

    backup_path = _backup_resource(kind, name) if action != "restart" else None
    cmd = _build_command(kind, name, action, patch=patch, replicas=replicas, dry_run=False)
    output = _run(cmd)
    _audit(
        {
            "kind": kind,
            "name": name,
            "action": action,
            "patch": patch,
            "replicas": replicas,
            "outcome": "applied" if not output.startswith("ERROR:") else "failed",
            "backup": str(backup_path) if backup_path else None,
        }
    )
    return output


def latest_backup(kind: str, name: str) -> Path | None:
    candidates = sorted(BACKUP_DIR.glob(f"*-{kind}-{name}.yaml"))
    return candidates[-1] if candidates else None


def rollback(kind: str, name: str) -> str:
    backup_path = latest_backup(kind, name)
    if backup_path is None:
        return f"No backup found for {kind}/{name}"
    # `replace --force` (delete + recreate from the backup manifest), not
    # `apply`: both chaos and fixes mutate live objects out-of-band via
    # `kubectl patch`, which never updates the last-applied-config
    # annotation `apply`'s 3-way merge relies on -- so `apply` would leave
    # patched-in fields (e.g. a bad `command`) in place instead of undoing
    # them. `replace --force` guarantees the object matches the backup.
    return _run(["replace", "--force", "-f", str(backup_path)])
