"""Small durable knowledge store.

JSONL is intentional for v1: append-friendly, diffable, portable, and independent of
the telemetry databases. The knowledge survives when an intervention is rejected or
retired; that separation is the point.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Iterable

from .models import Intervention, Pattern


DEFAULT_HOME = Path(os.environ.get("CALLUS_HOME", Path.home() / ".callusguard"))


class KnowledgeStore:
    def __init__(self, root: str | os.PathLike | None = None):
        self.root = Path(root) if root else DEFAULT_HOME / "knowledge"
        self.patterns_path = self.root / "patterns.jsonl"
        self.interventions_path = self.root / "interventions.jsonl"

    def init(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        for path in (self.patterns_path, self.interventions_path):
            path.touch(exist_ok=True)

    @staticmethod
    def _read(path: Path) -> list[dict]:
        if not path.exists():
            return []
        rows = []
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
        return rows

    @staticmethod
    def _append(path: Path, row: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, sort_keys=True) + "\n")

    def add_pattern(self, pattern: Pattern) -> None:
        self._append(self.patterns_path, pattern.to_dict())

    def add_intervention(self, intervention: Intervention) -> None:
        self._append(self.interventions_path, intervention.to_dict())

    def patterns(self) -> list[dict]:
        return self._latest_by_id(self._read(self.patterns_path))

    def interventions(self) -> list[dict]:
        return self._latest_by_id(self._read(self.interventions_path))

    @staticmethod
    def _latest_by_id(rows: Iterable[dict]) -> list[dict]:
        latest = {}
        for row in rows:
            if row.get("id"):
                latest[row["id"]] = row
        return list(latest.values())
