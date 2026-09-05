"""Canonical learning objects shared across Claude, Codex, Gemini, and future hosts."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class EvidenceRef:
    source: str
    session_id: str | None = None
    tool_call_id: str | None = None
    note: str | None = None


@dataclass(frozen=True)
class ToolAttempt:
    source: str
    tool_name: str
    status: str
    command: str | None = None
    error: str | None = None
    session_id: str | None = None
    tool_call_id: str | None = None
    model: str | None = None
    ts: str | None = None


@dataclass
class EvidencePacket:
    question: str
    window: str
    stats: dict[str, Any] = field(default_factory=dict)
    representative_failures: list[dict[str, Any]] = field(default_factory=list)
    representative_successes: list[dict[str, Any]] = field(default_factory=list)
    previous_patterns: list[dict[str, Any]] = field(default_factory=list)
    previous_interventions: list[dict[str, Any]] = field(default_factory=list)
    evidence_refs: list[EvidenceRef] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Pattern:
    id: str
    observation: str
    hypothesis: str | None = None
    status: str = "active"
    evidence_refs: list[EvidenceRef] = field(default_factory=list)
    successful_strategies: list[str] = field(default_factory=list)
    first_seen: str | None = None
    last_seen: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Intervention:
    id: str
    pattern_id: str
    kind: str
    description: str
    status: str = "proposed"
    result: str | None = None
    evidence_refs: list[EvidenceRef] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Proposal:
    finding: str
    confidence: str
    root_cause: str | None
    intervention_type: str
    intervention: str
    why: str
    expected_effect: str | None = None
    validation: str | None = None
    evidence_refs: list[EvidenceRef] = field(default_factory=list)

    ALLOWED_TYPES = {
        "rule",
        "skill",
        "workflow",
        "tool_fix",
        "orchestration_change",
        "no_action",
    }

    def __post_init__(self):
        if self.intervention_type not in self.ALLOWED_TYPES:
            raise ValueError("unsupported intervention_type: %s" % self.intervention_type)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
