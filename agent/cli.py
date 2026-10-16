"""Interactive CLI chat with the Kubernetes troubleshooting agent.

Usage:
    python -m agent.cli [--approval-mode per-fix|plan]

In-chat commands:
    /mode per-fix   switch to confirming every fix individually
    /mode plan      switch to approving one full plan up front
    /exit           quit
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from agent.approval import ApprovalManager
from agent.claude_agent import diagnose_and_fix

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--approval-mode", choices=["per-fix", "plan"], default="per-fix")
    args = parser.parse_args()

    # Shell-exported vars win over .env (override=False is the default).
    load_dotenv(PROJECT_ROOT / ".env")

    if not os.environ.get("ANTHROPIC_API_KEY"):
        print("ANTHROPIC_API_KEY is not set. Add it to .env or export it before running the agent:", file=sys.stderr)
        print("  cp .env.example .env   # then set ANTHROPIC_API_KEY=sk-ant-...", file=sys.stderr)
        sys.exit(1)

    approval = ApprovalManager(mode=args.approval_mode)
    print(f"Kubernetes troubleshooting agent ready. Approval mode: {approval.mode}")
    print("Commands: /mode per-fix | /mode plan | /exit\n")

    while True:
        try:
            user_input = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not user_input:
            continue
        if user_input == "/exit":
            break
        if user_input.startswith("/mode"):
            parts = user_input.split()
            if len(parts) == 2 and parts[1] in ("per-fix", "plan"):
                approval.set_mode(parts[1])
                print(f"Approval mode set to: {approval.mode}")
            else:
                print("Usage: /mode per-fix | /mode plan")
            continue

        reply = diagnose_and_fix(user_input, approval)
        print(f"\nagent> {reply}\n")


if __name__ == "__main__":
    main()
