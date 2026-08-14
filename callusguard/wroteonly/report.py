"""The diff, and the verdict it produces.

Brings the three pieces together: what was declared (declare), what actually
happened (observe), what newly broke (baseline) — and reduces them to one
ACS-shaped decision plus a report a human can read without a decoder ring.

DECISION TABLE

    condition                                        decision   reason
    ---------------------------------------------------------------------------
    wrote/deleted a path matching `forbid`           deny       forbidden_path
    wrote a path outside the declaration             deny       undeclared_write
    deleted a path outside the declaration           deny       undeclared_delete
    new checker findings absent from the baseline    warn*      new_errors
    declared a path and never wrote it               warn*      declared_but_unwritten
    nothing declared, but writes happened            escalate   no_declaration
    clean                                            allow      clean

    * escalates to `deny` under `fail_direction: closed`, because on a closed
      posture "something broke and I cannot prove it was pre-existing" is not a
      thing to wave through.

    Composition is most-restrictive-wins (verdict.worst) — the house pattern, and
    XACML's `deny-overrides`.

WHY `escalate` FOR A MISSING DECLARATION
    "The agent never said what it would do" is not the same failure as "the agent
    did something it didn't say." The first means the tool was not driven properly
    and a human should look; the second means the agent exceeded its brief. Folding
    them together would make every un-instrumented run look like a violation, and
    the tool would be turned off within a week.

INVARIANT
    Every path in the evidence is relative to the run root and POSIX-separated, so a
    report is diffable across machines. Nothing here re-reads the filesystem; it
    operates only on what the observers already recorded.
"""

from __future__ import annotations

from ..core import verdict as V
from .declare import Declaration, normalize_path
from .observe import CREATED, DELETED, MODIFIED


def _kind_label(kind: str) -> str:
    return {CREATED: "created", MODIFIED: "modified", DELETED: "deleted"}.get(
        kind, kind)


def classify(declaration: Declaration, changes: list, root: str) -> dict:
    """Split observed changes into permitted / violating / escaped.

    `escaped` holds paths that resolved outside the run root. They can never match
    a declaration pattern, so reporting them as merely "undeclared" would understate
    what happened — a write outside the tree is a different and larger event.
    """
    permitted, violations, escaped = [], [], []

    for change in changes:
        raw_path = change["path"]
        rel = normalize_path(raw_path, root)
        if rel is None:
            escaped.append(dict(change, reason="outside run root"))
            continue

        record = dict(change)
        record["path"] = rel
        ok, why = declaration.permits(rel, change["kind"])
        record["rule"] = why
        (permitted if ok else violations).append(record)

    return {"permitted": permitted, "violations": violations, "escaped": escaped}


def unfulfilled(declaration: Declaration, changes: list) -> list:
    """Declared patterns that nothing matched.

    Usually benign — an agent that declared three files and needed two is not
    misbehaving. It matters when a declaration is used as a completion contract, so
    it is reported always and only *enforced* under `strict_unfulfilled`.

    Patterns containing a wildcard are skipped: `src/**/*.py` describes a space, not
    a promise, and "you declared a glob and matched nothing" is not a useful finding.
    """
    touched = {c["path"] for c in changes}
    missing = []
    for bucket, patterns in (("create", declaration.create),
                             ("modify", declaration.modify),
                             ("delete", declaration.delete)):
        for pattern in patterns:
            if any(ch in pattern for ch in "*?["):
                continue
            if pattern not in touched:
                missing.append({"pattern": pattern, "bucket": bucket})
    return missing


