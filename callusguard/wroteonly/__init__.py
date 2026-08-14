"""wroteonly — verify an agent touched only what it said it would.

Declare the intended write set before the agent acts, snapshot a baseline, then
diff what actually happened against what was declared and surface only the errors
the run introduced.

Host-neutral core; Claude Code and OpenAI Codex adapters in `wroteonly.hosts`.
"""

__version__ = "0.1.0"

from ..core.verdict import Verdict, ALLOW, WARN, DENY, ESCALATE  # noqa: F401
from .declare import Declaration, DeclarationError  # noqa: F401

__all__ = [
    "Verdict", "ALLOW", "WARN", "DENY", "ESCALATE",
    "Declaration", "DeclarationError", "__version__",
]
