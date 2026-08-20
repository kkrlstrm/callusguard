# Security policy

## Reporting

Report privately through
[**GitHub Security Advisories**](https://github.com/kkrlstrm/callusguard/security/advisories/new)
— that channel is enabled on this repository. Please do not open a public issue for a
suspected vulnerability.

Useful in a report: the version (`callus --version`), the host (Claude Code or Codex),
the ruleset in play, and the smallest tool-call payload that reproduces it. A rule id
and the audit line it did or did not produce is usually enough.

This project is maintained by one person. Expect an acknowledgement within about a week,
and an assessment of whether it is in scope within two. If a report is in scope and I
cannot fix it quickly, I would rather document it in
[docs/THREAT_MODEL.md](docs/THREAT_MODEL.md) than leave it silently open.

## Supported versions

| version | supported |
|---|---|
| 0.5.x | yes |
| < 0.5 | no — upgrade; the pre-0.5 releases predate PyPI publication |

Fixes land on `main` and go out in the next release. There are no backport branches.

## Scope: read the threat model first

[docs/THREAT_MODEL.md](docs/THREAT_MODEL.md) is the authoritative statement of what this
tool does and does not defend, and this policy does not restate it. The short version:
**callusguard is a control and evidence loop, not an isolation boundary.** If you need
isolation, run it inside an OS sandbox or an isolated CI runner.

That distinction decides most scope questions, so it is worth stating in the form that
actually comes up:

**Evading a rule is documented behavior. A rule that fails on input it matches is a bug.**

Rules are regex over a tool-call payload. That a sufficiently creative command slips past
one is written down as a limitation, not a defect — blocking `Write` and having the agent
reach for a Bash heredoc is the documented shape of the tool, not a bypass to report.
What *is* worth reporting is the guard not doing the thing it says it does.

### In scope

- A rule that **does not fire on a payload it matches** — a `block` that lets the call
  through, an action silently downgraded, precedence resolving to the wrong verdict.
- **Audit-chain forgery that `callus guard verify` reports as intact.** The chain is
  tamper-*evident*, and a tampering that verification misses defeats the one guarantee it
  makes. (Deleting the log outright is not this — see the threat model.)
- **A secret reaching disk unredacted** in the audit log, a spill file, or telemetry, by
  a pattern class not already listed in `tests/telemetry/test_redaction_known_gaps.py`.
  The gaps recorded there are known and tracked; a new class is a finding.
- **Evidence-tier enforcement failing** — a ruleset that arms an action beyond what its
  tier permits and still passes `callus guard check`. The ceiling is meant to be
  enforced, not advisory.
- **Path traversal or an unintended write** from a `wroteonly` declaration, or a scope
  verdict that reports a run as clean when it wrote outside its declaration.
- Anything in `callusguard.core`, `callusguard.guard` or `callusguard.wroteonly` that
  **reaches the network, imports a third-party package, or executes payload content.**
  That layer runs inside every tool call; the dependency wall is a security property, and
  `tests/test_dependency_wall.py` exists to keep it one.
- Credential or token exposure in the telemetry server's logs or responses.

### Not in scope

These are documented properties. Reporting them is welcome as an issue or a docs
improvement, but they are not vulnerabilities:

- Regex evasion, obfuscated SQL, dynamically generated commands, indirection through a
  script — the read-only guard is a backstop, and the durable control is a `SELECT`-only
  database role.
- Prompt injection that abuses an **allowed** path. A content-blind rule cannot see it.
- A compromised local machine. Rules, hook and audit log all sit on the same box; local
  write access defeats all three.
- Hooks not installed, unwired, or falling open on a broken ruleset. Fail-open is
  deliberate — a guard bug must never wedge a session. Confirm wiring with
  `python3 bin/doctor.py --project <dir>`.
- Sub-agents packaged as a **plugin**: the host ignores `hooks:` there, so the read-only
  backstop does not fire. Install under `.claude/agents/`, never as a plugin.
- Deleting or truncating the audit log. It is not a compliance vault; the head hash is not
  anchored externally.
- The absence of rollback. The `Stop` gate refuses to finish a run on a violation; it does
  not undo writes.

## Disclosure

Coordinated. Tell me first, give me a reasonable window, and I will credit you in the
advisory and the release notes unless you would rather stay anonymous. If a report is out
of scope I will say so plainly and explain why, rather than leaving it unanswered.
