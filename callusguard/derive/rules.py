#!/usr/bin/env python3
"""derive_rules.py — turn recurring tool-failures into candidate guard rules.

The telemetry-feedback half of agent-guard's loop: a failure that keeps happening
is a job the model can't do reliably on its own yet — i.e. a candidate for a
nudge. This tool surfaces those clusters and emits *candidate `monitor` rules*
(log-only, never auto-armed). A human reviews them, refines the regex + message,
and promotes monitor -> nudge/block. See docs/TELEMETRY.md.

Two input modes (both stdlib — cc-logger mode shells out to `psql`, no driver):

  # From a cc-logger Postgres DB (the rich path):
  python3 bin/derive_rules.py --from-cc-logger --days 7 --min-count 3 \
      --db-url "$NEON_CC_LOGGER_URL" --out candidates.rules.json

  # From a JSONL log of tool calls (zero-dependency path):
  python3 bin/derive_rules.py --from-log tool_calls.jsonl --out candidates.rules.json

A log line is any JSON object with a tool name, (optional) command, and a failure
signal — flexible keys: tool_name/tool, tool_input.command/command, and
status=="failure"/error/is_error. Non-failures are counted as attempts (see below)
but never become candidates themselves.

THE DENOMINATOR
    A failure count on its own cannot separate a broken command from a busy one.
    Both modes therefore also count every *attempt* of the same command shape —
    successes included — and hand the pair to `core.tiers`, which grades the
    cluster deterministic / reproducible / probabilistic / anecdotal and caps how
    restrictive a rule derived from it may ever be.

    An anecdotal cluster is not proposed at all. That is the point: it is what
    replaces the old rule of thumb that three failures earn a guard.
"""
import os
import re
import sys
import json
import argparse
import hashlib
import subprocess
from datetime import date

from callusguard.core import tiers

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
SQL_FILE = os.path.join(REPO, "sql", "recurring_failures.sql")

# Tools whose failures are NOT rule material, excluded from derivation by default.
#
# A telemetry logger may capture read-only/context tools (cc-logger records `Read` and
# `Skill` so context loading is attributable). Their "failures" are ordinary agent
# behaviour — a Read that misses is the agent probing whether a file exists, not a
# failure mode worth guarding. Left in, they clear the min-count threshold easily and
# emit a "tool surface X failed Nx" candidate in every review, spending the reviewer
# attention this tool exists to conserve.
#
# Override with --include-tool to derive on one anyway, or --exclude-tool to add more.
DEFAULT_EXCLUDED_TOOLS = {"Read", "Skill", "Glob", "Grep", "TodoWrite", "NotebookRead"}

# Command multiplexers where the 2nd token carries the real meaning.
MULTIPLEXERS = {"git", "npm", "pnpm", "yarn", "docker", "kubectl", "cargo", "go",
                "pip", "pip3", "python", "python3", "psql", "aws", "gcloud", "make",
                "brew", "apt", "apt-get", "systemctl", "launchctl"}


