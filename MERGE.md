# How the five repos merge

**Written before the code, from reading the five codebases — not from the portfolio notes.**
Baseline to preserve: **202 passing tests** (agent-guard 64, codex-guard 12, wroteonly 64,
codex-logger 8, cc-logger 54 + 3 skipped + 11 xfailed).

---

## What the survey actually found

### 1. agent-guard and codex-guard are the same program

`guard/engine.py` is **182 lines, zero differing**. The complete diff across the whole guard
is:

| Differs | agent-guard | codex-guard |
|---|---|---|
| brand string in messages | `BLOCKED by agent-guard:` | `BLOCKED by codex-guard:` |
| nudge prefix | `agent-guard (telemetry-driven):` | `codex-guard:` |
| env prefix | `AGENT_GUARD_*` | `CODEX_GUARD_*` |
| audit path | `~/.agent-guard/audit.jsonl` | `~/.codex-guard/audit.jsonl` |
| docstrings | Claude Code wording | Codex wording |

**There is no behavioural difference at all.** Both hosts accept the identical enforcement
contract — exit 2 hard-blocks, `permissionDecision:"deny"` soft-denies, `additionalContext`
nudges. That was verified independently against Codex 0.140.0-alpha.2 and against the current
Claude Code hook docs.

So the guard does not need a host *adapter*. It needs a host **identity**: a name, an env
prefix, and a state directory. One file, five fields.

### 2. The two loggers are NOT the same program

| | cc-logger | codex-logger |
|---|---|---|
| acquisition | **push** — Claude Code `type:"http"` hooks POST to a FastAPI server | **pull** — walks `~/.codex/sessions/rollout-*.jsonl` |
| runtime | long-running server + asyncio queue + Postgres | batch CLI, no server |
| deps | fastapi, uvicorn, psycopg | stdlib + optional psycopg |
| default store | Postgres | **SQLite**, zero-config |

This difference is *correct* and must be preserved — it follows from what each host affords.
Claude Code can POST from a hook; Codex writes rollout files that can be tailed.

But they already converge where it counts. From `codex_logger/store.py`:

> "Optional: Postgres (mirrors cc-logger's Neon layout) … Every row carries `source='codex'`
> so a future UNION view against cc-logger is trivial."

**The merge point for telemetry is the store, and it was already designed for.** Two
acquisition front-ends, one schema.

### 3. The real boundary is dependencies, not hosts

| Layer | Deps | Runtime characteristic |
|---|---|---|
| **Enforcement** — guard, wroteonly | **none, stdlib** | synchronous, inside every tool call |
| **Telemetry** — server, ingest | fastapi, psycopg | out-of-band, long-running or batch |
| **Derivation** — derive_rules | psql shell-out | weekly, offline |

"No dependencies. No network. No model calls." is load-bearing for the enforcement layer. It
is what makes it defensible to put on the hot path of every tool call, and what lets someone
audit the whole thing in an afternoon. **A merge that puts FastAPI in the same install as the
guard destroys the only claim that makes the guard safe.**

That is the constraint the package layout has to satisfy.

---

## The layout

```
callusguard/
  core/                 zero-dep, shared by everything
    hosts.py              Host identity + wire format — ONE definition
    audit.py              hash-chained JSONL — ONE copy (was 3)
    verdict.py            ACS decisions — ONE copy
  guard/                zero-dep enforcement
    engine.py             rules/matching/precedence — ONE copy (was 2 identical)
    runner.py             stdin -> verdict -> exit, host-parameterised
    rules/*.json
  wroteonly/            zero-dep enforcement
    declare, observe, baseline, report, state, runner
  telemetry/            OPTIONAL EXTRA — every dependency lives behind this wall
    server.py             cc-logger (push, FastAPI)
    ingest.py             codex-logger (pull, rollout files)
    store.py, models.py, redaction.py, …
  derive/               telemetry -> candidate rules (the seam)
```

```toml
[project]
dependencies = []                     # enforcement installs with nothing

[project.optional-dependencies]
telemetry = ["fastapi>=0.115", "uvicorn[standard]>=0.32", "psycopg[binary]>=3.2"]
```

`pip install callusguard` gives the hot path and cannot pull a transitive dependency into it.
`pip install callusguard[telemetry]` adds the warehouse. **A CI job asserts that importing
anything under `guard/`, `wroteonly/` or `core/` works with zero third-party packages
installed** — otherwise the guarantee rots the first time someone adds a convenient import.

---

## Decisions and their reasons

**One `Host` class, two consumers.** wroteonly already needed host wire-format differences
(Stop: exit 2 on Claude vs `{"decision":"block"}` on Codex; PostToolUse can block on Codex
only). The guard needs only identity. One class carries both — identity fields plus emitters
— and the guard simply ignores the emitters it does not use.

**Audit collapses 3 → 1.** agent-guard's and codex-guard's copies are 110 lines each differing
by 12; kgg has a third at 108. The merged one is parameterised by host state dir. kgg is out
of scope here (different repo, different install) but can import it later.

**The audit log stays per-host by default.** `~/.agent-guard/audit.jsonl` and
`~/.codex-guard/audit.jsonl` keep working. Merging the *log files* would break every existing
`audit verify` chain — the hash chain is per-file, and concatenating two chains invalidates
both. Sharing code is the win; sharing the file is a regression.

**Back-compat is not optional.** `AGENT_GUARD_*` and `CODEX_GUARD_*` env vars, the existing
audit paths, and the `pretooluse-guard.py` / `validate-readonly.py` entry-point names all
keep working. Kai has live hooks pointing at those paths right now; a merge that silently
stops enforcing is strictly worse than no merge.

**Test suites are ported wholesale, not rewritten.** All 202 tests move across and must pass
against the merged code. Rewriting them would prove the new code passes new tests, which is
not the claim that needs proving. Mixed runners are kept: `unittest` for the guards and
wroteonly (deliberate, no pytest), `pytest` for the loggers.

---

## What this does NOT do

- **Does not merge the two audit *logs*.** Code merges; chains stay separate. See above.
- **Does not unify push and pull telemetry.** They stay two front-ends over one schema,
  because the difference is forced by the hosts.
- **Does not touch kgg.** It has the third audit copy and its own verdict enum, but it is a
  knowledge-graph write gate, not part of the agent loop.
- **Does not rename anything user-facing yet.** The package name here is a placeholder that
  appears in one constant and the directory name; renaming is a sed.
- **Does not resolve the published `wroteonly` repo.** It is already public at
  `kkrlstrm/wroteonly`. Whether that becomes an archived pointer to the monorepo, or stays a
  split-out mirror, is a positioning decision, not an engineering one.
