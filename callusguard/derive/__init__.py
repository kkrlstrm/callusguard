"""Telemetry -> candidate rules. The seam that makes the loop a loop.

Reads recurring tool failures (from cc-logger's Postgres, codex-logger's SQLite, or
a JSONL export), clusters them by normalised error signature, and emits candidate
rules as `action: "monitor"` — never auto-armed. A human promotes monitor -> nudge
-> block.

This is the part the surveyed alternatives do not have: guards grown from what has
actually gone wrong in your runs, rather than written from imagination.
"""
