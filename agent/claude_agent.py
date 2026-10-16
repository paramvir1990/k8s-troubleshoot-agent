"""The agent's reasoning core: builds the system prompt, defines its tools,
and runs the manual tool-use loop against the Claude API.

A manual loop (not the beta Tool Runner) is used deliberately -- the
per-fix vs. plan approval modes need custom pause/resume behavior around
one specific tool (apply_fix) that the runner's generic hooks don't fit as
directly, and this keeps the whole loop readable in one place.
"""

from __future__ import annotations

import anthropic

from agent import kube, skill_loader
from agent.approval import ApprovalManager, SessionLimitReached

MODEL = "claude-opus-5"
MAX_TOKENS = 16000

ROLE_INSTRUCTIONS = """You are a Kubernetes troubleshooting specialist operating ONLY on the \
`demo` namespace of a local `kind` cluster. Follow a failure-mode-first workflow, per the \
skill below: gather evidence with kubectl_read (get/describe/logs/events) BEFORE forming a \
hypothesis, form one specific hypothesis about the failure mode, explain it to the user, \
then propose exactly one fix at a time via apply_fix with a clear one-sentence `explanation`.

You cannot bypass approval or scope: apply_fix always requires the user (via the approval \
mode currently in effect) to confirm before anything is applied, and only `deployment`, \
`pod`, `service`, `configmap`, `secret`, and `replicaset` resources in the `demo` namespace \
can ever be touched -- this is enforced in code, not just requested of you. If a guardrail \
blocks a call, do not retry the same call; explain the restriction to the user instead.
"""

TOOLS = [
    {
        "name": "kubectl_read",
        "description": (
            "Run a read-only kubectl command against the demo cluster "
            "(get, describe, logs, events, top, explain). Executes immediately, no approval needed."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "args": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": 'kubectl args after "kubectl", e.g. ["get", "pods", "-n", "demo"]',
                }
            },
            "required": ["args"],
        },
    },
    {
        "name": "propose_plan",
        "description": (
            "Only used in PLAN approval mode: propose an ordered list of fixes before applying "
            "any of them. The user approves the whole batch once; do not call this in PER-FIX mode."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"steps": {"type": "array", "items": {"type": "string"}}},
            "required": ["steps"],
        },
    },
    {
        "name": "apply_fix",
        "description": (
            "Propose and, if approved, apply a fix to one resource in the demo namespace. "
            "Always shows a dry-run preview and requires human approval before anything is applied."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": sorted(kube.ALLOWED_KINDS)},
                "name": {"type": "string", "description": "resource name, e.g. 'web'"},
                "action": {"type": "string", "enum": sorted(kube.FIX_ACTIONS)},
                "patch": {
                    "type": "string",
                    "description": "JSON strategic-merge-patch body, required for action='patch'",
                },
                "replicas": {"type": "integer", "description": "required for action='scale'"},
                "explanation": {
                    "type": "string",
                    "description": "one sentence: what's broken and why this fixes it",
                },
            },
            "required": ["kind", "name", "action", "explanation"],
        },
    },
]


def _build_system(approval: ApprovalManager) -> list[dict]:
    skill_text = skill_loader.load_skill_text()
    static_block = {
        "type": "text",
        "text": ROLE_INSTRUCTIONS + "\n\n## Kubernetes troubleshooting skill (vendored)\n\n" + skill_text,
        "cache_control": {"type": "ephemeral"},
    }
    if approval.mode == "plan":
        mode_text = (
            "Current approval mode: PLAN. Before calling apply_fix, first call propose_plan "
            "with the ordered list of fixes you intend to make, and wait for it to be approved."
        )
    else:
        mode_text = (
            "Current approval mode: PER-FIX. Call apply_fix one fix at a time; each call is "
            "confirmed individually, so do not call propose_plan."
        )
    return [static_block, {"type": "text", "text": mode_text}]


def _execute_tool(name: str, tool_input: dict, approval: ApprovalManager) -> tuple[str, bool]:
    """Returns (content, is_error)."""
    try:
        if name == "kubectl_read":
            return kube.run_readonly(tool_input["args"]), False

        if name == "propose_plan":
            approved = approval.approve_plan(tool_input["steps"])
            if approved:
                return "Plan approved by the user.", False
            return "User rejected the plan. Ask what they'd like to do instead.", True

        if name == "apply_fix":
            kind = tool_input["kind"]
            res_name = tool_input["name"]
            action = tool_input["action"]
            patch = tool_input.get("patch")
            replicas = tool_input.get("replicas")
            explanation = tool_input.get("explanation", "")

            dry_run_output = kube.dry_run_fix(kind, res_name, action, patch=patch, replicas=replicas)
            description = f"{action} {kind}/{res_name} -- {explanation}"

            if not approval.confirm(description, dry_run_output):
                return "User declined this fix. Do not retry the same call; ask what they'd like instead.", True

            return kube.execute_fix(kind, res_name, action, patch=patch, replicas=replicas), False

        return f"Unknown tool '{name}'", True

    except kube.GuardrailViolation as exc:
        return f"Blocked by guardrail: {exc}", True
    except SessionLimitReached as exc:
        return f"Stopped: {exc}", True


def diagnose_and_fix(user_message: str, approval: ApprovalManager) -> str:
    """Run the agent to completion on one user message and return its final reply.

    This is the one function the CLI and the tests both call -- tests pass an
    ApprovalManager with auto_approve=True so no interactive input is needed.
    """
    client = anthropic.Anthropic()
    messages: list[dict] = [{"role": "user", "content": user_message}]

    while True:
        response = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=_build_system(approval),
            tools=TOOLS,
            messages=messages,
        )

        if response.stop_reason == "pause_turn":
            messages.append({"role": "assistant", "content": response.content})
            continue

        tool_use_blocks = [b for b in response.content if b.type == "tool_use"]
        if not tool_use_blocks:
            break

        messages.append({"role": "assistant", "content": response.content})
        tool_results = []
        for block in tool_use_blocks:
            content, is_error = _execute_tool(block.name, block.input, approval)
            result = {"type": "tool_result", "tool_use_id": block.id, "content": content}
            if is_error:
                result["is_error"] = True
            tool_results.append(result)
        messages.append({"role": "user", "content": tool_results})

    return "\n".join(b.text for b in response.content if b.type == "text")
