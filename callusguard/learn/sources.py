"""Normalization boundary for host-specific execution telemetry."""

from __future__ import annotations

import json
from typing import Iterable, Mapping

from .models import ToolAttempt


def normalize_attempt(source: str, row: Mapping) -> ToolAttempt:
    """Normalize a Claude/Codex/Gemini-shaped row into one learning object."""
    tool_input = row.get("tool_input") or {}
    command = row.get("command")
    if command is None and isinstance(tool_input, Mapping):
        command = tool_input.get("command")
    if command is None and row.get("arguments"):
        arguments = row.get("arguments")
        if isinstance(arguments, str):
            try:
                parsed = json.loads(arguments)
            except ValueError:
                parsed = None
            if isinstance(parsed, Mapping):
                command = parsed.get("command") or parsed.get("cmd")
            else:
                command = arguments
        elif isinstance(arguments, Mapping):
            command = arguments.get("command") or arguments.get("cmd")

    status = row.get("status")
    if status is None and row.get("exit_code") is not None:
        status = "success" if int(row["exit_code"]) == 0 else "failure"
    error = row.get("error")
    if status == "failure" and not error:
        error = row.get("output")

    return ToolAttempt(
        source=source,
        tool_name=str(row.get("tool_name") or row.get("tool") or "unknown"),
        status=str(status or "unknown"),
        command=command,
        error=error,
        session_id=row.get("session_id"),
        tool_call_id=row.get("tool_call_id") or row.get("call_id"),
        model=row.get("model"),
        ts=str(row.get("started_at") or row.get("ts") or "") or None,
    )


def normalize_attempts(source: str, rows: Iterable[Mapping]) -> list[ToolAttempt]:
    return [normalize_attempt(source, row) for row in rows]
