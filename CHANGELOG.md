# Changelog

Notable changes to callusguard. Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/);
versioning is [SemVer](https://semver.org/), pre-1.0 — the minor is where behavior changes
land while the loop settles.

Releases before 0.5.0 were never published to PyPI; they exist as git history and are
listed here so the relicense and the merge have a written record.

## [Unreleased]

### Fixed

- **`doctor --project` could not detect a correct installation.** `check_project` matched
  only `pretooluse-guard.py`, while `install.py` writes `guard-hook.py` — so a properly
  wired project was reported as "main guard NOT found", and the remedy it printed
  (`install.sh`) is not a file in this repo. THREAT_MODEL names this command as the
  mitigation for "disabled or unwired hooks", which made the false negative the worst
  available direction: the verification step telling a protected user they are
  unprotected. Both entry-script names are now recognised and the message names
  `python3 install.py`. Regression tests build the settings document from the installer's
  own `HOOKS` spec, so adding an entry script without teaching `doctor` about it fails.

### Added

- **This repository now guards itself** — `.claude/settings.json` wires the `PreToolUse`
  guard over this checkout with the shipped starter ruleset, no hand-written repo rules.
  The `doctor` defect above was found by doing it.
- `SECURITY.md` — private reporting via GitHub Security Advisories (now enabled on the
  repository), supported versions, and an explicit in-scope / not-in-scope split. The
  line that settles most questions: evading a regex rule is documented behavior; a rule
  that fails to fire on input it matches is a bug.
- `CHANGELOG.md` — this file.
- A test asserting `pyproject.toml` and `callusguard.__version__` agree. The audit log
  stamps the latter and PyPI serves the former, so drift makes an audit chain claim a
  version that was never released. It caught a real instance immediately: a stale
  `__pycache__` entry left `import callusguard` reporting a version the source did not
  contain.
- `Changelog` and `Security` links in the package metadata.

## [0.5.0] — 2026-08-20

First release on **PyPI**: `pip install callusguard`. Until now the README's primary
install path 404'd because the name had never been published.

### Added

- **Evidence tiers with an enforced action ceiling** (`callusguard/core/tiers.py`).
  Clusters are graded `deterministic` / `reproducible` / `probabilistic` / `anecdotal` /
  `unknown`, and each tier caps how far a rule may be armed — `block` requires ≥95%
  failure over ≥5 attempts. `verify_rules` refuses a ruleset whose action outruns its
  tier, so `callus guard check` fails review instead of shipping a block promoted from a
  coin flip. Hand-written untiered rules are left alone.
- Package metadata for the PyPI page: Python 3.9–3.13 classifiers,
  `Development Status :: 4 - Beta`, `Intended Audience :: Developers`,
  `Operating System :: OS Independent`, and Repository + Issues URLs.
- `SECURITY.md` with a private reporting channel, and this changelog.
- A test asserting `pyproject.toml` and `callusguard.__version__` agree — the audit log
  stamps the latter and PyPI serves the former, so drift would make an audit chain claim
  a version that was never released.

### Changed

- **Failures are graded by rate, not by count.** Every threshold in the loop ran on a bare
  numerator: `derive` proposed at 3 failures, `guard prune` promoted at 5 firings, and
  neither asked how many times the command was *tried*. Three failures out of three and
  three out of three hundred produced the same number and meant opposite things. The
  denominator was always in the telemetry — cc-logger records successes next to failures —
  and was simply never queried.
- `derive` counts attempts per command **shape**; an error signature cannot supply a
  denominator because a success has no error to key on.
- Promotion in `guard prune` gates on failure rate rather than firing count. A pattern
  matching fifty successful commands is busy, not broken.
- Private-stack texture removed from the published tree — no credentials or client data
  were involved, but internal repo names, job paths, helper names and a real budget cap
  were. `examples/archref_job/` → `examples/library_job/` with the shape unchanged;
  internal rule ids in the README generalized, with the table saying so directly; the
  measured failure modes, dates, rates and attempt counts untouched.
- `16_hook_targets.sql` match patterns are now labelled placeholders, with a header
  warning that leaving them unreplaced makes every count read zero — which looks like
  health and is not.

### Fixed

- `anecdotal` clusters are **withheld and reported** rather than proposed, so "nothing to
  propose" cannot hide "eight things too thin to grade."
- A row with no attempt count grades `unknown`, never `deterministic`. Absence of evidence
  is not evidence of a settled failure.
- A JSONL log filtered down to failures would hand every cluster a fabricated 100% rate.
  If a log contains no successful call at all, no denominator is supplied and everything
  grades `unknown`, with a note on stderr.
- The two `derive` input modes now agree. The SQL path thresholded non-Bash rows at
  `HAVING >= 3`, deleting sub-threshold signatures that `rules.py` aggregates into one
  tool-wide candidate, so the JSONL path had been finding tool-wide signals the cc-logger
  path could not.

### Note

Tiers are **observational** — a rate over the traffic that happened to run, not a
controlled re-run. Read one as a prior, not a proof. This is stated wherever a tier is
displayed.

## [0.4.0] — 2026-08-18

### Changed

- **Relicensed from AGPL-3.0 to Apache-2.0.** AGPL's network clause triggers on
  network-interactive use; the enforcement layer is a dependency-free library invoked
  inside a hook in someone else's process, so nobody can offer it as a service. The
  copyleft guarded a threat that did not apply while reliably failing corporate OSS review
  for a tool whose whole value is being installed in an agent loop. `NOTICE` records the
  reasoning and that all five predecessors share one copyright holder.
- The README publishes the derivation funnel from 97 days of production telemetry
  (134,068 tool calls, 6,012 failures): 136 candidates proposed, 12 promoted. A 9%
  promotion rate is the monitor rung working.
- Says plainly what derivation is **not** — a frequency counter with a template that does
  not generalize, with rule shape remaining a human step. The narrower supported claim is
  that derivation allocates reviewer attention.
- The demo is relabelled a pipeline smoke test; a synthetic 7-event trace proves the
  stages connect and nothing more. `wroteonly` states plainly that it has no production
  track record, so the evidence section cannot be read as covering it.

### Added

- `scripts/evidence-report.py` regenerates both README tables from any cc-logger database,
  so the numbers are checkable rather than asserted.
- Audit events carry `tool_use_id`, the join key to the recorder's `tool_call_id` — what
  will let "did the nudge work?" be measured on a single call rather than inferred from
  rates over time.
- `doctor` reads the installed host version against `VERIFIED_HOSTS` and warns on drift.
  "Exit 2 survives a parent's `bypassPermissions`" is a property of the host, not of this
  code, and can change in any release with no error to catch it. It warns, never fails.

### Fixed

- **The prune window was lying.** An event with no `ts` cannot be windowed. Against a
  97-day log of undated verdicts the report printed "1916 verdict(s) in the last 30 days"
  — an all-time number wearing a windowed label, exactly the silent degradation this tool
  exists to catch. Undated events are still counted, `undated_events` reports how many, and
  the renderer labels the report ALL-TIME when nothing in it is windowed.
- Two rules that went the wrong way are published rather than buried:
  `macos-timeout-not-installed` moved 26.2% → 26.8% and never demonstrably helped;
  `page-digest-dead-domain-retry` moved 7.0% → 17.3% because its own nudge named a
  `--waterfall` flag that did not exist, so agents following the advice exited 2. A guard
  that fires 1,057 times and hands out a wrong flag is worse than no guard. Both were
  recoverable nudges, and catching them is the case for the monitor rung existing.

## [0.3.0] — 2026-08-14

First release under the callusguard name. Supersedes and replaces **agent-guard**,
**codex-guard**, **cc-logger**, **codex-logger** and **wroteonly** — five repos merged into
one `record → derive → guard → verify` loop for Claude Code and the OpenAI Codex CLI.

### Added

- `callus guard prune` (`callusguard/guard/lifecycle.py`) — the name's central claim,
  "pruned when the evidence stops," previously had no implementation. Reads the
  hash-chained audit log and classifies each declared rule `prune` / `promote` / `review` /
  `keep`. It never edits a ruleset: promotion and pruning stay human acts, the same
  discipline that makes derived rules land as `monitor` and never auto-arm.
- The three docs the README linked to but did not ship: `WHEN_TO_USE`, `THREAT_MODEL`,
  `TELEMETRY`.
- `tests/test_cli_coverage.py`, which reads the verbs out of each sub-CLI's own source and
  asserts the wrapper routes every one.

### Changed

- **The dependency wall.** The real boundary is dependencies, not hosts: `core`, `guard`
  and `wroteonly` are stdlib and run inside every tool call; telemetry needs FastAPI and
  psycopg and runs out of band. `dependencies = []` plus a `telemetry` extra, enforced by
  `tests/test_dependency_wall.py` importing under `python -S`.
- One guard engine. agent-guard and codex-guard were the same program — `guard/engine.py`
  was 182 lines with zero differing; the entire diff was a brand string, an env prefix and
  an audit path. Now one engine with a host-parameterised runner.
- The two recorders stay genuinely different, as the hosts force: cc-logger is push
  (FastAPI, hooks POST in), codex-logger is pull (walks `~/.codex/sessions` rollouts).
  They already shared a schema with a `source` column, which is the merge point.
- "Records every tool call" overstated the Claude capture model — it is an allowlist
  (`Agent`, `Bash`, `Edit`, `Write`, `Read`, `Skill`, `WebFetch`, `WebSearch`, `mcp__*`).
  A product about trustworthy evidence cannot be loose about what it captures.

### Fixed

- **Host mis-detection.** `CLAUDECODE=1` is exported into every shell Claude Code spawns,
  so a Codex hook run from a terminal inside a Claude session was identified as Claude Code
  — wrong brand, wrong rules variable, wrong audit log, silently. The payload now outranks
  the environment.
- Ruleset resolution ran *before* host detection, so `<PREFIX>_RULES` missed on one host.
- A lost rule: codex-guard's `apply-patch-writes-secret-file` had been dropped by a careless
  copy. The shipped set is now the union.
- `callus record` hard-coded `choices=("serve","ingest")`, silently dropping cc-logger's
  `migrate` / `sessions` / `inspect` / `insights` / `rate`. They worked before the merge and
  did not after.
- "No audit log" and "log present but aged out" were conflated — both yield zero events in
  the window and prune warned on both. They mean opposite things: an unreadable log means
  we know nothing and must not delete on it; an intact log whose verdicts all predate the
  window is exactly the prune signal. Split by `audit_readable`.
- 16 broken relative documentation links.

### Compatibility

`AGENT_GUARD_*`, `CODEX_GUARD_*` and `WROTEONLY_*` keep working; per-host brand strings and
audit paths are unchanged. The audit **logs** are deliberately not merged — a hash chain is
per-file, so concatenating them would invalidate both.

[0.5.0]: https://github.com/kkrlstrm/callusguard/releases/tag/v0.5.0
[0.4.0]: https://github.com/kkrlstrm/callusguard/commit/2281b86
[0.3.0]: https://github.com/kkrlstrm/callusguard/commit/15cebd1