def build(
    declaration: Declaration,
    changes: list,
    root: str,
    new_by_check: dict | None = None,
    degraded: list | None = None,
    scan_ok: bool = True,
    scan_error: str = "",
) -> tuple:
    """Produce (Verdict, report_dict).

    The report dict is the human-facing artifact and the verdict's `evidence` at
    once — ACS treats `evidence` as opaque, so we put the whole diff there rather
    than inventing top-level keys it would not recognise.
    """
    new_by_check = new_by_check or {}
    degraded = degraded or []
    verdicts = []

    split = classify(declaration, changes, root)
    missing = unfulfilled(declaration, changes)

    # When nothing was declared, every change is trivially "undeclared" — but
    # reporting them as violations would make an un-instrumented run indistinguishable
    # from a run that exceeded its brief, and the tool gets switched off. A
    # declaration that carries ONLY `forbid` is still load-bearing, though, so those
    # violations survive: "I am not declaring a write set, but never touch these."
    if declaration.declares_anything:
        violations = split["violations"] + split["escaped"]
    else:
        violations = [v for v in split["violations"]
                      if str(v.get("rule", "")).startswith("forbid:")]
        violations += split["escaped"]

    report = {
        "run_id": declaration.run_id,
        "intent": declaration.intent,
        "root": root,
        "declared": declaration.to_dict(),
        "counts": {
            "changed": len(changes),
            "permitted": len(split["permitted"]),
            "violations": len(violations),
            "new_findings": sum(len(v) for v in new_by_check.values()),
        },
        "permitted": split["permitted"],
        "violations": violations,
        "escaped": split["escaped"],
        "unfulfilled": missing,
        "new_findings": {
            name: [f.to_dict() for f in findings]
            for name, findings in new_by_check.items()
        },
        "degraded": degraded,
    }

    closed = declaration.fail_direction == "closed"

    # -- the scan itself failed --------------------------------------------
    if not scan_ok:
        verdicts.append(V.tooling_failure(
            V.R_OBSERVE_FAILED,
            "Could not determine the actual write set: %s" % (scan_error or "unknown"),
            declaration.fail_direction))

    # -- nothing was declared ----------------------------------------------
    if not declaration.declares_anything:
        if changes:
            # `escalate` by default — a human should look. Under a closed posture
            # this becomes `deny`, which is how a caller says "an undeclared run is
            # itself the failure".
            verdicts.append(V.Verdict(
                V.DENY if closed else V.ESCALATE, V.R_NOTHING_DECLARED,
                "%d file(s) changed but the run declared no intended write set."
                % len(changes),
                evidence={"changed": [c["path"] for c in changes][:50]}))
        else:
            verdicts.append(V.Verdict(
                V.ALLOW, V.R_NO_VIOLATION, "No declaration, and nothing changed."))

    # -- violations ---------------------------------------------------------
    forbidden = [v for v in violations
                 if str(v.get("rule", "")).startswith("forbid:")]
    deletes = [v for v in violations
               if v.get("kind") == DELETED and v not in forbidden]
    writes = [v for v in violations if v not in forbidden and v not in deletes]

    if forbidden:
        verdicts.append(V.Verdict(
            V.DENY, V.R_FORBIDDEN_PATH,
            "Touched %d forbidden path(s): %s"
            % (len(forbidden), ", ".join(v["path"] for v in forbidden[:5])),
            evidence={"paths": [v["path"] for v in forbidden]}))

    if deletes:
        verdicts.append(V.Verdict(
            V.DENY, V.R_UNDECLARED_DELETE,
            "Deleted %d undeclared path(s): %s"
            % (len(deletes), ", ".join(v["path"] for v in deletes[:5])),
            evidence={"paths": [v["path"] for v in deletes]}))

    if writes:
        verdicts.append(V.Verdict(
            V.DENY, V.R_UNDECLARED_WRITE,
            "Wrote %d path(s) outside the declaration: %s"
            % (len(writes), ", ".join(v["path"] for v in writes[:5])),
            evidence={"paths": [v["path"] for v in writes]}))

    # -- newly-introduced checker findings ----------------------------------
    if new_by_check:
        total = sum(len(v) for v in new_by_check.values())
        sample = []
        for name, findings in new_by_check.items():
            for finding in findings[:3]:
                sample.append("%s: %s" % (name, finding.raw.strip()[:160]))
        verdicts.append(V.Verdict(
            V.DENY if closed else V.WARN, V.R_NEW_ERRORS,
            "%d new finding(s) absent from the pre-run baseline." % total,
            evidence={"sample": sample[:10],
                      "by_check": {k: len(v) for k, v in new_by_check.items()}}))

    # -- declared-but-unwritten --------------------------------------------
    if missing and declaration.strict_unfulfilled:
        verdicts.append(V.Verdict(
            V.DENY if closed else V.WARN, V.R_UNFULFILLED,
            "Declared but never written: %s"
            % ", ".join(m["pattern"] for m in missing[:5]),
            evidence={"patterns": [m["pattern"] for m in missing]}))

    # -- a check could not be trusted ---------------------------------------
    if degraded:
        verdicts.append(V.tooling_failure(
            V.R_CHECK_FAILED,
            "; ".join("%s (%s)" % (d.get("check"), d.get("why")) for d in degraded[:3]),
            declaration.fail_direction))

    if not verdicts:
        verdicts.append(V.Verdict(
            V.ALLOW, V.R_NO_VIOLATION,
            "%d file(s) changed, all inside the declaration; no new findings."
            % len(changes)))

    final = V.worst(verdicts)
    final = V.Verdict(
        decision=final.decision,
        reason=final.reason,
        message=final.message,
        evidence=dict(report, all_reasons=[v.reason for v in verdicts],
                      degraded=bool(degraded) or final.degraded),
        result_labels=final.result_labels,
    )
    return final, report


# ---------------------------------------------------------------------------
# human-readable rendering
# ---------------------------------------------------------------------------

_GLYPH = {V.ALLOW: "✓", V.WARN: "⚠", V.ESCALATE: "⚠", V.DENY: "✗"}


def render(final, report: dict, color: bool = False) -> str:
    """A short report that leads with what to do about it.

    House convention: messages start with a glyph and always say what to do
    instead, not only what went wrong.
    """
    lines = []
    glyph = _GLYPH.get(final.decision, "·")
    lines.append("%s wroteonly: %s — %s" % (glyph, final.decision, final.message))

    if report["violations"]:
        lines.append("")
        lines.append("  Outside the declaration:")
        for item in report["violations"][:20]:
            note = "" if item.get("hashed", True) else "  (size/mtime only)"
            tool = "  [%s]" % item["tool"] if item.get("tool") else ""
            lines.append("    %-8s %s%s%s"
                         % (_kind_label(item["kind"]), item["path"], tool, note))
        extra = len(report["violations"]) - 20
        if extra > 0:
            lines.append("    … and %d more" % extra)
        lines.append("")
        lines.append("  Declared: %s"
                     % (", ".join(report["declared"]["create"]
                                  + report["declared"]["modify"]) or "(nothing)"))

    if report["new_findings"]:
        lines.append("")
        lines.append("  New since the baseline (pre-existing findings suppressed):")
        for name, findings in report["new_findings"].items():
            for finding in findings[:10]:
                lines.append("    %s: %s" % (name, finding["raw"][:200]))

    if report["degraded"]:
        lines.append("")
        lines.append("  ⚠ Degraded — this verdict rests on incomplete information:")
        for item in report["degraded"][:5]:
            lines.append("    %s: %s" % (item.get("check"), item.get("why")))

    if final.decision == V.DENY:
        lines.append("")
        lines.append("  To resolve: revert the writes listed above, or re-run with a")
        lines.append("  declaration that includes them if they were intended.")

    return "\n".join(lines)
