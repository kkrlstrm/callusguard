"""The decision object wroteonly emits.

wroteonly does not define a verdict vocabulary. It adopts the one from Microsoft's
Agent Control Specification (ACS), shipped in `microsoft/agent-governance-toolkit`
under MIT, so that a wroteonly result is readable by anything that already speaks ACS.

    ACS 0.3.1-beta:  decision ∈ {allow, deny, warn, escalate, transform}
    verdict members: decision (required), reason, message, transform,
                     evidence, result_labels

wroteonly never rewrites the artifact it is checking, so it never emits `transform`.
It uses four of the five: allow / warn / deny / escalate.

WHY WE ADOPT RATHER THAN DEFINE
    Nine closed-source vendors independently invented nine incompatible graded
    verdict vocabularies (see docs/evidence-corpus.md §2.2). Publishing a tenth is
    how that ends badly. ACS is public, formally specified, and has the transform
    outcome none of the nine shared. Being its consumer is worth more than being
    its rival.

THE ONE PLACE WE DEVIATE FROM ACS, AND WHY
    ACS mandates a fail-closed runtime: "Any error during evaluation MUST yield a
    `deny` verdict." That is correct for a policy engine that owns enforcement.
    wroteonly does not own enforcement — it wraps somebody's agent, and a bug in a
    verification wrapper must never wedge a session that was otherwise fine.

    So: a VIOLATED DECLARATION always fails closed (that is the entire point). A
    TOOLING error fails in the direction the caller declared, defaulting to `warn`,
    and it is never silent — `degraded` is set on the evidence and the reason names
    what broke. Silent fail-open is the failure mode this design exists to prevent.

    Deviating reasons live in the `wroteonly:` namespace so they can never collide
    with an ACS `runtime_error:` identifier.

INVARIANT
    A Verdict is immutable once built, and `to_dict()` output is ACS-shaped: no
    wroteonly-specific key ever appears at the top level. Everything we add lives
    under `evidence`, which ACS defines as opaque to the runtime.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# ACS decision vocabulary (spec 0.3.1-beta, section: Verdict)
# ---------------------------------------------------------------------------

ALLOW = "allow"
WARN = "warn"
DENY = "deny"
ESCALATE = "escalate"
TRANSFORM = "transform"  # defined by ACS; never emitted by wroteonly

ACS_DECISIONS = (ALLOW, DENY, WARN, ESCALATE, TRANSFORM)

#: Emitted by wroteonly, worst last. Rank is used for most-restrictive-wins
#: composition when several checks produce verdicts for one run.
#:
#: `escalate` outranks `warn` (a human is being asked to look, which is a stronger
#: statement than a note) but not `deny` (the run is already refused). This
#: ordering is ours; ACS specifies no combining algorithm because it evaluates
#: "one intervention point of one agent at a time" and never has two verdicts to
#: combine. See docs/existence-checks-2026-08-14.md §A.3 item 2.
DECISION_RANK = {ALLOW: 0, WARN: 1, ESCALATE: 2, DENY: 3}

# ---------------------------------------------------------------------------
# Reason identifiers
# ---------------------------------------------------------------------------

#: Our namespace. Never collides with ACS `runtime_error:*`.
R_NO_VIOLATION = "wroteonly:clean"
R_UNDECLARED_WRITE = "wroteonly:undeclared_write"
R_UNDECLARED_DELETE = "wroteonly:undeclared_delete"
R_FORBIDDEN_PATH = "wroteonly:forbidden_path"
R_NEW_ERRORS = "wroteonly:new_errors"
R_NOTHING_DECLARED = "wroteonly:no_declaration"
R_UNFULFILLED = "wroteonly:declared_but_unwritten"

#: Tooling failures — these are the ones that honour `fail_direction`.
R_CHECK_FAILED = "wroteonly:check_command_failed"
R_BASELINE_MISSING = "wroteonly:baseline_unavailable"
R_OBSERVE_FAILED = "wroteonly:observation_failed"

TOOLING_REASONS = (R_CHECK_FAILED, R_BASELINE_MISSING, R_OBSERVE_FAILED)


@dataclass(frozen=True)
class Verdict:
    """An ACS-shaped decision.

    `evidence` is where every wroteonly-specific detail goes — ACS defines it as
    opaque to the runtime and explicitly does not validate it, which makes it the
    correct home for the declared/actual diff and the new-error list.
    """

    decision: str
    reason: str = ""
    message: str = ""
    evidence: dict = field(default_factory=dict)
    result_labels: tuple = ()

    def __post_init__(self) -> None:
        if self.decision not in ACS_DECISIONS:
            raise ValueError(
                "decision %r is not an ACS decision; expected one of %s"
                % (self.decision, ", ".join(ACS_DECISIONS))
            )

    # -- properties ---------------------------------------------------------

    @property
    def blocking(self) -> bool:
        """True when the caller should stop the run rather than let it finish."""
        return self.decision == DENY

    @property
    def degraded(self) -> bool:
        """True when this verdict was reached without complete information."""
        return bool(self.evidence.get("degraded"))

    # -- serialisation ------------------------------------------------------

    def to_dict(self) -> dict:
        """ACS-shaped dict. Optional members are omitted when empty, per spec."""
        out: dict = {"decision": self.decision}
        if self.reason:
            out["reason"] = self.reason
        if self.message:
            out["message"] = self.message
        if self.evidence:
            out["evidence"] = self.evidence
        if self.result_labels:
            out["result_labels"] = list(self.result_labels)
        return out

    def to_json(self, indent: int | None = None) -> str:
        return json.dumps(self.to_dict(), indent=indent, sort_keys=True)


def worst(verdicts) -> Verdict:
    """Most-restrictive-wins composition.

    The house pattern, independently arrived at three times: agent-guard's
    ACTION_RANK, kgg's IntEnum, and Openbox's HALT > BLOCK > REQUIRE_APPROVAL >
    ALLOW. XACML calls it `deny-overrides`.

    Ties are broken by order — the first verdict of the winning rank wins, so a
    caller can express priority by ordering its checks. Returns a clean `allow`
    when given nothing, because "no check ran" is not a violation; a caller that
    wants "no check ran" to be an error should assert on the check list, not on
    this function.
    """
    verdicts = list(verdicts)
    if not verdicts:
        return Verdict(ALLOW, R_NO_VIOLATION, "No checks ran.")
    return max(verdicts, key=lambda v: DECISION_RANK.get(v.decision, 0))


def tooling_failure(reason: str, message: str, fail_direction: str) -> Verdict:
    """Build the verdict for a wroteonly-internal failure.

    This is the deviation from ACS documented in the module docstring, and the one
    place where `fail_direction` is consulted. `degraded` is always set: a
    fail-open that nobody can see is indistinguishable from a pass, which is the
    exact accident Galtea's `narrativeGeneratedByAi` flag exists to prevent
    (docs/evidence-corpus.md §2.4).
    """
    if reason not in TOOLING_REASONS:
        raise ValueError("%r is not a tooling reason" % (reason,))
    decision = DENY if fail_direction == "closed" else WARN
    return Verdict(
        decision=decision,
        reason=reason,
        message=message,
        evidence={"degraded": True, "fail_direction": fail_direction},
    )
