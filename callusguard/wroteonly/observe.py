"""What the agent actually touched.

Two independent observers, deliberately. They answer different questions and they
fail in different directions:

    FILESYSTEM SCAN (ground truth)
        Fingerprint every in-scope file before the run and again after, then diff.
        Sees every write regardless of how it happened — Write tool, Edit tool,
        a Bash heredoc, `sed -i`, a python script the agent wrote and then ran.

    HOOK EVENT STREAM (attribution)
        Record each PreToolUse/PostToolUse call the host reports. Tells you *which
        tool call* produced a write, which the scan cannot. Blind to anything that
        happens inside a Bash command.

WHY BOTH, AND WHY THE SCAN IS AUTHORITATIVE
    Tool-level path blocking is known to be routable-around: block Write and the
    model uses a Bash heredoc; block `rm` and it uses `perl -e "unlink(...)"`. Worse,
    under `--permission-mode bypassPermissions` Claude Code's Write/Edit run
    in-process via `fs.writeFileSync` and skip sandbox filesystem isolation entirely
    (anthropics/claude-code#29048) — which is exactly the mode the archref job runs.

    So a verifier that trusts only the hook stream is verifying the honest path. The
    scan is what makes the answer true. The hook stream is a convenience for
    explaining it.

    See docs/existence-checks-2026-08-14.md §B.3.

FINGERPRINTS
    (size, mtime_ns, sha256). Content hashing is what makes "modified" mean modified
    — an agent that rewrites a file with identical bytes did not change it, and a
    `touch` is not an edit. Files larger than `max_hash_bytes` record a null hash and
    fall back to (size, mtime_ns); this is stated in the report so a reader knows
    which rows are hash-backed. Default cap is 8 MiB, which hashes a source tree in
    well under a second and declines to hash checked-in binaries.

INVARIANT
    Scanning never mutates the tree and never follows symlinks out of the root.
"""

from __future__ import annotations

import hashlib
import os
import stat as _stat

#: Directories never worth scanning. A dot-directory is skipped by default, so
#: this list only needs the non-dotted offenders.
DEFAULT_IGNORE_DIRS = frozenset({
    "node_modules", "__pycache__", "venv", ".venv", "env",
    "dist", "build", "target", "vendor", "site-packages",
    ".git", ".hg", ".svn", ".jj", ".mypy_cache", ".pytest_cache",
    ".ruff_cache", ".tox", ".next", ".nuxt", ".cache", ".gradle",
})

DEFAULT_MAX_HASH_BYTES = 8 * 1024 * 1024
DEFAULT_MAX_FILES = 200_000

CREATED = "create"
MODIFIED = "modify"
DELETED = "delete"


class ScanLimitExceeded(RuntimeError):
    """The tree is larger than `max_files`; the scan would not be trustworthy."""


def _sha256(path: str, max_bytes: int) -> str | None:
    try:
        if os.path.getsize(path) > max_bytes:
            return None
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            for chunk in iter(lambda: fh.read(1024 * 256), b""):
                h.update(chunk)
        return h.hexdigest()
    except (OSError, ValueError):
        return None


def snapshot(
    root: str,
    ignore_dirs=DEFAULT_IGNORE_DIRS,
    max_hash_bytes: int = DEFAULT_MAX_HASH_BYTES,
    max_files: int = DEFAULT_MAX_FILES,
    include_dotdirs: bool = False,
) -> dict:
    """Fingerprint every regular file under `root`.

    Returns {rel_posix_path: [size, mtime_ns, sha256_or_None]}.

    Symlinks are recorded by their own lstat, never followed — a symlinked
    directory could otherwise walk the scan out of the root or into a cycle.
    """
    root = os.path.realpath(root)
    out: dict = {}
    count = 0

    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames[:] = [
            d for d in dirnames
            if d not in ignore_dirs and (include_dotdirs or not d.startswith("."))
        ]
        for name in filenames:
            full = os.path.join(dirpath, name)
            try:
                st = os.lstat(full)
            except OSError:
                continue
            if not (_stat.S_ISREG(st.st_mode) or _stat.S_ISLNK(st.st_mode)):
                continue
            count += 1
            if count > max_files:
                raise ScanLimitExceeded(
                    "more than %d files under %s; narrow the scan scope"
                    % (max_files, root))
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            digest = None if os.path.islink(full) else _sha256(full, max_hash_bytes)
            out[rel] = [st.st_size, st.st_mtime_ns, digest]
    return out


