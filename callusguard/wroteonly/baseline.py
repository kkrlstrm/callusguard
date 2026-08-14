"""New errors only — the pre-existing ones are not yours.

The second check. A declaration diff catches *where* the agent wrote; this catches
*what it broke*, without drowning the answer in failures that were already there.

    "A whole-workbook error dump is noise to an agent; the signal is
     'did *my* change break something'."   — Witan, via docs/evidence-corpus.md §1.7

PRIOR ART — THIS PART IS NOT NOVEL, AND THE README SAYS SO
    `betterer` (JS/TS) and `linthell` (Python) both do baseline-filtered linting, and
    `reviewdog` filters findings to a PR diff. We do not reimplement them. What
    differs is only *what the baseline is keyed to*:

        reviewdog          the PR diff          → changed LINES
        betterer/linthell  a committed file     → the codebase over time
        wroteonly          captured per run,    → ONE agent invocation
                           immediately before
                           the agent acts, and
                           never committed

    The consequence that matters: a change in file A that breaks file B is invisible
    to a line-keyed filter and visible here.

ERROR IDENTITY — THE DESIGN DECISION WITAN LEAVES UNSPECIFIED
    Witan's source does not say whether an error that *moves* (same error, different
    line) counts as new. We decide: it does not.

    Identity is (path, code, normalised message) — line and column are deliberately
    excluded. Inserting ten lines at the top of a file shifts every finding below it;
    keying on line number would report the entire file as newly broken.

    Normalisation collapses bare numbers only. Quoted literals are deliberately NOT
    collapsed: the symbol in `unused import 'os'` IS the identity of that finding,
    and folding it to a placeholder would make every unused import in a file look
    like the same error — so introducing a new one would be invisible. (An earlier
    draft did collapse them, copying agent-guard's telemetry clustering. That is the
    right normalisation for grouping failures into candidate rules and the wrong one
    for deciding whether two findings are the same finding. The unit test
    `test_different_symbol_is_a_different_finding` pins this.)

    The cost, stated honestly: two identical errors in the same file at different
    lines share one identity, so introducing a second occurrence of an error that
    already exists is not reported as new. That is the deliberate trade — it buys
    immunity to line drift, which is the far more common case.

INVARIANT
    The subtraction happens here, in code, never in the model's head. A checker that
    fails to run produces an explicit degraded result, never an empty finding set —
    an empty set is indistinguishable from "clean", and that is the failure mode this
    module exists to prevent.
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess

DEFAULT_TIMEOUT = 120

#: `path:line:col: message` and `path:line: message` — ruff, flake8, mypy, tsc,
#: gcc, eslint's compact formatter, shellcheck's gcc format. One pattern covers
#: the overwhelming majority of checkers a project actually runs.
DEFAULT_LINE_RE = re.compile(
    r"^(?P<path>[^\s:][^:]*?):(?P<line>\d+)(?::(?P<col>\d+))?:\s*(?P<message>.+)$"
)

#: A leading error code inside the message, e.g. "E501 line too long",
#: "error TS2345: ...", "error[E0308]: ...".
_CODE_RE = re.compile(
    r"^(?:(?P<kind>error|warning|note)\s*)?"
    r"(?P<code>[A-Z]{1,6}\d{2,5}|[A-Za-z]+\[[A-Za-z0-9_]+\])\b[:\s]*"
)

_NUMBER = re.compile(r"\b\d+\b")
_WS = re.compile(r"\s+")


def normalize_message(message: str) -> str:
    """Collapse the parts of a message that vary without the error changing.

    Bare numbers become '#', so "expected 4 spaces, found 6" and
    "expected 2 spaces, found 3" share an identity — the same complaint about the
    same construct. Quoted symbols are preserved; see the module docstring for why
    collapsing them was a bug.
    """
    text = _NUMBER.sub("#", message or "")
    return _WS.sub(" ", text).strip().lower()[:400]


class Finding:
    """One checker finding, reduced to a drift-resistant identity."""

    __slots__ = ("path", "code", "message", "raw", "line")

    def __init__(self, path: str, message: str, raw: str,
                 code: str = "", line: str = ""):
        self.path = (path or "").replace(os.sep, "/").lstrip("./")
        self.code = code or ""
        self.message = message or ""
        self.raw = raw or ""
        self.line = line or ""

    @property
    def identity(self) -> str:
        payload = "\x00".join(
            (self.path, self.code, normalize_message(self.message)))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    def to_dict(self) -> dict:
        return {
            "identity": self.identity,
            "path": self.path,
            "code": self.code,
            "line": self.line,
            "message": self.message.strip()[:400],
            "raw": self.raw.strip()[:400],
        }


def parse_output(text: str, pattern: re.Pattern | None = None) -> list:
    """Turn raw checker output into Findings.

    Lines that do not match the location pattern are still kept, keyed on their
    normalised text with an empty path. Losing them would let an unparsed checker
    look clean, which is the one outcome that must never happen silently.
    """
    rx = pattern or DEFAULT_LINE_RE
    findings = []
    for raw in (text or "").splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue
        match = rx.match(line.strip())
        if match:
            groups = match.groupdict()
            message = groups.get("message") or ""
            code = ""
            code_match = _CODE_RE.match(message.strip())
            if code_match:
                code = code_match.group("code") or ""
            findings.append(Finding(
                path=groups.get("path") or "",
                message=message,
                raw=line,
                code=code,
                line=groups.get("line") or "",
            ))
        else:
            findings.append(Finding(path="", message=line, raw=line))
    return findings


class CheckResult:
    """The outcome of running one check command."""

    __slots__ = ("name", "command", "ok", "exit_code", "findings", "error")

    def __init__(self, name, command, ok, exit_code, findings, error=""):
        self.name = name
        self.command = command
        self.ok = ok
        self.exit_code = exit_code
        self.findings = findings
        self.error = error

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "command": self.command,
            "ok": self.ok,
            "exit_code": self.exit_code,
            "error": self.error,
            "findings": [f.to_dict() for f in self.findings],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "CheckResult":
        findings = []
        for item in data.get("findings") or []:
            findings.append(Finding(
                path=item.get("path", ""),
                message=item.get("message", ""),
                raw=item.get("raw", ""),
                code=item.get("code", ""),
                line=item.get("line", ""),
            ))
        return cls(
            name=data.get("name", ""),
            command=data.get("command", ""),
            ok=bool(data.get("ok")),
            exit_code=data.get("exit_code"),
            findings=findings,
            error=data.get("error", ""),
        )


def run_check(name: str, command: str, cwd: str,
              timeout: int = DEFAULT_TIMEOUT,
              pattern: re.Pattern | None = None) -> CheckResult:
    """Run one check command and parse its output.

    A non-zero exit is NOT a failure — that is a linter's normal way of saying it
    found something. `ok` is False only when the command could not be run to
    completion at all (missing binary, timeout), because that is the case where the
    finding set is untrustworthy rather than merely non-empty.
    """
    try:
        proc = subprocess.run(
            command, shell=True, cwd=cwd, timeout=timeout,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, errors="replace",
        )
    except subprocess.TimeoutExpired:
        return CheckResult(name, command, False, None, [],
                           "timed out after %ss" % timeout)
    except (OSError, ValueError) as exc:
        return CheckResult(name, command, False, None, [], str(exc))

    output = proc.stdout or ""
    # 127 is the shell's "command not found" — an empty finding set here means the
    # checker never ran, not that the tree is clean.
    if proc.returncode == 127:
        return CheckResult(name, command, False, 127, [],
                           "command not found: %s" % command)
    return CheckResult(name, command, True, proc.returncode,
                       parse_output(output, pattern))


def capture(checks, cwd: str, timeout: int = DEFAULT_TIMEOUT) -> dict:
    """Run every check and return a serialisable baseline.

    `checks` is a list of {"name", "command"} dicts.
    """
    results = [run_check(c.get("name") or c.get("command", "check"),
                         c.get("command", ""), cwd, timeout)
               for c in checks]
    return {"checks": [r.to_dict() for r in results]}


def new_findings(before: dict, after: dict) -> tuple:
    """Set-difference the two capture()s.

    Returns (new_by_check, degraded) where `new_by_check` maps a check name to the
    findings present after but not before, and `degraded` lists the checks whose
    result is untrustworthy on either side.

    A check that failed to run in the BEFORE pass is degraded even if it ran fine
    after: with no baseline, every finding would look new, and reporting a hundred
    pre-existing errors as the agent's fault is worse than reporting nothing.
    """
    before_checks = {c["name"]: CheckResult.from_dict(c)
                     for c in before.get("checks", [])}
    after_checks = {c["name"]: CheckResult.from_dict(c)
                    for c in after.get("checks", [])}

    new_by_check: dict = {}
    degraded: list = []

    for name, after_result in after_checks.items():
        before_result = before_checks.get(name)
        if before_result is None:
            degraded.append({"check": name, "why": "no baseline for this check"})
            continue
        if not before_result.ok:
            degraded.append({"check": name,
                             "why": "baseline check failed: %s" % before_result.error})
            continue
        if not after_result.ok:
            degraded.append({"check": name,
                             "why": "post-run check failed: %s" % after_result.error})
            continue

        seen = {f.identity for f in before_result.findings}
        fresh = [f for f in after_result.findings if f.identity not in seen]
        if fresh:
            new_by_check[name] = fresh

    return new_by_check, degraded
