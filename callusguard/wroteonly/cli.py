"""The operator CLI — for driving a run that has no hooks, and for inspecting one.

Two audiences:

    UNATTENDED JOBS that wrap an agent in a shell script. They do not need hooks at
    all; `declare` → run the agent → `verify` is the whole integration, and it works
    with any agent, on any host, including ones wroteonly has never heard of:

        wroteonly declare --intent "refresh the library" \\
            --create 'context/**/*.md' --forbid '**/*.env' --run-id "$JOB"
        claude -p "$PROMPT" --permission-mode bypassPermissions
        wroteonly verify --run-id "$JOB" || exit 1

    HUMANS debugging a hooked run: `wroteonly show` prints the last verdict and the
    evidence behind it.

WHY THE CLI PATH IS NOT SECOND-CLASS
    Hooks give a pre-emptive block and per-tool attribution. The CLI gives the same
    verdict from the same code with none of the host coupling. For the library job in
    `examples/` — an unattended `claude -p` under `bypassPermissions` — the CLI is the
    honest integration, because it verifies the tree rather than trusting the tool
    stream that `bypassPermissions` is already known to bypass.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from . import baseline as B
from . import observe as O
from . import report as R
from . import runner
from ..core import verdict as V
from .declare import Declaration, DeclarationError
from .state import RunState, prune, state_root

EXIT_OK = 0
EXIT_VIOLATION = 2
EXIT_BAD_INPUT = 3


def _default_run_id() -> str:
    return (os.environ.get("WROTEONLY_RUN_ID")
            or os.environ.get("CLAUDE_SESSION_ID")
            or "cli-%d" % os.getpid())


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------

def cmd_declare(args) -> int:
    checks = []
    for spec in args.check or []:
        name, _, command = spec.partition("=")
        if not command:
            name, command = command or spec, spec
        checks.append({"name": name or command, "command": command})

    data = {
        "run_id": args.run_id,
        "intent": args.intent or "",
        "create": args.create or [],
        "modify": args.modify or [],
        "delete": args.delete or [],
        "forbid": args.forbid or [],
        "fail_direction": args.fail_direction,
        "strict_unfulfilled": args.strict_unfulfilled,
        "checks": checks,
    }

    try:
        decl = Declaration.from_dict(data, root=args.root)
    except DeclarationError as exc:
        sys.stderr.write("✗ %s\n" % exc)
        return EXIT_BAD_INPUT

    if not decl.declares_anything and not args.allow_empty:
        sys.stderr.write(
            "✗ Declaration names no paths.\n"
            "  Pass at least one of --create / --modify / --delete, or\n"
            "  --allow-empty if you deliberately want to assert 'touch nothing'.\n")
        return EXIT_BAD_INPUT

    state = RunState(decl.run_id).ensure()
    state.write_json(RunState.DECLARATION, decl.to_dict())

    base = runner.capture_baseline(state, decl, args.root)

    if args.json:
        print(json.dumps({"run_id": decl.run_id, "state_dir": state.dir,
                          "files_fingerprinted": len(base.get("files") or {}),
                          "checks": len(decl.checks)}, indent=2))
    else:
        print("· wroteonly run %s" % decl.run_id)
        print("  fingerprinted %d file(s) under %s"
              % (len(base.get("files") or {}), os.path.realpath(args.root)))
        if decl.checks:
            failed = [c for c in base.get("checks", []) if not c.get("ok")]
            print("  baseline checks: %d run, %d unavailable"
                  % (len(decl.checks), len(failed)))
            for check in failed:
                print("  ⚠ %s did not run: %s" % (check["name"], check["error"]))
        print("  declared: %s" % (", ".join(decl.all_allow_patterns()) or "(nothing)"))
        if not base.get("scan_ok", True):
            print("  ⚠ baseline scan incomplete: %s" % base.get("scan_error"))
    return EXIT_OK


def cmd_verify(args) -> int:
    state = RunState(args.run_id)
    if not state.exists():
        sys.stderr.write(
            "✗ No run state for %r.\n"
            "  Run `wroteonly declare --run-id %s ...` before the agent acts.\n"
            % (args.run_id, args.run_id))
        return EXIT_BAD_INPUT

    stored = state.read_json(RunState.DECLARATION)
    if not stored:
        sys.stderr.write("✗ Run %r has no stored declaration.\n" % args.run_id)
        return EXIT_BAD_INPUT

    try:
        decl = Declaration.from_dict(stored, root=args.root or stored.get("root"))
    except DeclarationError as exc:
        sys.stderr.write("✗ stored declaration is invalid: %s\n" % exc)
        return EXIT_BAD_INPUT

    root = args.root or decl.root
    final, report = runner.verify(state, decl, root)
    state.write_json(RunState.VERDICT, final.to_dict())

    if args.json:
        print(final.to_json(indent=2))
    else:
        print(R.render(final, report))

    if args.keep:
        pass
    else:
        state.destroy()

    if final.decision == V.DENY:
        return EXIT_VIOLATION
    if final.decision in (V.WARN, V.ESCALATE) and args.strict:
        return EXIT_VIOLATION
    return EXIT_OK


def cmd_show(args) -> int:
    state = RunState(args.run_id)
    if not state.exists():
        sys.stderr.write("✗ No run state for %r.\n" % args.run_id)
        return EXIT_BAD_INPUT
    payload = {
        "run_id": state.run_id,
        "state_dir": state.dir,
        "declaration": state.read_json(RunState.DECLARATION),
        "verdict": state.read_json(RunState.VERDICT),
        "observations": len(state.observations()),
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return EXIT_OK


def cmd_runs(args) -> int:
    base = os.path.join(state_root(), "runs")
    try:
        names = sorted(os.listdir(base))
    except OSError:
        names = []
    if not names:
        print("· no runs recorded under %s" % base)
        return EXIT_OK
    for name in names:
        state = RunState(name)
        final = state.read_json(RunState.VERDICT) or {}
        print("%-40s %s" % (name, final.get("decision", "(in progress)")))
    return EXIT_OK


def cmd_prune(args) -> int:
    removed = prune(args.days)
    print("· removed %d run(s) older than %g day(s)" % (removed, args.days))
    return EXIT_OK


def cmd_hook(args) -> int:
    return runner.run()


# ---------------------------------------------------------------------------
# parser
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wroteonly",
        description="Verify an agent touched only what it said it would.")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("declare", help="record intent and snapshot the baseline")
    p.add_argument("--run-id", default=_default_run_id())
    p.add_argument("--intent", default="")
    p.add_argument("--create", action="append", metavar="GLOB")
    p.add_argument("--modify", action="append", metavar="GLOB")
    p.add_argument("--delete", action="append", metavar="GLOB")
    p.add_argument("--forbid", action="append", metavar="GLOB")
    p.add_argument("--check", action="append", metavar="NAME=COMMAND",
                   help="a project check to baseline and re-run (repeatable)")
    p.add_argument("--fail-direction", choices=("open", "closed"), default="open")
    p.add_argument("--strict-unfulfilled", action="store_true")
    p.add_argument("--allow-empty", action="store_true",
                   help="permit a declaration that names no writable paths")
    p.add_argument("--root", default=".")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_declare)

    p = sub.add_parser("verify", help="diff actual against declared, emit a verdict")
    p.add_argument("--run-id", default=_default_run_id())
    p.add_argument("--root", default=None)
    p.add_argument("--json", action="store_true")
    p.add_argument("--strict", action="store_true",
                   help="exit non-zero on warn/escalate too, not only deny")
    p.add_argument("--keep", action="store_true",
                   help="keep the run state instead of discarding it")
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("show", help="print a run's declaration and verdict")
    p.add_argument("--run-id", default=_default_run_id())
    p.set_defaults(func=cmd_show)

    p = sub.add_parser("runs", help="list recorded runs")
    p.set_defaults(func=cmd_runs)

    p = sub.add_parser("prune", help="delete old run state")
    p.add_argument("--days", type=float, default=7.0)
    p.set_defaults(func=cmd_prune)

    p = sub.add_parser("hook", help="act as a hook (reads a payload on stdin)")
    p.set_defaults(func=cmd_hook)

    return parser


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
