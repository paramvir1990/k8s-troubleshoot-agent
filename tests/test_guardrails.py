"""Unit tests for agent/kube.py's safety checks. No cluster required --
these exercise the guardrails directly (mocking the one function, _run,
that would actually shell out to kubectl).
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent import kube


def test_run_readonly_rejects_mutating_verb():
    with pytest.raises(kube.GuardrailViolation):
        kube.run_readonly(["delete", "pod", "web"])


def test_run_readonly_allows_read_verbs(monkeypatch):
    monkeypatch.setattr(kube, "_run", lambda args: "ok")
    assert kube.run_readonly(["get", "pods", "-n", "demo"]) == "ok"


@pytest.mark.parametrize("kind", ["namespace", "node", "persistentvolume", "clusterrole"])
def test_forbidden_kinds_are_rejected(kind):
    with pytest.raises(kube.GuardrailViolation):
        kube.dry_run_fix(kind, "x", "delete")


def test_kind_outside_allowlist_is_rejected():
    with pytest.raises(kube.GuardrailViolation):
        kube.dry_run_fix("ingress", "x", "delete")


def test_action_outside_allowlist_is_rejected():
    with pytest.raises(kube.GuardrailViolation):
        kube.dry_run_fix("deployment", "web", "apply")


def test_scale_to_zero_is_rejected():
    with pytest.raises(kube.GuardrailViolation):
        kube.dry_run_fix("deployment", "web", "scale", replicas=0)


def test_scale_requires_replicas():
    with pytest.raises(kube.GuardrailViolation):
        kube.dry_run_fix("deployment", "web", "scale")


def test_patch_requires_a_patch_body():
    with pytest.raises(kube.GuardrailViolation):
        kube.dry_run_fix("deployment", "web", "patch")


def test_patch_rejects_malformed_json():
    with pytest.raises(kube.GuardrailViolation):
        kube.dry_run_fix("deployment", "web", "patch", patch="{not json")


def test_restart_only_applies_to_deployments():
    with pytest.raises(kube.GuardrailViolation):
        kube.dry_run_fix("pod", "web", "restart")


def test_valid_patch_reaches_kubectl(monkeypatch):
    monkeypatch.setattr(kube, "_run", lambda args: "dry run ok")
    output = kube.dry_run_fix("deployment", "web", "patch", patch=json.dumps({"spec": {}}))
    assert output == "dry run ok"


def test_restart_has_no_dry_run_subprocess_call(monkeypatch):
    def fail(_args):
        raise AssertionError("restart dry-run should not call kubectl")

    monkeypatch.setattr(kube, "_run", fail)
    output = kube.dry_run_fix("deployment", "web", "restart")
    assert "no dry-run preview" in output
