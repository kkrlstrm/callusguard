"""stdin → verdict → exit code. The hook entry point, for both hosts.

THE LIFECYCLE

    SessionStart / first PreToolUse
        Load the declaration. Fingerprint the tree. Run the project's checks and
        store the findings. This is the baseline, and it is ephemeral — captured
        immediately before the agent acts, never committed, discarded on prune.

    PreToolUse
        Cheap pre-emptive gate. When the tool names a path outright (Write, Edit,
        MultiEdit, NotebookEdit) and that path is outside the declaration, block it
        before it happens — exit 2, which survives `bypassPermissions` on both
        hosts. Bash is deliberately NOT parsed; guessing which files a shell command
        will touch is exactly the guesswork this tool refuses to do. Those writes are
        caught by the scan instead.

    PostToolUse
        Record what was touched, for attribution. On Codex this can also block; on
        Claude Code it cannot ("tool already ran"), so nothing here is load-bearing.

    Stop
        The gate. Rescan, diff against the baseline, re-run the checks, subtract
        pre-existing findings, emit a verdict. On `deny`, refuse to let the agent
        stop and hand it the report so it can fix what it did.

ABSOLUTE INVARIANT — inherited from agent-guard, and it is not negotiable

    A wroteonly bug must never wedge a session.

    Every entry point is wrapped in a bare `except BaseException` that exits 0. A
    verification wrapper that takes down the agent it was verifying has done more
    damage than the write it was looking for. The one thing that is allowed to stop
    a run is a violated declaration — a decision, not a crash.

    The single exception is `WROTEONLY_STRICT=1`, which turns an internal error into
    a block. Unattended jobs that would rather halt than proceed unverified can set
    it; the default cannot.
"""

from __future__ import annotations

import json
import os
import sys

from . import baseline as B
from ..core import hosts as H
from . import observe as O
from . import report as R
from ..core import verdict as V
from .declare import Declaration, DeclarationError
from .state import RunState

DECLARATION_ENV = "WROTEONLY_DECLARATION"
DEFAULT_DECLARATION_PATHS = (
    ".wroteonly/declaration.json",
    ".wroteonly.json",
)


def _strict() -> bool:
    return os.environ.get("WROTEONLY_STRICT", "").strip() in ("1", "true", "yes")


def _disabled() -> bool:
    return os.environ.get("WROTEONLY_DISABLE", "").strip() in ("1", "true", "yes")


def find_declaration(cwd: str) -> str | None:
    """Locate the declaration file, env var first."""
    explicit = os.environ.get(DECLARATION_ENV)
    if explicit:
        return explicit if os.path.exists(explicit) else None
    for candidate in DEFAULT_DECLARATION_PATHS:
        path = os.path.join(cwd, candidate)
        if os.path.exists(path):
            return path
    return None


def load_declaration(ctx: dict) -> Declaration | None:
    """Load and stamp the declaration with this run's id.

    The declaration file is authored before the run and does not know the session
    id the host will assign, so the run_id is filled in here when absent.
    """
    path = find_declaration(ctx["cwd"])
    if not path:
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError) as exc:
        raise DeclarationError("could not read %s: %s" % (path, exc)) from exc
    if not data.get("run_id"):
        data["run_id"] = ctx["run_id"] or "unknown"
    return Declaration.from_dict(data, root=ctx["cwd"])


# ---------------------------------------------------------------------------
# phases
# ---------------------------------------------------------------------------

def capture_baseline(state: RunState, decl: Declaration, cwd: str) -> dict:
    """Fingerprint the tree and record the checkers' current findings."""
    payload: dict = {"root": cwd, "scan_ok": True, "scan_error": ""}
    try:
        payload["files"] = O.snapshot(cwd)
    except O.ScanLimitExceeded as exc:
        payload["files"] = {}
        payload["scan_ok"] = False
        payload["scan_error"] = str(exc)
    except OSError as exc:
        payload["files"] = {}
        payload["scan_ok"] = False
        payload["scan_error"] = str(exc)

    payload["checks"] = B.capture(list(decl.checks), cwd)["checks"] if decl.checks else []
    state.write_json(RunState.BASELINE, payload)
    return payload


def ensure_baseline(state: RunState, decl: Declaration, cwd: str) -> None:
    if not state.has(RunState.BASELINE):
        state.write_json(RunState.DECLARATION, decl.to_dict())
        capture_baseline(state, decl, cwd)


def verify(state: RunState, decl: Declaration, cwd: str):
    """Run the full comparison. Returns (Verdict, report)."""
    before = state.read_json(RunState.BASELINE) or {}

    scan_ok = bool(before.get("scan_ok", False))
    scan_error = before.get("scan_error", "") or "no baseline scan was captured"
    changes: list = []

    if scan_ok:
        try:
            after = O.snapshot(cwd)
            changes = O.diff(before.get("files") or {}, after)
            changes = O.attribute(changes, state.observations())
        except (O.ScanLimitExceeded, OSError) as exc:
            scan_ok, scan_error = False, str(exc)

    new_by_check, degraded = {}, []
    if decl.checks:
        after_checks = B.capture(list(decl.checks), cwd)
        new_by_check, degraded = B.new_findings(
            {"checks": before.get("checks") or []}, after_checks)

    return R.build(decl, changes, cwd,
                   new_by_check=new_by_check, degraded=degraded,
                   scan_ok=scan_ok, scan_error=scan_error)


