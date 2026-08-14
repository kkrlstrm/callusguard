#!/usr/bin/env python3
"""PreToolUse guard entry point. Host is auto-detected; keep this file byte-stable.

Codex trust-pins the hook command by hash (`trusted_hash` in config.toml), so edits
here re-prompt for trust. All churn belongs in the rules JSON, which is not hashed.

Ruleset resolution:
  $AGENT_GUARD_RULES / $CODEX_GUARD_RULES  -> that file (per detected host)
  else                                     -> callusguard/guard/rules/starter.rules.json

Bias: any error -> exit 0 (allow). A guard bug must never wedge a session.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from callusguard.guard.runner import run     # noqa: E402

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT = os.path.join(HERE, "callusguard", "guard", "rules", "starter.rules.json")

if __name__ == "__main__":
    try:
        run(default_ruleset=DEFAULT, ruleset_env="RULES")
    except SystemExit:
        raise
    except Exception:
        sys.exit(0)  # absolute fail-open backstop
