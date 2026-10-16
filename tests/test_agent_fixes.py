"""Integration tests: chaos breaks the demo app, the agent fixes it for
real via the live Claude API. Requires ANTHROPIC_API_KEY. Excluded from a
plain `pytest`/`make test` run -- run explicitly with `make test-integration`.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.approval import ApprovalManager
from agent.claude_agent import diagnose_and_fix
from conftest import pods_running_and_ready, wait_until
from scripts.chaos import SCENARIOS, inject

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("scenario", sorted(SCENARIOS))
def test_agent_fixes_scenario(demo_app, scenario):
    inject(scenario)

    approval = ApprovalManager(mode="per-fix", auto_approve=True)
    diagnose_and_fix(
        "Something is wrong with the 'web' deployment in the demo namespace. Diagnose and fix it.",
        approval,
    )

    wait_until(pods_running_and_ready, timeout=180)
