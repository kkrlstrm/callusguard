"""The PreToolUse entry point. Host-parameterised; one copy for both hosts.

Flow: read hook JSON on stdin -> evaluate ruleset -> audit the verdict (best-effort)
-> emit the decision.

    ABSOLUTE INVARIANT: any unexpected error falls open (exit 0).
    A guard bug must never wedge a session. Auditing never blocks a call.

WHAT CHANGED IN THE MERGE, AND WHAT DID NOT
    agent-guard and codex-guard had two copies of this file differing by 32 lines.
    Every one of those lines was a *name*: the brand in the nudge prefix and the
    block message, the env-var prefix, and the audit path. None was behaviour.

    Those names now come from a Host (callusguard.core.hosts). The emitted bytes are
    unchanged for both hosts — `BLOCKED by agent-guard: …` still says agent-guard on
    Claude Code, and `codex-guard:` still prefixes nudges on Codex — because model-
    facing text and audit records are a contract with things already on disk.

Env toggles (prefix comes from the host: AGENT_GUARD_* or CODEX_GUARD_*):
    <PREFIX>_DRYRUN=1          -> compute + audit, but never block/nudge (observe only)
    <PREFIX>_NUDGE_AS_BLOCK=1  -> promote every nudge to a hard block (exit 2)
    <PREFIX>_AUDIT=<path>      -> override the audit-log location
    <PREFIX>_RULES=<path>      -> override the ruleset
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone

from ..core import audit
from ..core import hosts as _hosts
from .engine import effective_action, evaluate, get_field, resolve

#: Patterns whose *values* must never land in the audit log's command preview.
#: Ported verbatim from agent-guard; codex-guard's copy lacked these, so the merge
#: is a strict security improvement for the Codex side.
_SECRET_PREVIEW = [
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"glpat-[A-Za-z0-9_\-]{20,}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"(postgres(?:ql)?://[^:@\s]+):[^@\s]+@", re.I),  # DSN password
    re.compile(r"(?i)(password|secret|api[_-]?key|token)(\s*[=:]\s*)\S+"),
]


def _redacted_preview(cmd: str, limit: int = 120) -> str:
    """A short, secret-scrubbed preview of the command for the audit log.

    We log a hash for exact identity and only a redacted preview for human
    legibility, so a command containing a live key is never persisted verbatim.
    """
    s = cmd[:limit]
    for pat in _SECRET_PREVIEW:
        if pat.groups >= 2:
            s = pat.sub(lambda m: m.group(1) + m.group(2) + "***", s)
        elif pat.groups == 1:
            s = pat.sub(lambda m: m.group(1) + ":***@", s)
        else:
            s = pat.sub("***", s)
    return s + ("…" if len(cmd) > limit else "")


def _nudge_text(host, notes) -> str:
    """The exact pre-merge nudge prefixes, preserved per host."""
    if host.name == "claude-code":
        head = "agent-guard (telemetry-driven):"
    else:
        head = "%s:" % host.brand
    return head + "\n- " + "\n- ".join(notes)


def run(ruleset_path=None, load_rules_fn=None, host=None, stdin=None,
        stdout=None, stderr=None, exit_fn=None,
        default_ruleset=None, ruleset_env="RULES"):
    """Evaluate one tool call.

    Parameterised on streams and exit so the behaviour can be tested in-process as
    well as through the real entry scripts. Defaults reproduce the pre-merge
    process-level behaviour exactly.

    HOST DETECTION HAPPENS HERE, NOT IN THE ENTRY SCRIPT — and that ordering is
    load-bearing. The strongest signal for which host we are inside is the payload
    itself (Codex sends `turn_id` and no `prompt_id`; Claude Code sends `prompt_id`),
    and the payload only exists after stdin is read. An entry script that detects
    first can only ever consult the environment, which is empty under a plain
    `codex exec`. That mis-detection is silent and picks the wrong brand, the wrong
    ruleset env var, and the wrong audit log.

    So the ruleset is resolved *after* detection: pass `default_ruleset` and the
    correct `<PREFIX>_RULES` override is honoured for whichever host turns out to
    be calling.
    """
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    stderr = stderr or sys.stderr
    exit_fn = exit_fn or sys.exit

    try:
        raw = stdin.read()
        data = json.loads(raw) if raw.strip() else {}
    except Exception:
        return exit_fn(0)  # malformed input -> never block

    try:
        host = host or _hosts.detect(data if isinstance(data, dict) else None)
        if not isinstance(data, dict):
            return exit_fn(0)

        tool_name = data.get("tool_name", "") or ""
        tool_input = data.get("tool_input", {}) or {}

        # Resolved only now that the host is known — see the docstring.
        if ruleset_path is None:
            ruleset_path = host.env(ruleset_env) or default_ruleset

        if load_rules_fn is not None:
            rules = load_rules_fn()
        else:
            with open(ruleset_path) as fh:
                rules = json.load(fh).get("rules", [])

        fired = evaluate(rules, tool_name, tool_input)

        nudge_as_block = host.env("NUDGE_AS_BLOCK") == "1"
        dryrun = host.env("DRYRUN") == "1"
        verdict = resolve(fired, nudge_as_block=nudge_as_block)

        # Best-effort audit — a logging failure must never block the call.
        try:
            if fired:
                cmd = get_field(tool_input, "command")
                from .. import __version__
                event = {
                    "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "tool": tool_name,
                    "ruleset": os.path.basename(ruleset_path) if ruleset_path else "inline",
                    "fired": [r.get("id") for r in fired],
                    "actions": [effective_action(r, nudge_as_block) for r in fired],
                    "decision": "allow (dryrun)" if dryrun else verdict["decision"],
                    "winner": (verdict["winner"] or {}).get("id"),
                    "version": __version__,
                    "dryrun": dryrun,
                    "nudge_as_block": nudge_as_block,
                }
                if data.get("session_id"):
                    event["session_id"] = data["session_id"]
                if data.get("cwd"):
                    event["cwd"] = data["cwd"]
                if cmd:
                    event["command_sha256"] = hashlib.sha256(
                        cmd.encode("utf-8", "replace")).hexdigest()
                    event["command_preview"] = _redacted_preview(cmd)
                audit.append(event, host=host)
        except Exception:
            pass

        if dryrun:
            return exit_fn(0)

        decision = verdict["decision"]
        if decision == "block":
            stderr.write("BLOCKED by %s: %s\n" % (host.brand, verdict["winner"]["message"]))
            return exit_fn(2)
        if decision == "deny":
            out, _, _ = host.deny_tool(verdict["winner"]["message"])
            stdout.write(out)
            return exit_fn(0)
        if decision == "nudge" and verdict["notes"]:
            out, _, _ = host.advise(_nudge_text(host, verdict["notes"]))
            stdout.write(out)
            return exit_fn(0)
        return exit_fn(0)

    except SystemExit:
        raise
    except Exception:
        return exit_fn(0)  # absolute fail-open backstop