def diff(before: dict, after: dict) -> list:
    """Compare two snapshots into a list of change records.

    Each record is {"path", "kind", "hashed"}. `hashed` is False when the verdict
    rests on (size, mtime) rather than content — surfaced so a report never implies
    more certainty than it has.

    A file whose (size, mtime_ns) changed but whose hash did not is NOT reported as
    modified. Rewriting identical bytes is not a change, and treating it as one
    would make every formatter-on-save look like a violation.
    """
    changes = []
    before_keys, after_keys = set(before), set(after)

    for path in sorted(after_keys - before_keys):
        changes.append({"path": path, "kind": CREATED,
                        "hashed": after[path][2] is not None})

    for path in sorted(before_keys - after_keys):
        changes.append({"path": path, "kind": DELETED,
                        "hashed": before[path][2] is not None})

    for path in sorted(before_keys & after_keys):
        b, a = before[path], after[path]
        b_hash, a_hash = b[2], a[2]
        if b_hash is not None and a_hash is not None:
            if b_hash != a_hash:
                changes.append({"path": path, "kind": MODIFIED, "hashed": True})
        else:
            # One side was too large (or a symlink) to hash. Fall back to
            # size+mtime and say so.
            if b[0] != a[0] or b[1] != a[1]:
                changes.append({"path": path, "kind": MODIFIED, "hashed": False})

    return changes


# ---------------------------------------------------------------------------
# hook-event attribution
# ---------------------------------------------------------------------------

#: Tools whose `tool_input` names a file path directly. Everything else (Bash,
#: shell, apply_patch) is left to the filesystem scan, because parsing intent out
#: of a shell command is exactly the guesswork this tool refuses to do.
PATH_BEARING_TOOLS = {
    "Write": ("file_path",),
    "Edit": ("file_path",),
    "MultiEdit": ("file_path",),
    "NotebookEdit": ("notebook_path", "file_path"),
    "Read": ("file_path",),
}


def paths_from_tool_input(tool_name: str, tool_input) -> list:
    """Best-effort extraction of file paths a tool call names.

    Attribution only. A miss here costs an explanatory line in the report; it
    never costs a detection, because the scan is what detects.
    """
    if not isinstance(tool_input, dict):
        return []
    keys = PATH_BEARING_TOOLS.get(tool_name)
    if not keys:
        return []
    found = []
    for key in keys:
        value = tool_input.get(key)
        if isinstance(value, str) and value.strip():
            found.append(value)
    return found


def attribute(changes: list, observations: list) -> list:
    """Annotate each change with the tool call that most plausibly caused it.

    Matches on path only, taking the last observation that named the path — the
    last writer is the one that produced the final content. Adds `tool` and
    `tool_use_id` when a match is found; leaves them absent when not, which is the
    honest answer for a Bash-mediated write.
    """
    by_path: dict = {}
    for obs in observations:
        for raw in obs.get("paths") or []:
            key = raw.replace(os.sep, "/").lstrip("./")
            by_path[key] = obs
            by_path[os.path.basename(key)] = obs

    annotated = []
    for change in changes:
        record = dict(change)
        obs = by_path.get(change["path"]) or by_path.get(
            os.path.basename(change["path"]))
        if obs:
            if obs.get("tool"):
                record["tool"] = obs["tool"]
            if obs.get("tool_use_id"):
                record["tool_use_id"] = obs["tool_use_id"]
        annotated.append(record)
    return annotated
