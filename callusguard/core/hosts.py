"""Host identity and wire format — the one definition, used by every component.

Before the merge this knowledge was spread across four places: agent-guard's runner
(env prefix, audit path, brand string), codex-guard's near-identical copy, and
wroteonly's `hosts/` module (wire emitters). Adding a host meant editing all of them;
adding an outcome meant editing more.

A Host is two things:

    IDENTITY   what this host's files and environment variables are called.
               `~/.agent-guard/audit.jsonl` vs `~/.codex-guard/audit.jsonl`,
               `AGENT_GUARD_RULES` vs `CODEX_GUARD_RULES`. The guard needs only this.

    WIRE       how to say allow / advise / deny / block / keep-going to this host.
               wroteonly needs this, because the hosts genuinely differ at Stop.

THE SURPRISE FROM THE SURVEY
    For the *guard*, the two hosts do not differ at all. agent-guard and codex-guard's
    engines are byte-identical and their enforcement contracts are the same three
    mechanisms: exit 2 hard-blocks, `permissionDecision:"deny"` soft-denies,
    `additionalContext` nudges. Verified against Codex 0.140.0-alpha.2 and the current
    Claude Code hook docs.

    So the "two guards" were one program wearing two names, and the host abstraction
    they needed was a naming table, not an adapter.

WHERE THE HOSTS REALLY DIVERGE (wroteonly's problem, not the guard's)

    capability                Claude Code              Codex
    ---------------------------------------------------------------------
    PreToolUse hard block     exit 2                   exit 2
    PostToolUse can block     NO ("tool already ran")  YES {"decision":"block"}
    Stop forces continuation  exit 2                   {"decision":"block", reason}
    Rewrite a tool's input    not available            updatedInput

INVARIANT
    A Host decides nothing. It converts a payload in and a verdict out. Every
    behavioural difference between the two products lives in the components, not here.
"""

from __future__ import annotations

import json
import os

PRE_TOOL_USE = "PreToolUse"
POST_TOOL_USE = "PostToolUse"
STOP = "Stop"
SESSION_START = "SessionStart"


class Host:
    """Base host. Subclasses override only what genuinely differs."""

    #: Canonical slug, e.g. "claude-code".
    name = "generic"
    #: What the guard calls itself in messages to the model. Preserved verbatim from
    #: the pre-merge repos so existing audit trails and model-facing text do not shift.
    brand = "callusguard"
    #: Environment-variable prefix, e.g. "AGENT_GUARD" -> AGENT_GUARD_RULES.
    env_prefix = "CALLUS"
    #: Per-host state directory under $HOME. Kept separate on purpose: the audit log
    #: is a hash chain, and concatenating two chains invalidates both.
    state_dir = ".callusguard"
    #: Can this host block at PostToolUse?
    post_can_block = False

    # -- identity -----------------------------------------------------------

    def env(self, suffix: str, default=None):
        """Read `<PREFIX>_<SUFFIX>` from the environment."""
        return os.environ.get("%s_%s" % (self.env_prefix, suffix), default)

    def state_path(self, *parts: str) -> str:
        return os.path.join(os.path.expanduser("~"), self.state_dir, *parts)

    # -- input --------------------------------------------------------------

    def parse(self, payload: dict) -> dict:
        """Normalise a hook payload to the fields every component uses.

        The field names are spelled identically on both hosts — `session_id`, `cwd`,
        `tool_name`, `tool_input`, `tool_use_id`, `permission_mode`,
        `stop_hook_active` — which is what makes one normaliser sufficient.
        """
        tool_input = payload.get("tool_input")
        return {
            "host": self.name,
            "event": payload.get("hook_event_name") or "",
            "run_id": payload.get("session_id") or "",
            "cwd": payload.get("cwd") or os.getcwd(),
            "tool_name": payload.get("tool_name") or "",
            "tool_input": tool_input if isinstance(tool_input, dict) else {},
            "tool_use_id": payload.get("tool_use_id") or "",
            "permission_mode": payload.get("permission_mode") or "",
            "stop_hook_active": bool(payload.get("stop_hook_active")),
            "transcript_path": payload.get("transcript_path") or "",
        }

    # -- output -------------------------------------------------------------
    # Emitters return (stdout, stderr, exit_code) so callers stay pure functions of
    # a verdict and tests need no subprocess.

    def noop(self):
        return "", "", 0

    def advise(self, message: str, event: str = PRE_TOOL_USE):
        return json.dumps({
            "hookSpecificOutput": {
                "hookEventName": event,
                "additionalContext": message,
            }
        }), "", 0

    def deny_tool(self, message: str, event: str = PRE_TOOL_USE):
        return json.dumps({
            "hookSpecificOutput": {
                "hookEventName": event,
                "permissionDecision": "deny",
                "permissionDecisionReason": message,
            }
        }), "", 0

    def block_tool(self, message: str):
        """Hard block. exit 2 on both hosts; survives bypassPermissions."""
        return "", "BLOCKED by %s: %s\n" % (self.brand, message), 2

    def keep_going(self, message: str):
        """Refuse to let the agent stop, so it can fix what it broke."""
        raise NotImplementedError