# ---------------------------------------------------------------------------
# event handlers
# ---------------------------------------------------------------------------

def handle_pre_tool_use(host, ctx, state, decl):
    ensure_baseline(state, decl, ctx["cwd"])

    paths = O.paths_from_tool_input(ctx["tool_name"], ctx["tool_input"])
    if paths:
        state.append_observation({
            "phase": "pre", "tool": ctx["tool_name"],
            "tool_use_id": ctx["tool_use_id"], "paths": paths,
        })

    if not decl.declares_anything:
        return host.noop()

    for raw in paths:
        from .declare import normalize_path
        rel = normalize_path(raw, ctx["cwd"])
        if rel is None:
            return host.block_tool(
                "%s writes outside the run root (%s). The declaration cannot "
                "cover it." % (ctx["tool_name"], raw))
        # A path-naming tool is a write; whether it creates or modifies is not
        # knowable yet, so test the permissive case and let the Stop gate — which
        # knows what actually happened — make the precise call.
        ok_create, _ = decl.permits(rel, "create")
        ok_modify, why = decl.permits(rel, "modify")
        if not (ok_create or ok_modify):
            declared = ", ".join(decl.all_allow_patterns()) or "(nothing)"
            return host.block_tool(
                "%s is outside the declared write set (%s).\n"
                "Declared: %s\n"
                "If this write is intended, re-run with a declaration that "
                "includes it." % (rel, why, declared))
    return host.noop()


def handle_post_tool_use(host, ctx, state, decl):
    paths = O.paths_from_tool_input(ctx["tool_name"], ctx["tool_input"])
    if paths:
        state.append_observation({
            "phase": "post", "tool": ctx["tool_name"],
            "tool_use_id": ctx["tool_use_id"], "paths": paths,
        })
    return host.noop()


def handle_stop(host, ctx, state, decl):
    # `stop_hook_active` is true when we are already inside a continuation we
    # caused. Blocking again would loop forever, so report and let it stop.
    if ctx.get("stop_hook_active"):
        return host.noop()

    if not state.has(RunState.BASELINE):
        # Nothing was ever captured — the agent ran without wroteonly seeing the
        # start. Say so rather than claiming a clean run.
        final = V.tooling_failure(
            V.R_BASELINE_MISSING,
            "No baseline was captured for this run; nothing can be verified.",
            decl.fail_direction)
        state.write_json(RunState.VERDICT, final.to_dict())
        if final.blocking:
            return host.keep_going(final.message)
        return host.advise("wroteonly: " + final.message, H.STOP)

    final, report = verify(state, decl, ctx["cwd"])
    state.write_json(RunState.VERDICT, final.to_dict())
    rendered = R.render(final, report)

    if final.decision == V.DENY:
        return host.keep_going(rendered)
    if final.decision in (V.WARN, V.ESCALATE):
        return host.advise(rendered, H.STOP)
    return host.noop()


HANDLERS = {
    H.PRE_TOOL_USE: handle_pre_tool_use,
    H.POST_TOOL_USE: handle_post_tool_use,
    H.STOP: handle_stop,
}


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------

def run(stdin=None, stdout=None, stderr=None) -> int:
    """Read a hook payload, act, return an exit code.

    Written to be callable in-process so the tests can drive it without a
    subprocess, while `bin/wroteonly-hook.py` stays a stable three-line shim —
    which matters on Codex, where the entry script's hash is trust-pinned.
    """
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    stderr = stderr or sys.stderr

    try:
        if _disabled():
            return 0

        raw = stdin.read()
        try:
            payload = json.loads(raw) if raw.strip() else {}
        except ValueError:
            return 0  # not a hook payload; not ours to complain about

        host = H.detect(payload)
        ctx = host.parse(payload)
        event = ctx["event"]
        if event not in HANDLERS:
            return 0

        state = RunState(ctx["run_id"] or "unknown")

        try:
            decl = load_declaration(ctx)
        except DeclarationError as exc:
            # A malformed declaration is a real problem, but it is the operator's
            # problem, not a reason to kill the agent. Surface it loudly at Stop.
            if event == H.STOP:
                out, err, code = host.advise(
                    "wroteonly: declaration could not be parsed — nothing was "
                    "verified this run. %s" % exc, H.STOP)
                stdout.write(out)
                stderr.write(err)
                return code
            return 0

        if decl is None:
            return 0  # no declaration on disk: wroteonly is not driving this run

        out, err, code = HANDLERS[event](host, ctx, state, decl)
        if out:
            stdout.write(out)
        if err:
            stderr.write(err)
        return code

    except BaseException as exc:  # noqa: BLE001 — the invariant, deliberately broad
        if _strict():
            try:
                stderr.write("wroteonly: internal error (strict mode): %s\n" % exc)
            except Exception:
                pass
            return 2
        return 0


def main() -> int:
    return run()
