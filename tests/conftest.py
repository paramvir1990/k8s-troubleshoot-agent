import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
load_dotenv(PROJECT_ROOT / ".env")  # ANTHROPIC_API_KEY / KEEP_CLUSTER; shell env wins

from agent import kube  # noqa: E402


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "integration: requires a real ANTHROPIC_API_KEY and calls the live Claude API"
    )


@pytest.fixture(scope="session")
def kind_cluster():
    """Session-scoped: create the kind cluster once for the whole test run."""
    subprocess.run([str(PROJECT_ROOT / "scripts" / "setup_cluster.sh")], check=True)
    yield
    if os.environ.get("KEEP_CLUSTER") != "1":
        subprocess.run(["kind", "delete", "cluster", "--name", "k8s-troubleshoot"], check=False)


@pytest.fixture()
def demo_app(kind_cluster):
    """Function-scoped: force a clean baseline before every test.

    `kubectl apply` alone is not enough here: chaos and fixes both use
    `kubectl patch`, which mutates the live object out-of-band without
    updating the last-applied-config annotation `apply` relies on -- so a
    field like a patched-in `command` would silently survive a plain
    re-apply of the original manifest. Deleting and recreating the
    Deployment/ConfigMap guarantees no drift leaks between tests.
    """
    subprocess.run(
        ["kubectl", "--context", kube.CONTEXT, "-n", "demo", "delete", "deployment", "web", "--ignore-not-found"],
        check=True,
    )
    subprocess.run(
        ["kubectl", "--context", kube.CONTEXT, "-n", "demo", "delete", "configmap", "web-config", "--ignore-not-found"],
        check=True,
    )
    subprocess.run(
        ["kubectl", "--context", kube.CONTEXT, "apply", "-f", str(PROJECT_ROOT / "manifests" / "demo-app.yaml")],
        check=True,
    )
    subprocess.run(
        ["kubectl", "--context", kube.CONTEXT, "-n", "demo", "rollout", "status", "deployment/web", "--timeout=120s"],
        check=True,
    )
    yield


def wait_until(predicate, timeout=90, interval=3):
    """Poll `predicate()` until it returns a truthy value, or raise."""
    deadline = time.time() + timeout
    last_state = None
    while time.time() < deadline:
        last_state = predicate()
        if last_state:
            return last_state
        time.sleep(interval)
    raise AssertionError(f"condition not met within {timeout}s; last observed state: {last_state}")


def pod_container_statuses():
    output = kube.run_readonly(["get", "pods", "-n", "demo", "-l", "app=web", "-o", "json"])
    data = json.loads(output)
    statuses = []
    for pod in data.get("items", []):
        statuses.extend(pod.get("status", {}).get("containerStatuses", []))
    return statuses


def pods_running_and_ready():
    output = kube.run_readonly(["get", "pods", "-n", "demo", "-l", "app=web", "-o", "json"])
    data = json.loads(output)
    pods = data.get("items", [])
    if not pods:
        return False
    for pod in pods:
        if pod.get("status", {}).get("phase") != "Running":
            return False
        for cond in pod.get("status", {}).get("conditions", []):
            if cond.get("type") == "Ready" and cond.get("status") != "True":
                return False
    return True
