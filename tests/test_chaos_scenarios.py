"""TDD red/green tests for scripts/chaos.py: each scenario must reliably
produce its expected broken state. Written before chaos.py's implementation
existed, as the first step of the TDD loop for this project.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from conftest import pod_container_statuses, wait_until
from scripts.chaos import inject


def test_crashloop_produces_crashloopbackoff(demo_app):
    inject("crashloop")

    def broken():
        statuses = pod_container_statuses()
        return any(s.get("state", {}).get("waiting", {}).get("reason") == "CrashLoopBackOff" for s in statuses)

    wait_until(broken, timeout=150)


def test_oom_produces_oomkilled(demo_app):
    inject("oom")

    def broken():
        statuses = pod_container_statuses()
        return any(s.get("lastState", {}).get("terminated", {}).get("reason") == "OOMKilled" for s in statuses)

    wait_until(broken, timeout=150)


def test_bad_probe_causes_restarts(demo_app):
    inject("bad_probe")

    def broken():
        statuses = pod_container_statuses()
        return any(s.get("restartCount", 0) >= 1 for s in statuses)

    wait_until(broken, timeout=150)


def test_missing_config_produces_create_container_config_error(demo_app):
    inject("missing_config")

    def broken():
        statuses = pod_container_statuses()
        return any(
            s.get("state", {}).get("waiting", {}).get("reason") == "CreateContainerConfigError" for s in statuses
        )

    wait_until(broken, timeout=90)