# --------------------------------------------------------------------------- #
# Normalization (mirrors sql/recurring_failures.sql so both modes cluster alike)
# --------------------------------------------------------------------------- #
def normalize_error(err):
    s = (err or "").lower()
    s = re.sub(r"'[^']*'|\"[^\"]*\"", "'S'", s)
    s = re.sub(r"[0-9]+", "#", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s[:200]


def _first_tokens(command):
    toks = [t for t in re.split(r"\s+", (command or "").strip()) if t]
    if not toks:
        return []
    head = os.path.basename(toks[0])
    # skip leading ENV=val assignments
    i = 0
    while i < len(toks) and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", toks[i]):
        i += 1
    if i >= len(toks):
        return []
    head = os.path.basename(toks[i])
    if head in MULTIPLEXERS and i + 1 < len(toks) and not toks[i + 1].startswith("-"):
        return [head, toks[i + 1]]
    return [head]


def _candidate_pattern(command):
    toks = _first_tokens(command)
    if not toks:
        return None
    return r"\b" + r"\s+".join(re.escape(t) for t in toks) + r"\b"


def _tool_group(tool):
    """Group a non-Bash tool for aggregation. MCP tools (mcp__Server__method)
    collapse to their server surface (mcp__Server__*); everything else groups by
    exact name. Returns (group_key, tool_glob)."""
    if tool.startswith("mcp__"):
        parts = tool.split("__")
        if len(parts) >= 3 and parts[1]:
            return (f"mcp__{parts[1]}__", f"mcp__{parts[1]}__*")
    return (tool, tool)


def command_shape(tool, command):
    """The clustering key for the DENOMINATOR — deliberately not the error signature.

    A successful call has no error to key on, so attempts and failures cannot share
    the error-shaped key that failures cluster by. They share this one instead:
    the command's shape for Bash (`git push`, not `git`, not the full command line),
    and the tool surface for everything else.

    Mirrors the `shaped` CTE in recurring_failures.sql. If you change one, change
    both, or the two input modes will tier the same workload differently.
    """
    if tool != "Bash":
        return _tool_group(tool)[0]
    toks = _first_tokens(command)
    return " ".join(toks) if toks else ""


def _rule_id(tool, tokens, signature):
    base = (tool + "-" + "-".join(tokens)) if tokens else tool
    base = re.sub(r"[^a-z0-9]+", "-", base.lower()).strip("-")
    h = hashlib.sha256(signature.encode()).hexdigest()[:6]
    return f"derived-{base}-{h}"


# --------------------------------------------------------------------------- #
# Core: rows -> candidate rules
# --------------------------------------------------------------------------- #
def _tier_meta(fail_count, attempt_count, min_attempts):
    """Grade one cluster and return the meta fields every candidate carries."""
    tier, rate = tiers.classify(fail_count, attempt_count, min_attempts)
    return tier, {
        "tier": tier,
        "tier_basis": tiers.describe(tier, rate, attempt_count),
        "attempt_count": int(attempt_count) if attempt_count else None,
        "fail_rate": round(rate, 4) if rate is not None else None,
        "action_ceiling": tiers.ceiling(tier),
    }


def derive_from_rows(rows, window_days, min_count=3, added=None, excluded_tools=None,
                     min_attempts=tiers.MIN_ATTEMPTS, keep_anecdotal=False,
                     dropped_out=None):
    """rows: list of dicts with tool_name, error_signature, fail_count,
    sample_command, sample_error (+ optional attempt_count/first_seen/last_seen).
    Returns a list of candidate rule dicts (all action=monitor).

    Bash rows become per-signature command-pattern candidates (the command shape
    is what matters). Non-Bash tools (MCP servers, WebFetch, …) are aggregated
    into ONE tool-wide candidate — a whole tool that keeps failing is a candidate
    to deny/monitor at the tool surface, regardless of the exact error. The
    min_count threshold is applied here (per-signature for Bash, per-tool total
    for others) so tool-wide signals can form from sub-threshold signatures.

    TIERING
        `attempt_count` — every settled call of the same command shape, successes
        included — grades each cluster via `core.tiers`. An **anecdotal** cluster
        (too few attempts to say anything) is dropped rather than proposed, unless
        `keep_anecdotal` asks to see it. Every surviving candidate carries its tier
        and the action ceiling that tier permits, and `engine.verify_rules` refuses
        a ruleset that later exceeds that ceiling.

        A row with no `attempt_count` tiers as **unknown**, not as deterministic.
        Callers that supply hand-built rows (and the pre-tiering JSONL logs that
        contain failures only) land here: still proposed, still monitor-only, but
        capped at a nudge for as long as the denominator is missing.

    WHY ATTEMPTS ARE TAKEN AS A MAX, NEVER A SUM
        Rows sharing a command shape each carry that shape's *total* attempts —
        the same number repeated, not a slice of it. Two failing MCP methods on
        one server both report the server's attempt count. Summing them would
        double the denominator and quietly demote a real deterministic failure
        into a probabilistic one.
    """
    from collections import defaultdict
    added = added or date.today().isoformat()
    excluded = DEFAULT_EXCLUDED_TOOLS if excluded_tools is None else set(excluded_tools)
    if excluded:
        rows = [r for r in rows if (r.get("tool_name") or "Bash") not in excluded]
    candidates = []
    dropped_anecdotal = []

    bash_rows = [r for r in rows if (r.get("tool_name") or "Bash") == "Bash"]
    other_rows = [r for r in rows if (r.get("tool_name") or "Bash") != "Bash"]

    for r in bash_rows:
        sig = r.get("error_signature") or normalize_error(r.get("sample_error"))
        count = int(r.get("fail_count", 0))
        if count < min_count:
            continue
        cmd = r.get("sample_command")
        tokens = _first_tokens(cmd) if cmd else []
        tier, tier_meta = _tier_meta(count, r.get("attempt_count"), min_attempts)
        if tier == tiers.ANECDOTAL and not keep_anecdotal:
            dropped_anecdotal.append(("Bash", " ".join(tokens) or sig[:40], tier_meta))
            continue
        rule = {
            "id": _rule_id("Bash", tokens, sig + str(count)),
            "tool": "Bash",
            "severity": 40,
            "action": "monitor",
            "message": (
                f"DRAFT — recurring Bash failure ({count}x in {window_days}d, "
                f"{tier_meta['tier_basis']}): "
                f"\"{(r.get('sample_error') or sig)[:160]}\". "
                f"Refine this pattern + write a helpful message, then promote monitor -> "
                f"at most {tier_meta['action_ceiling']}."
            ),
            "meta": {
                "why": f"{count} logged failures in {window_days}d; auto-surfaced by derive_rules.",
                "added": added, "telemetry_ref": "cc-logger",
                "fail_count": count, "error_signature": sig, "sample_command": cmd,
                "command_shape": r.get("command_shape") or command_shape("Bash", cmd),
                **tier_meta,
            },
        }
        if cmd:
            pat = _candidate_pattern(cmd)
            if pat:
                rule["any"] = [pat]
                rule["field"] = "command"
        candidates.append(rule)

    by_tool = defaultdict(list)
    for r in other_rows:
        by_tool[_tool_group(r["tool_name"])].append(r)
    for (group_key, tool_glob), rs in sorted(by_tool.items(), key=lambda kv: -sum(int(x.get("fail_count", 0)) for x in kv[1])):
        total = sum(int(x.get("fail_count", 0)) for x in rs)
        if total < min_count:
            continue
        # max, not sum — see the docstring. Every row of a group repeats the
        # group's own attempt total.
        attempts = max((int(x.get("attempt_count") or 0) for x in rs), default=0)
        tier, tier_meta = _tier_meta(total, attempts, min_attempts)
        if tier == tiers.ANECDOTAL and not keep_anecdotal:
            dropped_anecdotal.append((tool_glob, "(tool-wide)", tier_meta))
            continue
        sample = rs[0].get("sample_error") or rs[0].get("error_signature") or ""
        candidates.append({
            "id": _rule_id(tool_glob, [], group_key + str(total)),
            "tool": tool_glob,
            "severity": 60,
            "action": "monitor",
            "message": (
                f"DRAFT — tool surface '{tool_glob}' failed {total}x in {window_days}d across "
                f"{len(rs)} method/signature(s) ({tier_meta['tier_basis']}), e.g. "
                f"\"{sample[:140]}\". If this tool is unreliable/misconfigured, consider "
                f"action=deny (ceiling for this tier: {tier_meta['action_ceiling']}) with a "
                "pointer to the working path."
            ),
            "meta": {
                "why": f"{total} logged failures in {window_days}d on {tool_glob}; auto-surfaced by derive_rules.",
                "added": added, "telemetry_ref": "cc-logger",
                "fail_count": total, "distinct_signatures": len(rs),
                "command_shape": group_key,
                **tier_meta,
            },
        })

    # Silence is the failure mode this whole stage exists to prevent: a reviewer
    # who sees "2 candidates" must be able to tell "that is all there was" from
    # "eight more were dropped for thin evidence". `dropped_out` is how the caller
    # gets the second number — an explicit out-list, so the return type stays a
    # plain list of rules and every existing caller keeps working untouched.
    if dropped_out is not None:
        dropped_out.extend(dropped_anecdotal)
    return candidates


# --------------------------------------------------------------------------- #
# Input mode: cc-logger (via psql)
# --------------------------------------------------------------------------- #
def load_rows_from_cc_logger(db_url, days, min_count):
    with open(SQL_FILE) as f:
        sql = f.read()
    # Parameterize the bundled query.
    sql = re.sub(r"interval '\d+ days'", f"interval '{int(days)} days'", sql)
    sql = re.sub(r"HAVING count\(\*\) >= \d+", f"HAVING count(*) >= {int(min_count)}", sql)
    sql = sql.strip().rstrip(";")
    wrapped = f"SELECT coalesce(json_agg(t), '[]') FROM (\n{sql}\n) t"
    proc = subprocess.run(
        ["psql", db_url, "-tAX", "-c", wrapped],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"psql failed: {proc.stderr.strip()}")
    return json.loads(proc.stdout.strip() or "[]")


# --------------------------------------------------------------------------- #
# Input mode: JSONL tool-call log (zero-dep)
# --------------------------------------------------------------------------- #
def _looks_failed(obj):
    status = (obj.get("status") or "").lower()
    if status:
        return status == "failure"
    if obj.get("is_error") is True:
        return True
    err = obj.get("error")
    return bool(err) and str(err).strip() != ""


def load_rows_from_log(path, days, min_count):
    """Cluster a JSONL tool-call log into failure rows, each carrying its denominator.

    Successes are no longer skipped: they are the attempt count. Every settled line
    increments `attempts[command_shape]`, and each failure row is then stamped with
    its shape's total — the same total repeated across sibling rows, which is why
    derive_from_rows takes a max and never a sum.

    THE FAILURES-ONLY LOG, WHICH WOULD OTHERWISE LIE
        A log someone grepped down to failures has attempts == failures on every
        shape, so every cluster would grade `deterministic` — a 100% failure rate
        computed from a file that was filtered to contain nothing else. That is the
        exact shape of a number that looks like evidence and is an artifact of how
        the file was made.

        So: if the whole log contains no successful call at all, we decline to
        supply a denominator and let every row tier as `unknown` instead. Fewer
        claims, none of them invented. A log with even one success is taken at face
        value.
    """
    from collections import defaultdict
    clusters = defaultdict(lambda: {"fail_count": 0, "sample_command": None,
                                    "sample_error": None, "command_shape": ""})
    attempts = defaultdict(int)
    saw_success = False

    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue

            tool = obj.get("tool_name") or obj.get("tool") or "Bash"
            ti = obj.get("tool_input") or {}
            cmd = ti.get("command") if isinstance(ti, dict) else None
            cmd = cmd or obj.get("command")
            shape = command_shape(tool, cmd)

            failed = _looks_failed(obj)
            if shape:
                attempts[shape] += 1
            if not failed:
                saw_success = True
                continue

            err = obj.get("error") or obj.get("tool_response") or ""
            if isinstance(err, (dict, list)):
                err = json.dumps(err)[:300]
            sig = normalize_error(str(err))
            key = (tool, sig)
            c = clusters[key]
            c["fail_count"] += 1
            c["command_shape"] = shape
            if c["sample_command"] is None and cmd:
                c["sample_command"] = cmd
            if c["sample_error"] is None and err:
                c["sample_error"] = str(err)[:300]

    if not saw_success:
        sys.stderr.write(
            "note: %s contains no successful tool calls, so no failure rate can be "
            "computed from it — every cluster will tier as 'unknown' (capped at a "
            "nudge). Point --from-log at an unfiltered log, or use --from-cc-logger, "
            "to get real tiers.\n" % os.path.basename(path))

    # Return ALL clusters (no threshold here) so derive_from_rows can aggregate
    # sub-threshold signatures into tool-wide signals before thresholding.
    rows = []
    for (tool, sig), c in clusters.items():
        row = {"tool_name": tool, "error_signature": sig, **c}
        if saw_success:
            row["attempt_count"] = attempts.get(c["command_shape"], 0)
        rows.append(row)
    rows.sort(key=lambda r: r["fail_count"], reverse=True)
    return rows


# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser(description="Derive candidate agent-guard rules from tool-failure telemetry.")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--from-cc-logger", action="store_true", help="query a cc-logger Postgres DB via psql")
    src.add_argument("--from-log", metavar="JSONL", help="read a JSONL log of tool calls")
    ap.add_argument("--db-url", help="Postgres URL (default $NEON_CC_LOGGER_URL or $DATABASE_URL)")
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--min-count", type=int, default=3)
    ap.add_argument("--min-attempts", type=int, default=tiers.MIN_ATTEMPTS,
                    metavar="N",
                    help="below N observed attempts a cluster is 'anecdotal' and is "
                         f"not proposed at any failure rate (default: {tiers.MIN_ATTEMPTS})")
    ap.add_argument("--keep-anecdotal", action="store_true",
                    help="propose thin-evidence clusters too (they stay capped at "
                         "action=monitor and are marked tier=anecdotal)")
    ap.add_argument("--exclude-tool", action="append", metavar="TOOL", default=[],
                    help=f"additional tool to skip (default excluded: {', '.join(sorted(DEFAULT_EXCLUDED_TOOLS))})")
    ap.add_argument("--include-tool", action="append", metavar="TOOL", default=[],
                    help="derive on a tool that is excluded by default")
    ap.add_argument("--out", help="write candidate ruleset JSON to this file")
    ap.add_argument("--json", action="store_true", help="print the full candidate ruleset JSON to stdout")
    args = ap.parse_args()

    if args.from_cc_logger:
        db_url = args.db_url or os.environ.get("NEON_CC_LOGGER_URL") or os.environ.get("DATABASE_URL")
        if not db_url:
            ap.error("no DB url (pass --db-url or set NEON_CC_LOGGER_URL / DATABASE_URL)")
        rows = load_rows_from_cc_logger(db_url, args.days, args.min_count)
    else:
        rows = load_rows_from_log(args.from_log, args.days, args.min_count)

    excluded = (DEFAULT_EXCLUDED_TOOLS | set(args.exclude_tool)) - set(args.include_tool)
    dropped = []
    candidates = derive_from_rows(rows, args.days, min_count=args.min_count,
                                  excluded_tools=excluded,
                                  min_attempts=args.min_attempts,
                                  keep_anecdotal=args.keep_anecdotal,
                                  dropped_out=dropped)
    ruleset = {
        "ruleset": "derived-candidates",
        "bias": "fail-open",
        "description": (
            f"Auto-derived candidate rules from {'cc-logger' if args.from_cc_logger else args.from_log} "
            f"({len(candidates)} clusters, >= {args.min_count} fails in {args.days}d, "
            f">= {args.min_attempts} attempts). "
            "All action=monitor (log-only). Review, refine the regex + message, then "
            "promote — no further than each rule's meta.action_ceiling."
        ),
        "rules": candidates,
        "dropped_anecdotal": [
            {"tool": tool, "shape": shape, **meta} for tool, shape, meta in dropped
        ],
    }

    if args.out:
        with open(args.out, "w") as f:
            json.dump(ruleset, f, indent=2)
        sys.stderr.write(f"wrote {len(candidates)} candidate rule(s) -> {args.out}\n")

    if args.json or not args.out:
        # Default to a compact human summary unless --json asked for the full thing.
        if args.json:
            print(json.dumps(ruleset, indent=2))
        else:
            if not candidates:
                print(f"No recurring failures (>= {args.min_count} in {args.days}d). Nothing to propose.")
            else:
                print(f"{len(candidates)} candidate rule(s) from recurring failures "
                      f"(>= {args.min_count} in {args.days}d):\n")
                for r in candidates:
                    m = r["meta"]
                    print(f"  [{m['fail_count']:>3}x] {r['tool']:<16} {r.get('any', ['(tool-wide)'])[0]}")
                    detail = m.get("error_signature") or f"{m.get('distinct_signatures', '?')} distinct signature(s)"
                    print(f"         {detail[:100]}")
                    print(f"         {m['tier']} — {m['tier_basis']}; "
                          f"promote no further than {m['action_ceiling']}")
                print("\nRe-run with --out <file> to write the candidate monitor ruleset.")

            # Always say what was withheld, even when nothing was proposed — the
            # two zeroes mean very different things.
            if dropped:
                print(f"\n{len(dropped)} cluster(s) withheld as anecdotal "
                      f"(< {args.min_attempts} attempts — too thin to grade):")
                for tool, shape, meta in dropped[:10]:
                    print(f"  · {tool:<16} {shape[:40]:<40} {meta['tier_basis']}")
                if len(dropped) > 10:
                    print(f"  · … and {len(dropped) - 10} more")
                print("  Re-run with --keep-anecdotal to see them as monitor-only candidates.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
