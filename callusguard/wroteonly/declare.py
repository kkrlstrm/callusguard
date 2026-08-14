"""The declaration: what the agent said it was going to touch.

This is the half nobody else has. Witan reports the *actual* accesses and leaves the
comparison to the model's head; `agent-spec` enforces *human-authored* boundary globs
from a spec file. A wroteonly declaration is stated by the agent, per invocation, in
its own words — which is what lets us catch an in-policy but out-of-intent write:

    policy says      "you may write anywhere under src/"
    declaration says "I will create src/auth/login.py and modify src/app.py"
    the agent wrote   src/auth/login.py, src/app.py, and src/billing/rates.py

    A path allowlist passes that. A declaration does not.

DATA SHAPE

    {
      "run_id":   "sess-abc123",
      "intent":   "add password reset to the auth module",
      "create":   ["src/auth/reset.py"],
      "modify":   ["src/auth/__init__.py"],
      "delete":   [],
      "forbid":   ["**/*.env", "config/**"],
      "fail_direction": "open" | "closed",
      "strict_unfulfilled": false
    }

    `create`/`modify`/`delete` are separate because they are different risks. Creating
    a file the agent named is nearly always fine; silently rewriting one it did not
    name is the failure this tool exists to catch. A path in `modify` also permits
    creation (an edit to a file that turns out not to exist yet is not an escape); a
    path in `create` does NOT permit modifying an existing file.

    `forbid` always wins over the allow lists. It is the un-liftable floor — Ralio's
    "hard spend limits still apply and cannot be bypassed by any auth method"
    (docs/evidence-corpus.md §2.3) expressed for paths.

GLOB SEMANTICS
    gitignore-style, implemented here rather than with `fnmatch`, because
    `fnmatch("a/b/c.md", "a/*.md")` is True — `*` there crosses directory
    separators, which would silently widen every declaration a user writes.

        **/   matches zero or more leading directories
        **    matches anything, including separators
        *     matches anything except a separator
        ?     matches one character except a separator

    Paths are normalised to be relative to the run root and POSIX-separated before
    matching, so a declaration is portable and a `../` escape cannot match anything.

INVARIANT
    A Declaration never widens itself. Parsing is total: an unparseable declaration
    raises, and the caller decides the fail direction — it never degrades to an
    empty (and therefore permissive-looking) declaration.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field

VALID_FAIL_DIRECTIONS = ("open", "closed")


class DeclarationError(ValueError):
    """A declaration could not be parsed or is internally inconsistent."""


# ---------------------------------------------------------------------------
# glob → regex
# ---------------------------------------------------------------------------

def _glob_to_regex(pattern: str) -> re.Pattern:
    """Compile a gitignore-flavoured glob.

    Order matters: `**/` is consumed before `**`, and `**` before `*`, or the
    single-star rule would eat the first star of a double.
    """
    i, out = 0, ["^"]
    n = len(pattern)
    while i < n:
        c = pattern[i]
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif c == "*":
            out.append("[^/]*")
            i += 1
        elif c == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(c))
            i += 1
    out.append("$")
    return re.compile("".join(out))


def normalize_path(path: str, root: str) -> str | None:
    """Make `path` relative to `root`, POSIX-separated.

    Returns None when the path lies outside the root — such a path can never
    satisfy a declaration, and the caller reports it as an escape rather than
    silently matching it against patterns it cannot meaningfully match.
    """
    try:
        abs_path = os.path.realpath(os.path.join(root, path))
        abs_root = os.path.realpath(root)
    except (OSError, ValueError):
        return None
    if abs_path == abs_root:
        return None
    prefix = abs_root + os.sep
    if not abs_path.startswith(prefix):
        return None
    return abs_path[len(prefix):].replace(os.sep, "/")


class PatternSet:
    """A compiled list of globs, kept alongside their source text for reporting."""

    def __init__(self, patterns=()):
        self.patterns = tuple(patterns)
        self._compiled = tuple(_glob_to_regex(p) for p in self.patterns)

    def __bool__(self) -> bool:
        return bool(self.patterns)

    def __iter__(self):
        return iter(self.patterns)

    def match(self, rel_path: str) -> str | None:
        """Return the first pattern that matches, or None."""
        for pattern, rx in zip(self.patterns, self._compiled):
            if rx.match(rel_path):
                return pattern
        return None


# ---------------------------------------------------------------------------
# the declaration
# ---------------------------------------------------------------------------

@dataclass
class Declaration:
    """What the agent said it would touch, and how to fail if we cannot tell."""

    run_id: str
    intent: str = ""
    create: PatternSet = field(default_factory=PatternSet)
    modify: PatternSet = field(default_factory=PatternSet)
    delete: PatternSet = field(default_factory=PatternSet)
    forbid: PatternSet = field(default_factory=PatternSet)
    fail_direction: str = "open"
    strict_unfulfilled: bool = False
    root: str = "."
    #: [{"name": ..., "command": ...}] — the project's own checks, run once before
    #: the agent acts and once after, then set-differenced. Empty means the
    #: declaration diff is the only check, which is a perfectly valid way to run.
    checks: tuple = ()

    # -- classification -----------------------------------------------------

    def permits(self, rel_path: str, kind: str) -> tuple[bool, str]:
        """Is a `kind` operation on `rel_path` inside the declaration?

        `kind` is "create" | "modify" | "delete". Returns (permitted, why) where
        `why` names the deciding pattern, so a report can quote the rule that
        allowed or refused a write rather than only its verdict.
        """
        forbidden = self.forbid.match(rel_path)
        if forbidden:
            return False, "forbid:%s" % forbidden

        if kind == "create":
            # `modify` subsumes `create`: editing a file that did not exist yet is
            # not an escape from an intent that named it.
            hit = self.create.match(rel_path) or self.modify.match(rel_path)
            bucket = "create" if self.create.match(rel_path) else "modify"
        elif kind == "modify":
            hit = self.modify.match(rel_path)
            bucket = "modify"
        elif kind == "delete":
            hit = self.delete.match(rel_path)
            bucket = "delete"
        else:
            raise ValueError("unknown operation kind %r" % (kind,))

        if hit:
            return True, "%s:%s" % (bucket, hit)
        return False, "undeclared"

    @property
    def declares_anything(self) -> bool:
        return bool(self.create or self.modify or self.delete)

    def all_allow_patterns(self) -> list[str]:
        return list(self.create) + list(self.modify) + list(self.delete)

    # -- serialisation ------------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "run_id": self.run_id,
            "intent": self.intent,
            "create": list(self.create),
            "modify": list(self.modify),
            "delete": list(self.delete),
            "forbid": list(self.forbid),
            "fail_direction": self.fail_direction,
            "strict_unfulfilled": self.strict_unfulfilled,
            "root": self.root,
            "checks": [dict(c) for c in self.checks],
        }

    @classmethod
    def from_dict(cls, data: dict, root: str | None = None) -> "Declaration":
        if not isinstance(data, dict):
            raise DeclarationError("declaration must be a JSON object, got %s"
                                   % type(data).__name__)

        run_id = data.get("run_id") or ""
        if not isinstance(run_id, str) or not run_id.strip():
            raise DeclarationError("declaration needs a non-empty string 'run_id'")

        def _patterns(key: str) -> PatternSet:
            raw = data.get(key, [])
            if raw is None:
                raw = []
            if isinstance(raw, str):
                raw = [raw]
            if not isinstance(raw, list) or not all(isinstance(p, str) for p in raw):
                raise DeclarationError(
                    "'%s' must be a string or a list of strings" % key)
            cleaned = [p.strip() for p in raw if p.strip()]
            try:
                return PatternSet(cleaned)
            except re.error as exc:
                raise DeclarationError("bad glob in '%s': %s" % (key, exc)) from exc

        fail_direction = data.get("fail_direction", "open")
        if fail_direction not in VALID_FAIL_DIRECTIONS:
            raise DeclarationError(
                "fail_direction must be one of %s, got %r"
                % (", ".join(VALID_FAIL_DIRECTIONS), fail_direction))

        raw_checks = data.get("checks") or []
        if not isinstance(raw_checks, list):
            raise DeclarationError("'checks' must be a list of objects")
        checks = []
        for item in raw_checks:
            if not isinstance(item, dict) or not item.get("command"):
                raise DeclarationError(
                    "each check needs at least a 'command': %r" % (item,))
            checks.append({"name": str(item.get("name") or item["command"])[:80],
                           "command": str(item["command"])})

        decl = cls(
            run_id=run_id.strip(),
            intent=str(data.get("intent") or ""),
            create=_patterns("create"),
            modify=_patterns("modify"),
            delete=_patterns("delete"),
            forbid=_patterns("forbid"),
            fail_direction=fail_direction,
            strict_unfulfilled=bool(data.get("strict_unfulfilled", False)),
            root=root or str(data.get("root") or "."),
            checks=tuple(checks),
        )
        return decl

    @classmethod
    def from_json(cls, text: str, root: str | None = None) -> "Declaration":
        try:
            data = json.loads(text)
        except ValueError as exc:
            raise DeclarationError("declaration is not valid JSON: %s" % exc) from exc
        return cls.from_dict(data, root=root)

    @classmethod
    def load(cls, path: str, root: str | None = None) -> "Declaration":
        with open(path, "r", encoding="utf-8") as fh:
            return cls.from_json(fh.read(), root=root)
