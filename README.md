# callusguard

<!-- portfolio-status -->
**Status:** Production-derived — merged from five repos I run against my own live agent workflows. · **Layer:** Execution controls · **[Portfolio map ›](https://github.com/kkrlstrm)**

**Guards written from evidence, not imagination — and pruned when the evidence stops.**

Most agent guardrails are written by imagining what might go wrong. callusguard starts
from what *has* gone wrong in your own runs: it records every tool call, mines the
recurring failures into candidate rules, enforces the ones you promote, and verifies
each run only touched what it said it would.

Claude Code and the OpenAI Codex CLI are both first-class throughout.

```
   record  ─────►  derive  ─────►  guard  ─────►  wroteonly
     │               │               │               │
  every tool     recurring       enforce at      verify the run
  call, with     failures →      the tool        stayed inside
  exit codes     candidate       boundary        its declared
                 rules                           write set
     ▲                                                │
     └────────────── what happened feeds the next rule ┘
```

> **The loop is the product.** Recording without enforcement is a dashboard.
> Enforcement without recording is a guess.

## Why "callus"

A callus is laid down by repeated friction, at exactly the site of the damage — and it
**resolves when the friction is engineered away.** That is the rule lifecycle here, and
it is the part most guardrail tooling gets backwards: a rule library that only grows is
a library nobody trusts. Rules start as `monitor`, a human promotes them, and a guard
that stops firing gets pruned.

Shrinking is the tool working, not rotting.

## Install

```bash
pip install callusguard                 # the enforcement side — zero dependencies
pip install 'callusguard[telemetry]'    # adds the recorder (FastAPI + Postgres)
python3 install.py                    # wire the hooks into Claude Code and/or Codex
```

The enforcement half has **no dependencies, no network, and no model calls** — it runs
inside every tool call, and that is the only reason it is safe to put there. A CI job
imports it under `python -S` with site-packages unreachable, so the claim cannot rot.

## The four stages

```bash
callusguard record serve                       # Claude Code hooks POST here
callusguard record ingest                      # Codex: read ~/.codex/sessions rollouts
callusguard derive --from-log tool_calls.jsonl --out candidates.rules.json
callusguard guard check                        # validate rulesets (runs each rule's examples)
callusguard guard doctor                       # is it actually wired?
callusguard guard audit                        # verify the hash chain
callusguard scope declare --create 'docs/**/*.md' --run-id job1
callusguard scope verify --run-id job1
```

### record

Two acquisition models, because the hosts afford different things. Claude Code can
POST from a hook, so the recorder is a small FastAPI service that queues and writes.
Codex writes append-only rollout files, so its recorder walks and parses them —
SQLite by default, zero config. **Both write the same schema, with a `source` column**,
so one warehouse answers questions across both hosts.

### derive

Reads recurring tool failures, normalises the error text, clusters by signature, and
emits candidate rules. Everything comes back as `action: "monitor"` — **never
auto-armed**. A human promotes `monitor → nudge → block`. Rules earn their way up.

> Log real behaviour → find repeated failures → derive candidates → review → promote.

### guard

A rule is JSON: a glob on the tool name, regexes on a `tool_input` field, an action,
and a message. Four graded outcomes, numerically ranked, most-restrictive-wins:

| Action | Mechanism | Tool runs? |
|---|---|---|
| `monitor` | audit only, never surfaced — the staging rung for a candidate | yes |
| `nudge` | `additionalContext` injected so the model self-corrects | yes |
| `deny` | `permissionDecision: "deny"` — refused, model is told why | no |
| `block` | **exit 2** — survives a parent's `bypassPermissions` | no |

> **Nudge when the model can recover. Block when it can't.**

Every verdict lands in a hash-chained, tamper-evident JSONL log. Commands are stored
as a SHA-256 plus a secret-redacted preview, never verbatim.

### wroteonly

The agent declares its intended write set before acting; callusguard fingerprints the
tree and afterwards diffs actual against declared, surfacing only errors *this run*
introduced. It watches the filesystem rather than the tool stream, because tool-level
blocks are routable-around — block `Write` and the model uses a Bash heredoc.

Full detail in [docs/wroteonly.md](docs/wroteonly.md).

## What this replaces

Five repos, built at different times off the back of each other:

| Was | Now | What the merge actually removed |
|---|---|---|
| `agent-guard` | `callusguard.guard` | — |
| `codex-guard` | `callusguard.guard` | **182 lines of byte-identical engine**, maintained twice |
| `cc-logger` | `callusguard.telemetry.cc` | — |
| `codex-logger` | `callusguard.telemetry.codex` | — |
| `wroteonly` | `callusguard.wroteonly` | a third copy of the hash-chained audit |

The two guards turned out to be **the same program wearing two names** — the complete
diff was a brand string, an env prefix, and an audit path. Not one line of behaviour.
Host differences now live in one 200-line file.

The two loggers are genuinely different and stay that way: push vs pull is forced by
what each host offers. They converge at the schema, which is where it matters.

Reasoning, including what was deliberately *not* merged:
[MERGE.md](MERGE.md).

## Migrating from the old repos

Nothing user-facing changed. `AGENT_GUARD_*` and `CODEX_GUARD_*` still work, the audit
logs stay at `~/.agent-guard/audit.jsonl` and `~/.codex-guard/audit.jsonl`, and the
entry-point names are preserved as aliases.

**The audit *logs* are deliberately not merged.** A hash chain is per-file;
concatenating two would invalidate both. Code merges, chains don't.

## Tests

```bash
python3 -m unittest discover -s tests/guard     -p 'test_*.py'   # 76
python3 -m unittest discover -s tests/wroteonly -p 'test_*.py'   # 64
python3 -m unittest tests.test_dependency_wall                    #  3
python3 -m pytest tests/telemetry -q                              # 62
```

**All 202 tests from the five original repos were ported unchanged and still pass.**
Only import paths and entry-script locations were rewritten — every assertion is the
one the original repo shipped. That is the evidence the merge preserved behaviour;
rewriting the tests would only have proved the new code passes new tests.

The merge itself surfaced three real defects, which is the argument for having done it:

- **Host mis-detection.** `CLAUDECODE=1` is exported into every shell Claude Code
  spawns, so a Codex hook run from a terminal inside a Claude session was identified
  as Claude Code — wrong brand, wrong ruleset variable, wrong audit log, silently.
  The payload now outranks the environment.
- **A lost rule.** codex-guard's starter set had `apply-patch-writes-secret-file`;
  agent-guard's did not. A careless copy dropped it. The shipped set is now the union.
- **Ruleset resolution ordering.** The entry script resolved `<PREFIX>_RULES` before
  the host was known, so the override silently missed on one host.

## Scope: controls, not a sandbox

- **Not a sandbox.** A determined agent can route around any of this. The durable
  boundaries are OS-level isolation and a `SELECT`-only DB role — the read-only guard
  is a backstop, not a boundary.
- **A guard bug must never wedge a session.** Every enforcement path falls open on an
  internal error. The only thing allowed to stop a run is a decision, not a crash.
- **The audit is tamper-*evident*, not tamper-proof.** It proves a chain was edited;
  it does not prevent editing.
- **`derive` proposes; it never arms.** Candidates land as `monitor`. Promotion is a
  human act, on purpose.

## Docs

- [MERGE.md](MERGE.md) — how the five repos combine, and what was deliberately left apart
- [docs/WHEN_TO_USE.md](docs/WHEN_TO_USE.md) — the nudge-vs-block decision
- [docs/THREAT_MODEL.md](docs/THREAT_MODEL.md) — honest limits
- [docs/TELEMETRY.md](docs/TELEMETRY.md) — what gets recorded, and what is redacted

## License

GNU AGPL-3.0 — see [LICENSE](LICENSE). Copyright (C) 2026 Kai Karlstrom.

---

<!-- portfolio-footer -->
## Where this fits

Part of a portfolio of **governed, AI-native GTM systems** — reference implementations and reusable patterns extracted from a private production stack. In that system this is the control surface that turns observed agent failures into runtime guarantees.

**Full portfolio map → [github.com/kkrlstrm](https://github.com/kkrlstrm)**

Works with:
- [model-eval-gate](https://github.com/kkrlstrm/model-eval-gate) — the policy gate for delegating work to a cheaper model
- [agent-tenancy](https://github.com/kkrlstrm/agent-tenancy) — resolves the tenant before the agent runs, so routing never depends on the model
