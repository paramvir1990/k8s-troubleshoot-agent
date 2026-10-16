"""Human approval gate for mutating fixes.

Two modes, selectable at runtime: confirm every fix individually ("per-fix"),
or approve one full plan up front and let its steps apply without
re-prompting ("plan"). Either way, a hard session-wide cap on the number of
applied mutations stops a misbehaving loop from compounding changes
indefinitely -- this is a guardrail, not just a UX choice.
"""

MAX_MUTATIONS = 10
MODES = ("per-fix", "plan")


class SessionLimitReached(Exception):
    pass


class ApprovalManager:
    def __init__(self, mode: str = "per-fix", auto_approve: bool = False):
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}")
        self.mode = mode
        self.auto_approve = auto_approve
        self.plan_approved = False
        self.applied_count = 0

    def set_mode(self, mode: str) -> None:
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}")
        self.mode = mode
        self.plan_approved = False

    def approve_plan(self, steps: list[str]) -> bool:
        if self.auto_approve:
            self.plan_approved = True
            return True
        print("\nProposed plan:")
        for i, step in enumerate(steps, 1):
            print(f"  {i}. {step}")
        answer = input("Approve this plan? [y/N] ").strip().lower()
        self.plan_approved = answer == "y"
        return self.plan_approved

    def confirm(self, description: str, dry_run_output: str) -> bool:
        if self.applied_count >= MAX_MUTATIONS:
            raise SessionLimitReached(
                f"Reached the {MAX_MUTATIONS}-mutation limit for this session. "
                "Start a new session to continue."
            )

        if self.auto_approve:
            self.applied_count += 1
            return True

        if self.mode == "plan" and self.plan_approved:
            print(f"\n[plan already approved] applying: {description}")
            self.applied_count += 1
            return True

        print(f"\nProposed fix: {description}")
        print(f"Dry run:\n{dry_run_output}")
        answer = input("Apply this fix? [y/N] ").strip().lower()
        approved = answer == "y"
        if approved:
            self.applied_count += 1
        return approved
