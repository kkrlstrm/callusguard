"""Where a run's evidence lives while the run is happening.

One directory per run, keyed by the host's session id:

    $WROTEONLY_STATE/runs/<run_id>/
        declaration.json   what the agent said it would touch
        baseline.json      file fingerprints + checker findings, taken before it acted
        observed.jsonl     one line per tool call the hooks saw (append-only)
        verdict.json       the final decision, written by the Stop gate

Default `$WROTEONLY_STATE` is `~/.wroteonly`, matching the `~/.agent-guard` and
`~/.codex-guard` convention.

WHY A DIRECTORY AND NOT A DAEMON
    The hooks are separate short-lived processes — PreToolUse, PostToolUse and Stop
    each run as their own invocation, and on Codex they may not even share a parent.
    The filesystem is the only state they reliably share. It also means a run's
    evidence survives a crash and can be inspected afterwards, which a daemon's
    memory would not.

INVARIANT
    Writes are atomic (temp file + os.replace) so a hook killed mid-write can never
    leave a half-parsed declaration that reads as an empty, permissive one. Appends
    to observed.jsonl are single `write()` calls of one line, which is atomic enough
    for the sizes involved and keeps the observer off the critical path.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import time

_SAFE_ID = re.compile(r"[^A-Za-z0-9._-]")


def state_root() -> str:
    return os.environ.get("WROTEONLY_STATE") or os.path.join(
        os.path.expanduser("~"), ".wroteonly")


def safe_run_id(run_id: str) -> str:
    """Make a host-supplied session id safe to use as a directory name."""
    cleaned = _SAFE_ID.sub("-", (run_id or "").strip())[:120]
    return cleaned or "unknown"


class RunState:
    """The on-disk evidence directory for one agent run."""

    DECLARATION = "declaration.json"
    BASELINE = "baseline.json"
    OBSERVED = "observed.jsonl"
    VERDICT = "verdict.json"

    def __init__(self, run_id: str, root: str | None = None):
        self.run_id = safe_run_id(run_id)
        self.dir = os.path.join(root or state_root(), "runs", self.run_id)

    # -- lifecycle ----------------------------------------------------------

    def ensure(self) -> "RunState":
        os.makedirs(self.dir, exist_ok=True)
        return self

    def exists(self) -> bool:
        return os.path.isdir(self.dir)

    def destroy(self) -> None:
        shutil.rmtree(self.dir, ignore_errors=True)

    def path(self, name: str) -> str:
        return os.path.join(self.dir, name)

    # -- atomic json --------------------------------------------------------

    def write_json(self, name: str, data) -> str:
        self.ensure()
        target = self.path(name)
        fd, tmp = tempfile.mkstemp(dir=self.dir, prefix=".tmp-", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2, sort_keys=True)
                fh.write("\n")
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, target)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        return target

    def read_json(self, name: str, default=None):
        try:
            with open(self.path(name), "r", encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return default

    def has(self, name: str) -> bool:
        return os.path.exists(self.path(name))

    # -- append-only observations -------------------------------------------

    def append_observation(self, event: dict) -> None:
        """Append one observed tool call. Never raises into the caller.

        An observer that crashes the agent it is observing is worse than an
        observer that misses a line, so this swallows I/O errors — the missing
        line degrades the hook-attribution stream, and the filesystem scan (the
        ground truth) is unaffected.
        """
        try:
            self.ensure()
            event.setdefault("ts", time.time())
            line = json.dumps(event, sort_keys=True, separators=(",", ":"))
            with open(self.path(self.OBSERVED), "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except (OSError, ValueError, TypeError):
            pass

    def observations(self) -> list:
        out = []
        try:
            with open(self.path(self.OBSERVED), "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        out.append(json.loads(line))
                    except ValueError:
                        continue
        except OSError:
            pass
        return out


def prune(older_than_days: float = 7.0, root: str | None = None) -> int:
    """Delete run directories older than `older_than_days`. Returns the count.

    The baseline is deliberately ephemeral — it is captured per invocation and
    never committed, which is the property that distinguishes it from betterer's
    and linthell's committed baselines. Nothing here is meant to be kept.
    """
    base = os.path.join(root or state_root(), "runs")
    cutoff = time.time() - (older_than_days * 86400)
    removed = 0
    try:
        entries = os.listdir(base)
    except OSError:
        return 0
    for name in entries:
        target = os.path.join(base, name)
        try:
            if os.path.isdir(target) and os.path.getmtime(target) < cutoff:
                shutil.rmtree(target, ignore_errors=True)
                removed += 1
        except OSError:
            continue
    return removed
