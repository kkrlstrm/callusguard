"""Hash-chained, tamper-evident audit log. Pure stdlib (hashlib + json).

Each line is one event plus two chain fields:

    {..event.., "prev": <hash of previous line>, "hash": sha256(prev + canon(event))}

Any later edit to an earlier line breaks the chain from that point on, so `verify()`
can prove the log was not altered.

THIS FILE REPLACES THREE COPIES
    agent-guard/guard/audit.py (110 lines), codex-guard/guard/audit.py (110 lines,
    12 differing), and kgg/audit.py (108 lines) were three hand-maintained copies of
    the same algorithm. The only real difference was the default path and the env var
    name, both of which are now supplied by a Host.

    The behaviour below is byte-for-byte the pre-merge behaviour, including the exact
    hash construction — `sha256(prev + canonical_json(event))` with `sort_keys=True`
    and `(",", ":")` separators. **Changing any of that would invalidate every
    existing audit chain on disk**, so it is pinned by test and must not be "cleaned up".

WHY THE LOG FILES STAY SEPARATE
    The code merges; the chains do not. A hash chain is per-file — concatenating
    `~/.agent-guard/audit.jsonl` and `~/.codex-guard/audit.jsonl` would invalidate
    both, because line N's `prev` would no longer match the line above it. Each host
    keeps its own log, and `verify` is run per host.

INVARIANT
    Writing is best-effort from the caller's perspective: `append()` raises on I/O
    error, and every hot-path caller swallows it. An audit failure must never block a
    tool call.
"""

from __future__ import annotations

import hashlib
import json
import os

GENESIS = "GENESIS"


def default_path(host=None) -> str:
    """Where this host's audit log lives.

    Honours `<PREFIX>_AUDIT` (e.g. `AGENT_GUARD_AUDIT`, `CODEX_GUARD_AUDIT`) so every
    pre-merge override keeps working unchanged.
    """
    if host is None:
        from . import hosts
        host = hosts.detect()
    override = host.env("AUDIT")
    if override:
        return override
    return host.state_path("audit.jsonl")


def _canon(event) -> str:
    return json.dumps(event, sort_keys=True, separators=(",", ":"))


def _entry_hash(prev: str, event) -> str:
    return hashlib.sha256((prev + _canon(event)).encode("utf-8")).hexdigest()


def last_hash(path: str) -> str:
    """Hash of the final line, or GENESIS if the log is empty or absent."""
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            back = min(size, 8192)
            fh.seek(size - back)
            tail = fh.read().decode("utf-8", "ignore").strip().splitlines()
        if not tail:
            return GENESIS
        return json.loads(tail[-1]).get("hash", GENESIS)
    except FileNotFoundError:
        return GENESIS
    except Exception:
        return GENESIS


def append(event, path=None, host=None) -> str:
    """Append one event to the chain and return its hash.

    Raises on I/O error. Hot-path callers swallow that — auditing is never
    load-bearing enough to justify wedging a session.
    """
    path = path or default_path(host)
    prev = last_hash(path)
    record = dict(event)
    record["prev"] = prev
    record["hash"] = _entry_hash(prev, event)
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "a") as fh:
        fh.write(json.dumps(record) + "\n")
    return record["hash"]


def verify(path=None, host=None):
    """Recompute the chain. Returns (ok: bool, first_bad_line: int | None)."""
    path = path or default_path(host)
    prev = GENESIS
    try:
        fh = open(path)
    except FileNotFoundError:
        return True, None
    with fh:
        for i, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                record = json.loads(line)
            except Exception:
                return False, i
            event = {k: v for k, v in record.items() if k not in ("prev", "hash")}
            expected = _entry_hash(prev, event)
            if record.get("prev") != prev or record.get("hash") != expected:
                return False, i
            prev = expected
    return True, None


def main(argv=None) -> int:
    """`python3 -m callusguard.core.audit [verify|tail] [path]`"""
    import sys
    argv = list(sys.argv[1:] if argv is None else argv)
    cmd = argv[0] if argv else "verify"
    path = argv[1] if len(argv) > 1 else default_path()

    if cmd == "verify":
        ok, bad = verify(path)
        if ok:
            print("OK — chain intact (%s)" % path)
            return 0
        print("TAMPERED — chain breaks at line %s (%s)" % (bad, path))
        return 1
    if cmd == "tail":
        try:
            with open(path) as fh:
                for line in fh.readlines()[-20:]:
                    print(line.rstrip())
        except FileNotFoundError:
            print("(no audit log at %s)" % path)
        return 0
    print("usage: audit.py [verify|tail] [path]")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
