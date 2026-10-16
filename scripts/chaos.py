#!/usr/bin/env python3
"""Inject one of four core workload failures into the demo app.

Reuses agent.kube.execute_fix for the actual mutation, so chaos injection
gets the same guardrails (namespace/kind allow-list) and automatic backup
as the agent's own fixes -- and `make rollback` undoes chaos the same way
it undoes a bad agent fix.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent import kube

SCENARIOS = {
    "crashloop": {
        "kind": "deployment",
        "name": "web",
        "description": "container command replaced with something that exits immediately (CrashLoopBackOff)",
        "patch": {
            "spec": {
                "template": {
                    "spec": {"containers": [{"name": "web", "command": ["/bin/false"]}]}
                }
            }
        },
    },
    "oom": {
        "kind": "deployment",
        "name": "web",
        "description": "memory limit dropped far below what the container needs (OOMKilled)",
        "patch": {
            "spec": {
                "template": {
                    "spec": {
                        "containers": [
                            {
                                "name": "web",
                                "resources": {"limits": {"memory": "6Mi"}, "requests": {"memory": "6Mi"}},
                            }
                        ]
                    }
                }
            }
        },
    },
    "bad_probe": {
        "kind": "deployment",
        "name": "web",
        "description": "liveness probe pointed at a port nothing listens on (repeated restarts)",
        "patch": {
            "spec": {
                "template": {
                    "spec": {
                        "containers": [
                            {"name": "web", "livenessProbe": {"httpGet": {"path": "/", "port": 9999}}}
                        ]
                    }
                }
            }
        },
    },
    "missing_config": {
        "kind": "deployment",
        "name": "web",
        "description": "env var made to reference a ConfigMap key that doesn't exist (CreateContainerConfigError)",
        "patch": {
            "spec": {
                "template": {
                    "spec": {
                        "containers": [
                            {
                                "name": "web",
                                "env": [
                                    {
                                        "name": "GREETING",
                                        "valueFrom": {
                                            "configMapKeyRef": {"name": "web-config", "key": "MISSING_KEY"}
                                        },
                                    }
                                ],
                            }
                        ]
                    }
                }
            }
        },
    },
}


def inject(scenario_name: str) -> None:
    scenario = SCENARIOS[scenario_name]
    print(f"Injecting '{scenario_name}': {scenario['description']}")
    output = kube.execute_fix(
        scenario["kind"],
        scenario["name"],
        "patch",
        patch=json.dumps(scenario["patch"]),
    )
    print(output)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", choices=sorted(SCENARIOS), required=True)
    args = parser.parse_args()
    inject(args.scenario)


if __name__ == "__main__":
    main()
