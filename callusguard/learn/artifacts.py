"""Render reviewable outputs from learned proposals."""

from __future__ import annotations

import json
import re
from pathlib import Path

from .models import Pattern, Proposal


def _slug(text: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return value or "learned-skill"


def write_skill(proposal: Proposal, pattern: Pattern, root: str | Path) -> Path:
    if proposal.intervention_type != "skill":
        raise ValueError("proposal is not a skill")
    skill_dir = Path(root) / _slug(pattern.id)
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        "# %s\n\n%s\n\n## Why\n%s\n" % (
            pattern.id.replace("-", " ").title(),
            proposal.intervention.strip(),
            proposal.why.strip(),
        ),
        encoding="utf-8",
    )
    (skill_dir / "PURPOSE.md").write_text(
        "# Purpose and provenance\n\n"
        "Pattern: `%s`\n\nObservation: %s\n\nHypothesis: %s\n\n"
        "Expected effect: %s\n\nValidation: %s\n" % (
            pattern.id,
            pattern.observation,
            pattern.hypothesis or "unknown",
            proposal.expected_effect or "unspecified",
            proposal.validation or "human review required",
        ),
        encoding="utf-8",
    )
    return skill_dir


def write_proposal(proposal: Proposal, path: str | Path) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(proposal.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return target
