"""Loads the vendored KubeShark skill content that grounds the agent's
system prompt in a real, hallucination-resistant Kubernetes troubleshooting
workflow instead of the model's unaided judgment.
"""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SKILL_PATH = PROJECT_ROOT / "skills" / "kubernetes-skill" / "SKILL.md"


class SkillNotVendoredError(Exception):
    pass


def load_skill_text() -> str:
    if not SKILL_PATH.exists():
        raise SkillNotVendoredError(
            f"{SKILL_PATH} not found. Run `make skill` (or `scripts/install_skill.sh`) first."
        )
    return SKILL_PATH.read_text()
