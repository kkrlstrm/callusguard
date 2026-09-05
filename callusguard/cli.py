"""One CLI over the whole loop.

    callus record   …   telemetry: serve (Claude Code) / ingest (Codex)
    callus derive   …   mine recurring failures -> candidate monitor rules
    callus learn    …   persist patterns + distill reviewable interventions offline
    callus guard    …   check rulesets, doctor, verify the audit chain
    callus scope    …   wroteonly: declare / verify a run's write set

`learn` is deliberately offline. It may call a strong model through an explicit
provider command, but it never auto-activates a rule, skill, workflow, or tool fix.
The synchronous enforcement path remains stdlib-only, network-free, and model-free.
"""

from __future__ import annotations

import argparse
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RULES_DIR = os.path.join(REPO, "callusguard", "guard", "rules")


def _forward(entry, rest):
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


CC_VERBS = ("serve", "migrate", "sessions", "inspect", "insights", "rate")
CODEX_VERBS = ("ingest",)


def cmd_record(args) -> int:
    mode = args.mode
    if mode in CODEX_VERBS:
        try:
            from .telemetry.codex import cli as codex_cli
        except ImportError:
            return _need_telemetry("record %s" % mode)
        return _forward(codex_cli.main, args.rest)
    try:
        from .telemetry.cc import cli as cc_cli
    except ImportError:
        return _need_telemetry("record %s" % mode)
    return _forward(cc_cli.main, [mode] + list(args.rest))


def cmd_derive(args) -> int:
    from .derive import rules as derive_rules
    return _forward(derive_rules.main, args.rest)


def cmd_learn(args) -> int:
    from .learn import cli as learn_cli
    return _forward(learn_cli.main, args.rest)


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


def cmd_scope(args) -> int:
    from .wroteonly import cli as wo_cli
    return _forward(wo_cli.main, args.rest)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="callus",
        description="Record what your agent did, learn from it, enforce, verify.")
    sub = p.add_subparsers(dest="command", required=True)

    r = sub.add_parser("record", help="telemetry (needs the telemetry extra)",
                       add_help=False)
    r.add_argument("mode", choices=CC_VERBS + CODEX_VERBS, metavar="VERB",
                   help="serve|migrate|sessions|inspect|insights|rate (Claude Code) "
                        "· ingest (Codex rollout files)")
    r.set_defaults(func=cmd_record)

    d = sub.add_parser("derive", help="mine telemetry into candidate monitor rules",
                       add_help=False)
    d.set_defaults(func=cmd_derive)

    l = sub.add_parser("learn", help="offline knowledge + intervention distillation",
                       add_help=False)
    l.set_defaults(func=cmd_learn)

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
    args, rest = build_parser().parse_known_args(argv)
    args.rest = rest
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
