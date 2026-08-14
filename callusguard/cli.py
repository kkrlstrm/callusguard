"""One CLI over the whole loop.

    callusguard record   …   telemetry: serve (Claude Code) / ingest (Codex)
    callusguard derive   …   mine recurring failures -> candidate monitor rules
    callusguard guard    …   check rulesets, doctor, verify the audit chain
    callusguard scope    …   wroteonly: declare / verify a run's write set

The four verbs are the four stages, in order. That ordering is the product: a rule
you can enforce should have come from a failure you actually recorded.

DEPENDENCIES
    `derive`, `guard` and `scope` are stdlib-only. `record` needs the `telemetry`
    extra and imports it lazily, so `callus guard doctor` works on a machine that
    has never installed FastAPI. A missing extra produces an install instruction,
    not a traceback.
"""

from __future__ import annotations

import argparse
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RULES_DIR = os.path.join(REPO, "callusguard", "guard", "rules")


def _forward(entry, rest):
    """Call a sub-CLI's `main`, whichever calling convention it uses.

    The five merged tools disagree: `check_rules.main(argv)` and
    `wroteonly.cli.main(argv)` take a list, while `doctor.main()` and
    `derive.rules.main()` read `sys.argv` themselves. Rather than rewrite four
    working entry points — and risk changing behaviour the ported tests pin — this
    adapts to both and keeps `sys.argv` correct for the ones that read it.
    """
    argv_backup = sys.argv
    sys.argv = [getattr(entry, "__module__", "callusguard")] + list(rest)
    try:
        try:
            return entry(list(rest)) or 0
        except TypeError as exc:
            if "positional argument" not in str(exc):
                raise
            return entry() or 0
    finally:
        sys.argv = argv_backup


def _need_telemetry(what: str) -> int:
    sys.stderr.write(
        "✗ %s needs the telemetry extra, which is not installed.\n"
        "  Install it with:  pip install 'callusguard[telemetry]'\n"
        "  The enforcement side (guard, scope, derive) needs nothing and still works.\n"
        % what)
    return 3


# ---------------------------------------------------------------------------
# record — telemetry
# ---------------------------------------------------------------------------

def cmd_record(args) -> int:
    if args.mode == "ingest":
        # Codex: pull rollout files. Only needs psycopg for the Postgres backend;
        # the default SQLite path is stdlib.
        try:
            from .telemetry.codex import cli as codex_cli
        except ImportError:
            return _need_telemetry("record ingest")
        return _forward(codex_cli.main, args.rest)

    try:
        from .telemetry.cc import cli as cc_cli
    except ImportError:
        return _need_telemetry("record serve")
    return _forward(cc_cli.main, args.rest)


# ---------------------------------------------------------------------------
# derive — telemetry -> candidate rules
# ---------------------------------------------------------------------------

def cmd_derive(args) -> int:
    from .derive import rules as derive_rules
    return _forward(derive_rules.main, args.rest)


# ---------------------------------------------------------------------------
# guard
# ---------------------------------------------------------------------------

def cmd_guard(args) -> int:
    if args.action == "check":
        from .guard import check_rules
        targets = args.rest or [os.path.join(RULES_DIR, f)
                                for f in sorted(os.listdir(RULES_DIR))
                                if f.endswith(".json")]
        return _forward(check_rules.main, targets)

    if args.action == "doctor":
        from .guard import doctor
        return _forward(doctor.main, [])

    if args.action == "audit":
        from .core import audit, hosts
        host = hosts.get(args.host) if args.host else hosts.detect()
        return audit.main([args.audit_action or "verify",
                           args.path or audit.default_path(host)])

    if args.action == "prune":
        from .guard import lifecycle
        return _forward(lifecycle.main, args.rest)

    if args.action == "rules":
        for name in sorted(os.listdir(RULES_DIR)):
            if name.endswith(".json"):
                print(os.path.join(RULES_DIR, name))
        return 0
    return 2


# ---------------------------------------------------------------------------
# scope — wroteonly
# ---------------------------------------------------------------------------

def cmd_scope(args) -> int:
    from .wroteonly import cli as wo_cli
    return _forward(wo_cli.main, args.rest)


# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="callus",
        description="Record what your agent did, derive guards, enforce, verify.")
    sub = p.add_subparsers(dest="command", required=True)

    r = sub.add_parser("record", help="telemetry (needs the telemetry extra)",
                       add_help=False)
    r.add_argument("mode", choices=("serve", "ingest"),
                   help="serve = Claude Code hooks POST in; ingest = read Codex rollouts")
    r.set_defaults(func=cmd_record)

    d = sub.add_parser("derive", help="mine telemetry into candidate monitor rules",
                       add_help=False)
    d.set_defaults(func=cmd_derive)

    # allow_abbrev=False: otherwise argparse resolves a pass-through `--audit`
    # to this parser's `--audit-action` by prefix match and rejects the value.
    g = sub.add_parser("guard", help="rulesets, lifecycle, doctor, audit chain",
                       allow_abbrev=False)
    g.add_argument("action", choices=("check", "doctor", "audit", "rules", "prune"))
    g.add_argument("--host", choices=("claude-code", "codex"))
    g.add_argument("--audit-action", choices=("verify", "tail"))
    g.add_argument("--path")
    g.set_defaults(func=cmd_guard)

    s = sub.add_parser("scope", help="declare / verify a run's write set (wroteonly)",
                       add_help=False)
    s.set_defaults(func=cmd_scope)

    return p


def main(argv=None) -> int:
    """Parse known args and forward the rest to the sub-CLI.

    `parse_known_args`, not `nargs=REMAINDER`. REMAINDER stops collecting at the
    first token that *looks* like an option, so `callusguard derive --from-log x.jsonl`
    had its flags claimed by the top-level parser and then rejected as unrecognised.
    Forwarding sub-CLIs also set `add_help=False` so `--help` reaches the real tool
    rather than printing this wrapper's usage.
    """
    args, rest = build_parser().parse_known_args(argv)
    args.rest = rest
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
