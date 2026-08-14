"""codex-logger: telemetry for OpenAI Codex CLI, read from rollout JSONL files.

The Codex sibling of cc-logger. Instead of consuming lifecycle hooks (Codex's
PreToolUse/PostToolUse only fire for shell commands), this tails the append-only
rollout files Codex already writes to ~/.codex/sessions/**, which record every
session, turn, tool call (exec_command / apply_patch / MCP), model, and per-turn
token count. Hook-independent by design.
"""
from .parse import parse_session, Session, ToolCall, Message, Turn  # noqa: F401
from .store import open_store, SQLiteStore  # noqa: F401
from .ingest import ingest_once, watch, find_rollouts  # noqa: F401

__version__ = "0.1.0"
