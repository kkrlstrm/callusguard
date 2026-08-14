"""Rule lifecycle — which guards earned their place, and which should be cut back.

The stage that makes the name true.

    A callus forms where friction is real, and RESOLVES when the friction is
    engineered away. A rule library that only grows is one nobody trusts: every
    stale rule is latency on every tool call and noise in every review.

This reads the hash-chained audit log — which already records, per verdict, the
timestamp, the ruleset, and the ids of every rule that fired — and reports what each
rule has actually been doing. No new instrumentation: the enforcement record is the
evidence.

THE FOUR VERDICTS ON A RULE

    prune      declared in the ruleset, has not fired inside the window.
               Either the failure was engineered away (delete it — that is the tool
               working) or it never matched anything real (delete it — it was
               written from imagination).

    promote    a `monitor` rule firing repeatedly. It has evidence now. This is the
               staging rung graduating to nudge or block.

    review     a `nudge`/`deny`/`block` rule firing very often. Enforcement is
               working, but a guard that fires constantly is a workflow that has not
               been fixed. This is the "make it deterministic instead" signal.

    keep       firing occasionally in a settled action state. Leave it alone.

WHAT THIS DELIBERATELY DOES NOT DO
    It does not edit rulesets. Promotion and pruning are human acts — the same
    discipline that makes derived rules land as `monitor` and never auto-arm. This
    prints a report and exits; you decide.

    It also cannot distinguish "never fired because the failure is fixed" from
    "never fired because the regex is wrong". Both are prune candidates, and the
    second is the more valuable finding — a rule that has never once matched is not
    protecting you.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

PRUNE = "prune"
PROMOTE = "promote"
REVIEW = "review"
KEEP = "keep"

#: A monitor rule seen this many times has evidence behind it.
PROMOTE_AFTER = 5
#: Any acting rule firing more than this in the window is a workflow smell.
REVIEW_ABOVE = 25


def _parse_ts(value):
    if not isinstance(value, str):
        return None
    try:
        text = value.replace("Z", "+00:00")
        stamp = datetime.fromisoformat(text)
        return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def read_audit(path: str, since=None) -> list:
    """Load audit events, optionally only those at or after `since`.

    Tolerant by design: a corrupt line is skipped rather than fatal. Chain
    verification is `audit.verify`'s job; this is analysis, and refusing to report
    because line 4000 is malformed would be the wrong trade.
    """
    events = []
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except ValueError:
                    continue
                stamp = _parse_ts(event.get("ts"))
                if since and stamp and stamp < since:
                    continue
                events.append(event)
    except OSError:
        return []
    return events


def load_rules(ruleset_path: str) -> list:
    try:
        with open(ruleset_path, "r", encoding="utf-8") as fh:
            return json.load(fh).get("rules", [])
    except (OSError, ValueError):
        return []


def tally(events: list) -> dict:
    """rule_id -> {count, last_seen, actions seen}."""
    stats: dict = {}
    for event in events:
        actions = event.get("actions") or []
        for i, rule_id in enumerate(event.get("fired") or []):
            if not rule_id:
                continue
            row = stats.setdefault(rule_id, {"count": 0, "last_seen": None,
                                             "actions": set()})
            row["count"] += 1
            stamp = _parse_ts(event.get("ts"))
            if stamp and (row["last_seen"] is None or stamp > row["last_seen"]):
                row["last_seen"] = stamp
            if i < len(actions) and actions[i]:
                row["actions"].add(actions[i])
    return stats


def classify(rule: dict, stat: dict | None, window_days: int) -> tuple:
    """Return (verdict, reason) for one rule."""
    declared = rule.get("action", "nudge")

    if not stat or not stat["count"]:
        return PRUNE, ("never fired in the last %d days — either the failure was "
                       "engineered away, or the pattern never matched anything real"
                       % window_days)

    count = stat["count"]
    if declared == "monitor" and count >= PROMOTE_AFTER:
        return PROMOTE, ("fired %d times as monitor-only — it has evidence now; "
                         "promote to nudge or block" % count)
    if declared != "monitor" and count > REVIEW_ABOVE:
        return REVIEW, ("fired %d times — enforcement works, but a guard firing this "
                        "often is a workflow that has not been fixed; consider making "
                        "it deterministic instead" % count)
    return KEEP, "fired %d time(s); settled as %s" % (count, declared)


def report(ruleset_path: str, audit_path: str, window_days: int = 30) -> dict:
    """Build the lifecycle report for one ruleset against one audit log."""
    since = datetime.now(timezone.utc) - timedelta(days=window_days)
    events = read_audit(audit_path, since=since)
    stats = tally(events)
    rules = load_rules(ruleset_path)

    rows = []
    for rule in rules:
        rule_id = rule.get("id")
        stat = stats.get(rule_id)
        verdict, reason = classify(rule, stat, window_days)
        rows.append({
            "id": rule_id,
            "action": rule.get("action", "nudge"),
            "verdict": verdict,
            "reason": reason,
            "count": (stat or {}).get("count", 0),
            "last_seen": ((stat or {}).get("last_seen").isoformat()
                          if (stat or {}).get("last_seen") else None),
        })

    declared_ids = {r.get("id") for r in rules}
    orphans = sorted(set(stats) - declared_ids)

    return {
        "ruleset": ruleset_path,
        "audit": audit_path,
        "window_days": window_days,
        "events_in_window": len(events),
        "rules": rows,
        # Rules that fired but are no longer in the ruleset — usually a rename or a
        # deletion that already happened. Surfaced so the log stays interpretable.
        "orphan_rule_ids": orphans,
        "counts": {v: sum(1 for r in rows if r["verdict"] == v)
                   for v in (PRUNE, PROMOTE, REVIEW, KEEP)},
    }


GLYPH = {PRUNE: "✂", PROMOTE: "▲", REVIEW: "⚠", KEEP: "✓"}


def render(data: dict) -> str:
    lines = []
    counts = data["counts"]
    lines.append("rule lifecycle — %s" % os.path.basename(data["ruleset"]))
    lines.append("  %d verdict(s) in the last %d days"
                 % (data["events_in_window"], data["window_days"]))
    lines.append("")

    if data["events_in_window"] == 0:
        lines.append("  ⚠ No audit events in the window. Every rule below reads as a")
        lines.append("    prune candidate for lack of evidence, not because of it —")
        lines.append("    check the audit path before deleting anything.")
        lines.append("")

    for verdict in (PROMOTE, REVIEW, PRUNE, KEEP):
        rows = [r for r in data["rules"] if r["verdict"] == verdict]
        if not rows:
            continue
        lines.append("  %s %s (%d)" % (GLYPH[verdict], verdict.upper(), len(rows)))
        for row in rows:
            lines.append("      %-38s %s" % (row["id"], row["reason"]))
        lines.append("")

    if data["orphan_rule_ids"]:
        lines.append("  · fired but no longer declared: %s"
                     % ", ".join(data["orphan_rule_ids"][:8]))
        lines.append("")

    if counts[PRUNE]:
        lines.append("  %d rule(s) have stopped earning their place." % counts[PRUNE])
        lines.append("  Deleting them is the tool working, not rotting.")
    else:
        lines.append("  Every declared rule fired at least once. Nothing to cut back.")
    return "\n".join(lines)


def main(argv=None) -> int:
    import argparse
    from ..core import audit as audit_mod
    from ..core import hosts

    ap = argparse.ArgumentParser(
        prog="callus guard prune",
        description="Report which rules earned their place and which should be cut back.")
    ap.add_argument("ruleset", nargs="?", help="path to a ruleset JSON")
    ap.add_argument("--audit", help="audit log path (default: this host's)")
    ap.add_argument("--host", choices=("claude-code", "codex"))
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    host = hosts.get(args.host) if args.host else hosts.detect()
    audit_path = args.audit or audit_mod.default_path(host)

    ruleset = args.ruleset
    if not ruleset:
        here = os.path.dirname(os.path.abspath(__file__))
        ruleset = host.env("RULES") or os.path.join(here, "rules", "starter.rules.json")

    data = report(ruleset, audit_path, window_days=args.days)
    print(json.dumps(data, indent=2) if args.json else render(data))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