class ClaudeCode(Host):
    """Claude Code.

    Stop: exit 2 prevents Claude from stopping and surfaces the message.
    PostToolUse: cannot block — exit 2 "has no blocking effect on this event".
    """

    name = "claude-code"
    brand = "agent-guard"
    env_prefix = "AGENT_GUARD"
    state_dir = ".agent-guard"
    post_can_block = False

    def keep_going(self, message: str):
        return "", message + "\n", 2


class Codex(Host):
    """OpenAI Codex CLI.

    Stop: `{"decision":"block","reason":...}` tells Codex to continue and build a
    continuation prompt from the reason text.
    PostToolUse: accepts the same block shape.

    Trust-on-first-use: Codex records a `trusted_hash` of the hook command in
    config.toml. Entry scripts must stay byte-stable; put churn in JSON, which is
    not hashed.
    """

    name = "codex"
    brand = "codex-guard"
    env_prefix = "CODEX_GUARD"
    state_dir = ".codex-guard"
    post_can_block = True

    def keep_going(self, message: str):
        return json.dumps({"decision": "block", "reason": message}), "", 0

    def block_after_tool(self, message: str):
        return json.dumps({"decision": "block", "reason": message}), "", 0


HOSTS = {ClaudeCode.name: ClaudeCode, Codex.name: Codex}


def get(name: str) -> Host:
    """Resolve a host by slug. Unknown names fall back to Claude Code."""
    return HOSTS.get((name or "").strip().lower(), ClaudeCode)()


def detect(payload: dict | None = None) -> Host:
    """Work out which host we are inside.

    Precedence, strongest evidence first:

        1. an explicit override        — an unattended job should not have to infer
        2. THE PAYLOAD SHAPE           — direct evidence about *this call*
        3. environment markers         — ambient, and inherited by child processes
        4. Claude Code as the default

    THE PAYLOAD MUST OUTRANK THE ENVIRONMENT, and this ordering was a bug once.
    `CLAUDECODE=1` is exported into every shell Claude Code spawns, so a Codex hook
    invoked from a terminal inside a Claude Code session inherits it. Checking the
    environment first identified that call as Claude Code and then used the wrong
    brand, the wrong `<PREFIX>_RULES` variable, and the wrong audit log — silently.

    The environment says which process tree we are in. The payload says who is
    actually calling. When they disagree, the caller is right.

    (This could not happen before the merge only because each repo hard-coded its
    one host. It is a hazard the merge creates and must therefore answer.)
    """
    forced = (os.environ.get("CALLUS_HOST")
              or os.environ.get("WROTEONLY_HOST") or "").strip().lower()
    if forced in HOSTS:
        return HOSTS[forced]()

    if payload:
        # Codex sends `turn_id` (and `model`) on every event; Claude Code sends
        # `prompt_id` and never `turn_id`.
        if payload.get("turn_id") and "prompt_id" not in payload:
            return Codex()
        if payload.get("prompt_id"):
            return ClaudeCode()

    if os.environ.get("CODEX_HOME") or os.environ.get("CODEX_SANDBOX"):
        return Codex()
    if os.environ.get("CLAUDE_PROJECT_DIR") or os.environ.get("CLAUDECODE"):
        return ClaudeCode()

    return ClaudeCode()
