"""Strong-model distillation boundary.

The model is an analyst, never an authority. Providers receive one bounded evidence
packet and must return one structured proposal. No proposal is activated here.
"""

from __future__ import annotations

import json
import subprocess
from typing import Protocol

from .models import EvidencePacket, EvidenceRef, Proposal

SYSTEM_INSTRUCTION = """You analyze coding-agent execution evidence. Return JSON only.
Choose intervention_type from: rule, skill, workflow, tool_fix,
orchestration_change, no_action. Prefer the least restrictive intervention supported
by evidence. Do not claim causal proof from observational data. Required keys:
finding, confidence, root_cause, intervention_type, intervention, why,
expected_effect, validation, evidence_refs. evidence_refs must be a JSON list.
"""


class Provider(Protocol):
    def complete(self, system: str, payload: dict) -> str: ...


class CommandProvider:
    """Provider adapter for any CLI that accepts JSON on stdin and prints text.

    This deliberately avoids importing an SDK. A wrapper can point at OpenAI,
    Anthropic, Google, OpenRouter, or a local model without coupling CallusGuard to
    one vendor.
    """

    def __init__(self, command: list[str]):
        if not command:
            raise ValueError("provider command may not be empty")
        self.command = command

    def complete(self, system: str, payload: dict) -> str:
        request = json.dumps({"system": system, "input": payload})
        proc = subprocess.run(
            self.command,
            input=request,
            text=True,
            capture_output=True,
            check=False,
        )
        if proc.returncode:
            raise RuntimeError("provider command failed: %s" % proc.stderr.strip())
        return proc.stdout.strip()


def parse_proposal(text: str) -> Proposal:
    data = json.loads(text)
    refs = [EvidenceRef(**r) for r in data.get("evidence_refs", [])]
    return Proposal(
        finding=data["finding"],
        confidence=data.get("confidence", "unknown"),
        root_cause=data.get("root_cause"),
        intervention_type=data["intervention_type"],
        intervention=data.get("intervention", ""),
        why=data.get("why", ""),
        expected_effect=data.get("expected_effect"),
        validation=data.get("validation"),
        evidence_refs=refs,
    )


def distill(packet: EvidencePacket, provider: Provider) -> Proposal:
    return parse_proposal(provider.complete(SYSTEM_INSTRUCTION, packet.to_dict()))
