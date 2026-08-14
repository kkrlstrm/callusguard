# callusguard

<!-- portfolio-status -->
**Status:** Production-derived — merged from five repos I run against my own live agent workflows. · **Layer:** Execution controls · **[Portfolio map ›](https://github.com/kkrlstrm)**

**Guardrails that earn their place.**

callusguard turns repeated Claude Code and Codex failures into reviewed controls,
enforces them where the agent acts, and verifies what the run actually changed.

A static policy encodes what you *feared*. callusguard encodes what your agents
actually taught you — and proves whether a given run stayed inside the deal.

---

## The thing it does that nothing else does

Every part of this exists somewhere. **The lifecycle does not.**

```
  a failure happens          →  recorded, with its exit code and error
  it happens 3 more times    →  proposed as a rule, monitor-only, NOT armed
  you review the evidence    →  promoted to nudge, or to a hard block
  the workflow gets fixed    →  the rule stops firing
  30 days quiet              →  flagged for pruning. deleting it is the tool working
```

That is `record → derive → guard → verify → prune`, and the last step is the one
that makes the rest trustworthy. **A rule library that only grows is one nobody
trusts** — every stale rule is latency on every tool call and noise in every review.

### One incident, end to end

```console
$ callus derive --from-log tool_calls.jsonl --out candidates.rules.json
wrote 1 candidate rule(s) -> candidates.rules.json
# derived-bash-psql-9c1c3e  action: monitor
# "DRAFT — recurring Bash failure (3x in 7d): psql: error: connection to server..."
```

It lands as `monitor`. It does not block anything. You read it, decide it is real,
promote it to `block` — and now:

```console
$ # the agent tries it again
BLOCKED by agent-guard: Bare psql keeps failing here — pass a DSN.
$ echo $?
2
```

Six weeks later, after someone fixes the connection string for good:

```console
$ callus guard prune
  ✂ PRUNE (1)
      derived-bash-psql-9c1c3e   never fired in the last 30 days — either the failure
                                 was engineered away, or the pattern never matched
  1 rule(s) have stopped earning their place.
  Deleting them is the tool working, not rotting.
```

### And the scope half

The agent declares what it intends to touch. Afterwards, the actual write set is
diffed against that declaration, and the project's own checks run with
**pre-existing failures subtracted** — so a lint error that was already there can
never mask the one this run introduced.

```console
$ callus scope verify --run-id archref-2026-08-14
✗ deny — Wrote 1 path(s) outside the declaration: scripts/build.py

  Outside the declaration:
    modified scripts/build.py
  Declared: context/knowledge-hub/architecture-reference/*.md
```

That baseline is captured **per invocation and never committed**, which is what lets
it catch a break in a file the agent never opened.

## Install

```bash
pip install callusguard                 # enforcement — zero dependencies
pip install 'callusguard[telemetry]'    # adds the recorder
python3 install.py                      # wire the hooks into Claude Code and/or Codex
```

The enforcement half has **no dependencies, no network, and no model calls**. It runs
inside every tool call, and that is the only reason it is safe to put there. CI imports
it under `python -S` with site-packages unreachable, so the claim cannot rot.

## Not a sandbox — say it plainly

callusguard is an **evidence and control loop**, not an isolation boundary.

- A determined agent can route around any hook. Block `Write` and it uses a Bash
  heredoc; block `rm` and it reaches for `perl -e "unlink(...)"`.
- The read-only DB guard is a backstop. **The durable guarantee is a `SELECT`-only
  role**, not a regex.
- The `Stop` gate refuses to let a run finish on a violation. It does not roll the
  writes back. There is no undo.
- Nothing here is tamper-*proof*. The audit chain proves a log was edited; it does
  not prevent editing.

If you need real isolation, use an OS sandbox or an isolated CI runner — and run
callusguard inside it. They compose; they don't compete.

## What runs when

| Stage | Command | Needs deps? |
|---|---|---|
| record | `callus record serve` (Claude Code) · `callus record ingest` (Codex) | yes |
| derive | `callus derive --from-log …` | no |
| guard | `callus guard check` · `doctor` · `audit` | no |
| prune | `callus guard prune --days 30` | no |
| verify | `callus scope declare …` / `callus scope verify …` | no |

**On capture scope, precisely:** the Claude Code recorder captures an **allowlist** of
tools — `Agent`, `Bash`, `Edit`, `Write`, `Read`, `Skill`, `WebFetch`, `WebSearch`, and
anything matching `mcp__*` — not literally every call. The Codex recorder parses
rollout files. Both write one schema with a `source` column. Evidence you act on
should be described accurately; see [docs/TELEMETRY.md](docs/TELEMETRY.md).

## Four graded outcomes

| Action | Mechanism | Tool runs? |
|---|---|---|
| `monitor` | audit only, never surfaced — where every derived rule starts | yes |
| `nudge` | `additionalContext` injected so the model self-corrects | yes |
| `deny` | `permissionDecision: "deny"` — refused, model told why | no |
| `block` | **exit 2** — survives a parent's `bypassPermissions` | no |

Most-restrictive-wins. Every verdict lands in a hash-chained log; commands are stored
as a SHA-256 plus a secret-redacted preview, never verbatim.

> **Nudge when the model can recover. Block when it can't.**
> **A guard bug must never wedge a session** — every path falls open on an internal
> error. The only thing allowed to stop your agent is a decision, not a crash.

## Where this sits next to other tools

callusguard is deliberately narrow. It is **not** a general policy platform, a
sandbox, or a competing policy standard.

| If you want… | Use | callusguard's part |
|---|---|---|
| One policy across MCP, SDKs, many runtimes | a cross-framework policy platform | policies **earned from observed failures**, not authored up front |
| Isolation / sandboxing | OS sandbox, isolated CI runners | run callusguard inside it |
| Human intent → verifiable contract *before* work | spec/contract verification tools | what happens **during and after** execution |
| A portable policy decision contract | **Microsoft's Agent Control Specification** | callusguard **emits ACS-shaped verdicts** — it consumes the standard rather than rivalling it |
| A local telemetry dashboard | dedicated observability tools | telemetry here is **evidence for controls**, not the destination |

The gap this fills is **policy lifecycle**: *this rule exists because our agents
repeatedly did this; here is the evidence; here is when we promoted it; here is when
we retire it.*

## Tests

```bash
python3 -m unittest discover -s tests/guard     -p 'test_*.py'   # 89
python3 -m unittest discover -s tests/wroteonly -p 'test_*.py'   # 64
python3 -m unittest tests.test_dependency_wall                    #  3
python3 -m pytest tests/telemetry -q                              # 62
```

All 202 tests from the five predecessor repos were ported **unchanged** and still
pass — only import paths were rewritten. That is the evidence behaviour was preserved.

## Docs

- [docs/WHEN_TO_USE.md](docs/WHEN_TO_USE.md) — the nudge-vs-block decision
- [docs/THREAT_MODEL.md](docs/THREAT_MODEL.md) — what this does and does not defend against
- [docs/TELEMETRY.md](docs/TELEMETRY.md) — what is captured, and what is redacted
- [docs/wroteonly.md](docs/wroteonly.md) — declared-write-set verification in depth
- [MERGE.md](MERGE.md) — how five repos became one, and what was deliberately left apart

## Why "callus"

A callus is laid down by repeated friction, at exactly the site of the damage — and it
**resolves when the friction is engineered away.** That is the rule lifecycle, and it
is the part most guardrail tooling gets backwards.

Shrinking is the tool working, not rotting.

## License

GNU AGPL-3.0 — see [LICENSE](LICENSE). Copyright (C) 2026 Kai Karlstrom.

---

<!-- portfolio-footer -->
## Where this fits

Part of a portfolio of **governed, AI-native GTM systems** — reference implementations and reusable patterns extracted from a private production stack. In that system this is the operational memory that turns observed agent failures into runtime guarantees.

**Full portfolio map → [github.com/kkrlstrm](https://github.com/kkrlstrm)**

Works with:
- [model-eval-gate](https://github.com/kkrlstrm/model-eval-gate) — the policy gate for delegating work to a cheaper model
- [agent-tenancy](https://github.com/kkrlstrm/agent-tenancy) — resolves the tenant before the agent runs, so routing never depends on the model
