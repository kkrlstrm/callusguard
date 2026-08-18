#!/usr/bin/env python3
# Copyright (C) 2026 Kai Karlstrom
# SPDX-License-Identifier: Apache-2.0
"""Regenerate the two evidence tables in the README from a real cc-logger database.

The README makes two quantitative claims about this loop, and neither should have to
be taken on faith:

    the funnel     how many candidate rules derivation proposed, over how many weekly
                   windows, against how many were actually promoted into a ruleset.
                   The promotion rate IS the credibility metric for "guards written
                   from evidence" — a tool that proposes 136 rules to land 12 is
                   allocating reviewer attention, which is the honest claim.

    the effect     for each promoted rule, the failure rate among *matching* attempts
                   before vs. after the date it was promoted. Includes the rules that
                   got worse, which are the interesting ones.

Both are computed here, from the same telemetry the guard was derived from, so anyone
with a cc-logger DB can re-run them and check the numbers in the README.

    python3 scripts/evidence-report.py \
        --db-url "$NEON_CC_LOGGER_URL" \
        --ruleset path/to/live.rules.json \
        --since 2026-05-13 --until 2026-08-18

WHAT THIS CANNOT TELL YOU, AND THE README MUST SAY SO
    The before/after denominators are not controlled. Usage volume shifts, the surface
    an agent reaches for changes *because* of the nudge, and a rule can only ever
    affect attempts that come after it. So a falling rate is consistent with the guard
    working and also with the workload moving; a falling *attempt count* alongside it
    is the stronger signal, which is why both columns are printed.

    The genuinely rigorous version needs `tool_use_id` on the audit event (added in
    0.4.0) joined to `tool_calls.tool_call_id`, so one nudged call can be followed to
    its own outcome. Until enough of that data accumulates, this is correlation over
    time and is labelled as such.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from callusguard.derive.rules import derive_from_rows  # noqa: E402

# psql inherits PG* from the environment and will silently dial a local socket if any
# of them are set. Clearing them is not paranoia; it is the single most common way a
# "the database is empty" result turns out to be a connection to the wrong database.
_CLEAN_PG = {"PGDATABASE": "", "PGHOST": "", "PGPORT": "", "PGUSER": "", "PGPASSWORD": ""}

_FAILURE_CLUSTERS = """
SELECT coalesce(json_agg(t), '[]') FROM (
  SELECT tool_name,
    regexp_replace(regexp_replace(regexp_replace(
      lower(coalesce(error, '')), '''[^'']*''|"[^"]*"', '''S''', 'g'),
      '[0-9]+', '#', 'g'), '\\s+', ' ', 'g')     AS error_signature,
    count(*)                                     AS fail_count,
    (array_agg(tool_input ->> 'command' ORDER BY started_at DESC)
       FILTER (WHERE tool_input ? 'command'))[1] AS sample_command,
    (array_agg(left(coalesce(error, ''), 300) ORDER BY started_at DESC))[1] AS sample_error
  FROM tool_calls
  WHERE status = 'failure' AND started_at >= '%s' AND started_at < '%s'
  GROUP BY 1, 2 ORDER BY 3 DESC
) t
"""

_BASH_CALLS = """
SELECT coalesce(json_agg(json_build_object(
  'd', started_at::date::text, 'failed', status = 'failure',
  'c', coalesce(tool_input ->> 'command', ''))), '[]')
FROM tool_calls WHERE tool_name = 'Bash' AND started_at >= '%s' AND started_at < '%s'
"""

_TOTALS = """
SELECT json_build_object(
  'sessions',   (SELECT count(*) FROM sessions),
  'tool_calls', (SELECT count(*) FROM tool_calls WHERE started_at >= '%s' AND started_at < '%s'),
  'failures',   (SELECT count(*) FROM tool_calls
                 WHERE status = 'failure' AND started_at >= '%s' AND started_at < '%s'))
"""


def query(db_url: str, sql: str):
    proc = subprocess.run(["psql", db_url, "-tAX", "-c", sql],
                          capture_output=True, text=True,
                          env={**os.environ, **_CLEAN_PG})
    if proc.returncode != 0:
        raise SystemExit("psql failed: %s" % proc.stderr.strip()[:400])
    return json.loads(proc.stdout.strip() or "[]")


def funnel(db_url: str, since: datetime.date, until: datetime.date, min_count: int):
    """Replay derivation one week at a time, exactly as a weekly review would."""
    proposed, unique, windows = 0, set(), 0
    day = since
    rows_out = []
    while day < until:
        nxt = day + datetime.timedelta(days=7)
        clusters = query(db_url, _FAILURE_CLUSTERS % (day, nxt))
        candidates = derive_from_rows(clusters, 7, min_count=min_count)
        windows += 1
        proposed += len(candidates)
        unique.update(c["id"] for c in candidates)
        top = sorted(((c["meta"]["fail_count"],
                       c.get("any", ["(tool-wide)"])[0] if c["tool"] == "Bash" else c["tool"])
                      for c in candidates), reverse=True)[:3]
        rows_out.append((day, nxt, len(candidates), top))
        day = nxt
    return {"proposed": proposed, "unique": len(unique), "windows": windows, "rows": rows_out}


def promoted(ruleset_path: str) -> list:
    """Rules in the LIVE ruleset that trace back to telemetry.

    This is the funnel's denominator-partner, and it is deliberately wider than the
    effect table below: a rule promoted at the *tool* surface (an MCP server, WebFetch)
    has no command regex to measure, but it was still a candidate that got promoted.
    Counting only the measurable ones would understate the promotion rate.
    """
    with open(ruleset_path) as fh:
        rules = json.load(fh).get("rules", [])
    return [r for r in rules if (r.get("meta") or {}).get("telemetry_ref")]


def effect(db_url: str, ruleset_path: str, since: datetime.date, until: datetime.date):
    """Failure rate among matching Bash attempts, split at each rule's promotion date."""
    calls = query(db_url, _BASH_CALLS % (since, until))
    with open(ruleset_path) as fh:
        rules = json.load(fh).get("rules", [])
    out = []
    for rule in rules:
        patterns = rule.get("any")
        added = (rule.get("meta") or {}).get("added")
        if not patterns or rule.get("field") != "command" or not added:
            continue
        promoted = datetime.date.fromisoformat(added)
        compiled = [re.compile(p, re.I) for p in patterns]
        pre = post = pre_fail = post_fail = 0
        for call in calls:
            if not any(p.search(call["c"]) for p in compiled):
                continue
            if datetime.date.fromisoformat(call["d"]) < promoted:
                pre += 1
                pre_fail += bool(call["failed"])
            else:
                post += 1
                post_fail += bool(call["failed"])
        out.append({"id": rule["id"], "action": rule.get("action"), "promoted": added,
                    "before": (pre_fail, pre), "after": (post_fail, post)})
    return out


def _pct(fail, total):
    return "n/a" if not total else "%.1f%%" % (100.0 * fail / total)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--db-url", default=os.environ.get("NEON_CC_LOGGER_URL")
                    or os.environ.get("DATABASE_URL"))
    ap.add_argument("--ruleset", required=True, help="the LIVE ruleset — what got promoted")
    ap.add_argument("--since", required=True, help="YYYY-MM-DD")
    ap.add_argument("--until", required=True, help="YYYY-MM-DD")
    ap.add_argument("--min-count", type=int, default=3)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    if not args.db_url:
        ap.error("no DB url (pass --db-url or set NEON_CC_LOGGER_URL / DATABASE_URL)")
    since = datetime.date.fromisoformat(args.since)
    until = datetime.date.fromisoformat(args.until)

    totals = query(args.db_url, _TOTALS % (since, until, since, until))
    fun = funnel(args.db_url, since, until, args.min_count)
    eff = effect(args.db_url, args.ruleset, since, until)
    live = promoted(args.ruleset)

    if args.json:
        print(json.dumps({"totals": totals, "funnel": fun, "effect": eff,
                          "promoted": [r["id"] for r in live]}, indent=2, default=str))
        return 0

    print("corpus — %s .. %s (%d days)" % (since, until, (until - since).days))
    print("  %s sessions · %s tool calls · %s failures (%.1f%%)"
          % (totals["sessions"], totals["tool_calls"], totals["failures"],
             100.0 * totals["failures"] / max(totals["tool_calls"], 1)))
    print()
    print("derivation funnel — one 7-day window at a time, min-count %d" % args.min_count)
    for start, end, n, top in fun["rows"]:
        summary = ", ".join("%dx %s" % (c, p) for c, p in top)
        print("  %s  %3d candidate(s)  %s" % (start, n, summary))
    print()
    print("  %d proposed (%d unique) across %d windows"
          % (fun["proposed"], fun["unique"], fun["windows"]))
    by_source: dict = {}
    for rule in live:
        by_source.setdefault(rule["meta"]["telemetry_ref"], []).append(rule["id"])
    from_telemetry = sum(len(v) for k, v in by_source.items() if k == "cc-logger")
    print("  %d promoted into the live ruleset, %d of them from this telemetry"
          % (len(live), from_telemetry))
    print("  -> %.0f%% promotion rate" % (100.0 * from_telemetry / max(fun["proposed"], 1)))
    for source, ids in sorted(by_source.items()):
        if source != "cc-logger":
            # Authored from a principle, not mined from failures. It belongs in the
            # ruleset; it does not belong in the numerator of a derivation funnel.
            print("     (not derived: %s — %s)" % (", ".join(ids), source))
    print()
    print("effect of promotion — failure rate among MATCHING Bash attempts")
    print("  (%d of the %d promoted rules carry a command regex and are measurable here)"
          % (len(eff), len(live)))
    print("  %-32s %-11s %18s %18s" % ("rule", "promoted", "before", "after"))
    for row in eff:
        bf, bt = row["before"]
        af, at = row["after"]
        print("  %-32s %-11s %8s %9s %8s %9s"
              % (row["id"], row["promoted"], _pct(bf, bt), "(n=%d)" % bt,
                 _pct(af, at), "(n=%d)" % at))
    print()
    print("  Denominators are NOT controlled — see this file's docstring. Read the")
    print("  attempt counts alongside the rates; a collapsing n is the stronger signal.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
