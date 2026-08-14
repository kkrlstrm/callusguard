#!/usr/bin/env python3
"""Hard-blocking read-only backstop for a least-privilege sub-agent.

Wire into the sub-agent's frontmatter (Bash matcher) so it constrains ONLY that
sub-agent, never the main session. Hard-blocks (exit 2) any detected DB mutation.

This is a backstop, not a true fail-closed boundary: it blocks on a *matched*
mutation, but the hook itself falls open on any guard error. The durable guarantee
for a read-only workflow is a SELECT-only DB role, not this regex.

Ruleset: $AGENT_GUARD_READONLY_RULES / $CODEX_GUARD_READONLY_RULES, else
callusguard/guard/rules/readonly-db.rules.json
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from callusguard.guard.runner import run     # noqa: E402

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT = os.path.join(HERE, "callusguard", "guard", "rules", "readonly-db.rules.json")

if __name__ == "__main__":
    try:
        run(default_ruleset=DEFAULT, ruleset_env="READONLY_RULES")
    except SystemExit:
        raise
    except Exception:
        sys.exit(0)
