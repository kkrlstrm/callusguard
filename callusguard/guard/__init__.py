"""The policy engine — rules in JSON, four graded outcomes, most-restrictive-wins.

Two enforcement biases, one engine:
  - fail-OPEN nudges: telemetry-driven reminders injected as additionalContext;
  - hard-BLOCK backstop: a read-only-DB firewall for a least-privilege sub-agent.

The backstop is a backstop, not a true fail-closed boundary — the hook itself falls
open on any guard error, so the durable guarantee is a SELECT-only DB role.

Actions: monitor (log-only) | nudge (advise, tool runs) | deny (soft, model told) |
block (exit 2, survives bypassPermissions). Ranked; most restrictive wins.
"""
from .engine import ACTION_RANK, evaluate, resolve, verify_rules, warn_rules  # noqa: F401
