# callusguard

**Guardrails that earn their place.**

callusguard turns repeated Claude Code and Codex failures into reviewed controls,
enforces them where the agent acts, and verifies what the run actually changed.

A static policy encodes what you *feared*. callusguard encodes what your agents
actually taught you — and proves whether a given run stayed inside the deal.

---

## What static guardrails leave unfinished: the lifecycle

Most guardrails stop at policy. Every individual piece below exists somewhere —
recording, blocking, scope checks. What is rarely joined up is what happens to a
rule **over its life**.

```
  a failure happens          →  recorded, with its exit code and error
  it keeps happening         →  graded against how often it was TRIED, not just counted
  the rate earns a tier      →  proposed as a rule, monitor-only, NOT armed
  you review the evidence    →  promoted — no further than that tier allows
  the workflow gets fixed    →  the rule stops firing
  30 days quiet              →  flagged for pruning. deleting it is the tool working
```

That is `record → derive → guard → verify → prune`, and the last step is the one
that makes the rest trustworthy. **A rule library that only grows is one nobody
trusts** — every stale rule is latency on every tool call and noise in every review.

### The denominator

A failure count on its own cannot tell a broken command from a busy one. Three
failures out of three attempts and three out of three hundred are the same number
and opposite facts, and every threshold in this loop used to run on the first
number alone.

So each cluster is now graded against how many times it was actually tried — the
successes were always in the telemetry, they were simply never queried:

| tier | rate, over ≥5 attempts | may be armed as far as |
|---|---|---|
| `deterministic` | ≥ 95% — never really worked | `block` |
| `reproducible` | ≥ 50% — fails most times | `deny` |
| `probabilistic` | < 50% — usually works | `nudge` |
| `anecdotal` | too few attempts, at any rate | **not proposed at all** |
| `unknown` | no denominator available | `nudge` |

The ceiling is enforced, not advisory: `callus guard check` refuses a ruleset whose
action outran its evidence, so a `block` promoted from a coin-flip fails review
instead of shipping. `anecdotal` is what replaces the old rule of thumb that three
failures earn a guard — and withheld clusters are always reported, so "nothing to
propose" can never hide "eight things too thin to grade."

The same distinction governs promotion. A monitor rule used to graduate on how many
times it *fired*, but a pattern matching fifty commands that all succeeded is busy,
not broken — so `guard prune` now promotes on the failure rate, not the popularity.

One caveat, stated wherever a tier is displayed: this is an **observational** rate
over the traffic that happened to run, not an experimental one. Nothing here re-ran
anything under controlled conditions. Read a tier as a prior, not a proof.

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
$ callus scope verify --run-id library-2026-08-14
✗ deny — Wrote 1 path(s) outside the declaration: scripts/build.py

  Outside the declaration:
    modified scripts/build.py
  Declared: context/reference-library/*.md
```

That baseline is captured **per invocation and never committed**, which is what lets
it catch a break in a file the agent never opened.

**Status, plainly:** the guard half below has 97 days of production evidence. The
scope half has none — it is wired, tested, and demonstrated, but it has not yet run
unattended against a real job. Do not read the numbers in the next section as
covering it.

---

## What derivation actually does — and what it does not

`derive` is a **frequency counter with a template**. It is worth being exact about
this, because "guards derived from evidence" invites the reader to imagine something
smarter than what is here.

It groups failed tool calls by `(tool_name, normalized error signature)` — digits
collapsed to `#`, quoted strings to `'S'`, whitespace squeezed — counts each cluster,
and for Bash emits a candidate whose pattern is **the first token or two of the
sample command**. Non-Bash tools aggregate to one candidate per tool surface. That
is the whole algorithm; it is ~120 lines and you should read it.

**It does not generalize.** It cannot tell which part of a command caused the
failure, cannot widen a pattern to catch the next variant, and cannot narrow one that
would catch everything. Over 97 days of real telemetry it proposed candidates whose
patterns were `\bcd\b`, `\bset\b`, `\bfor\b`, and `\bpython3\b` — clusters that are
real and rules that are worthless.

The generalizing step is human, and the diff is the point:

| what derive proposed | what got promoted |
|---|---|
| `\bpython3\s+scripts/fetch\-tool\.py\b` | `fetch-tool\.py\b(?!.*--entity)` |
| `\bpsql\b` | `(?:^\|[;\|&(]\|\$\()\s*(?:[A-Za-z_]\w*=\S*\s+)*psql\b` |
| `\bsleep\b` | `\b(?:for\|while)\b[\s\S]{0,400}?\bsleep\s+\d` |

Each promoted regex encodes something the counter had no access to: that the failure
is the *absence* of a required flag, that a leading env assignment still counts as a
bare invocation, that the problem is a poll loop rather than a sleep.

**So the honest claim is narrower and, I think, more useful: derivation allocates
reviewer attention.** It finds the clusters worth 60 seconds of a human's time and
attaches the evidence — count, window, sample command, sample error — to each one. It
lands every candidate as `monitor`, armed at nothing. The rule shape is yours to
write. What the tool guarantees is that you are writing rules about things that
actually happened, at a rate you can sustain.

## The numbers

From a cc-logger database over **2026-05-13 → 2026-08-18 (97 days)**: 3,974 sessions,
134,068 tool calls, 6,012 failures (4.5%). Enforcement over the same period: **1,920
verdicts — 1,295 nudges, 624 monitor-only allows, 1 deny, 0 blocks.**

Regenerate all of it against your own database:

```bash
python3 scripts/evidence-report.py --db-url "$NEON_CC_LOGGER_URL" \
    --ruleset path/to/your/live.rules.json --since 2026-05-13 --until 2026-08-18
```

### The funnel

Derivation replayed one 7-day window at a time, exactly as a weekly review would run it:

| | |
|---|---|
| Candidate rules proposed | **136** (131 unique) across 14 windows |
| Promoted into the live ruleset | **12** |
| Promotion rate | **9%** |
| Promoted rules that have never fired | 1 of 13 |

Two of the fourteen windows proposed nothing at all. The 9% is not a defect — it is
the monitor rung doing its job, and it is the number I would want to see before
trusting anyone's "rules derived from evidence." A tool that promoted most of what it
proposed would be one that had stopped filtering.

(13 rules are live; 12 came from telemetry. The 13th was authored from a design
principle and is excluded from the numerator, which is why `evidence-report.py`
prints it separately.)

### Did promotion change anything

Failure rate among **matching** Bash attempts, split at each rule's promotion date:

| Rule | Before | After | Attempts |
|---|---|---|---|
| `cli-missing-required-flag` | 19.1% | **1.4%** | 131 → 142 |
| `bash-busywait-poll-loop` | 11.5% | **3.2%** | 139 → 94 |
| `shell-source-dotenv` | 16.1% | **8.6%** | 249 → 440 |
| `curl-page-scrape-spoofed-ua` | 2.0% | **0.0%** | 653 → 215 |
| `bash-sleep-chained-command` | 2.4% | 0.0% | 82 → 23 |
| `bare-psql-no-target` | 7.6% | 19.0% | **980 → 174** |
| `client-db-hand-rolled` | 11.4% | 20.0% | 220 → 20 |
| `macos-timeout-not-installed` | 26.2% | 26.8% | 42 → 82 |
| `fetch-dead-domain-retry` | 7.0% | **17.3%** | 1553 → 1746 |

Four of those IDs are generalized from their originals, which named internal tools in
the private repo they came from. Nothing else was altered: the failure modes, the
dates, the rates and the attempt counts are exactly what `evidence-report.py` printed.
Run it against your own telemetry and you will get your own names.

**Read the attempt counts, not just the rates.** `bare-psql-no-target` looks like a
regression until you notice attempts collapsed from 980 to 174: the nudge did not make
bare `psql` succeed, it made agents stop reaching for it. What remains is the residual
hard cases, at a higher rate. The behaviour changed; the rate metric hides that, and
a report that showed only rates would have called a win a loss.

**These denominators are not controlled.** Usage volume shifts, the tool surface an
agent reaches for changes *because* of the nudge, and a rule only affects attempts
that come after it. This is correlation over time. The rigorous version needs
`tool_use_id` on the audit event — added in 0.4.0 — joined to the recorder's
`tool_call_id`, so a single nudged call can be followed to its own outcome. That data
is only now accumulating.

### The two that went the wrong way

A repo that publishes only its wins is asking to be taken on faith, so:

**`macos-timeout-not-installed` does nothing.** 26.2% → 26.8% across 82 post-promotion
attempts. The nudge fires, the agent reads it, and the failure rate is unmoved. On the
lifecycle report it sits in REVIEW, which is the correct verdict for the wrong reason —
it is not "enforcement works but the workflow is unfixed," it is a rule that has never
demonstrably helped. It should be rewritten or pruned.

**`fetch-dead-domain-retry` caused failures.** 7.0% → 17.3%. Its own `meta.why`
records the cause: the nudge message told agents to pass a flag that the tool did not
have, so every agent that followed the advice exited 2. A guard that fires 1,057
times — more than every other rule combined — and hands out an argument that does not
exist is worse than no guard. It was caught by this same loop and the message was
fixed; the tail is still in the numbers above.

That is the case for the `monitor` rung existing at all. Both of these were `nudge` —
advisory, recoverable, and survivable. Neither was a `block`. The graded-outcome table
below is not decoration.

## Try the pipeline in ten seconds

```bash
git clone https://github.com/kkrlstrm/callusguard.git && cd callusguard
./examples/demo/run.sh
```

No install, no dependencies, no network, no config. It plays a 7-event failure trace
through **record → derive → review → guard → audit → prune**, then runs the scope
check, and writes only to a temp directory it deletes on exit. Your real audit log and
rulesets are never touched.

**This is a pipeline smoke test, not evidence.** The trace is synthetic and produces
one rule; what it proves is that the stages connect and every artifact is real — the
derived rule, the exit-2 block, the hash-chained audit entry, the prune verdict. For
evidence that derivation is worth running, read [the funnel](#the-funnel) above; that
is measured against 97 days of production telemetry.

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

The recorder also exposes cc-logger's full verb set through `callus record` —
`serve`, `migrate`, `sessions`, `inspect`, `insights`, `rate` — plus `ingest` for
Codex rollout files.

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

**Version pin for the `block` claim.** "Exit 2 survives a parent's
`bypassPermissions`" is a statement about Claude Code's PreToolUse hook semantics, not
a property of this code — it is verified against **Claude Code 2.1.226** and Codex CLI
rollout-hook behaviour as of **2026-08-18**. Anthropic can change it in a release, and
if they do, your strongest guarantee degrades with no error message.

So the pin is enforced rather than written down: `callus guard doctor` reads the
installed host's version and warns when it differs from the verified one.

```console
$ callus guard doctor
hosts:
  ✓ claude 2.1.226 — exit-2 block semantics verified on this version
```

A newer host is a warning, never a failure — the guard still works; what becomes
unverified is specifically whether `block` still overrides a parent's
`bypassPermissions`. Re-check before relying on it as a hard stop.

The other three outcomes rest on documented, stable interfaces (`additionalContext`,
`permissionDecision`) and are far less exposed to this.

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
python3 -m unittest discover -s tests/guard     -p 'test_*.py'   # 102
python3 -m unittest discover -s tests/wroteonly -p 'test_*.py'   # 64
python3 -m unittest tests.test_dependency_wall                    #  3
python3 -m pytest tests/telemetry -q                              # 62 (+3 async)
```

The three async telemetry tests need `pip install 'callusguard[dev]'` for
`pytest-asyncio`; without it they error rather than fail quietly.

All 202 tests from the five predecessor repos were ported **unchanged** — only import
paths were rewritten. [MERGE.md](MERGE.md) gives the per-repo breakdown
(agent-guard 64, codex-guard 12, wroteonly 64, codex-logger 8, cc-logger 54). Fair
warning on how checkable that is: this repo's history begins at the merge, so you
cannot verify the "unchanged" part from these commits alone. Publishing the
predecessors read-only is the fix, and it has not happened yet.

## Docs

- [docs/WHEN_TO_USE.md](docs/WHEN_TO_USE.md) — the nudge-vs-block decision
- [docs/THREAT_MODEL.md](docs/THREAT_MODEL.md) — what this does and does not defend against
- [docs/TELEMETRY.md](docs/TELEMETRY.md) — what is captured, and what is redacted
- [docs/wroteonly.md](docs/wroteonly.md) — declared-write-set verification in depth
- [SECURITY.md](SECURITY.md) — how to report a vulnerability, and what counts as one
- [CHANGELOG.md](CHANGELOG.md) — release history, including the AGPL → Apache-2.0 move
- [MERGE.md](MERGE.md) — how five repos became one, and what was deliberately left apart

**This repository guards itself.** [`.claude/settings.json`](.claude/settings.json) wires
the `PreToolUse` guard over this checkout using the shipped starter ruleset — the same
thing a new adopter gets, with no repo-specific rules hand-written on top, because rules
here should be earned from telemetry like anyone else's. It paid for itself on day one:
wiring it surfaced that `doctor --project` could not detect the wiring `install.py`
writes, so the one command the threat model names for *"confirm the hook is actually
installed"* was reporting a protected project as unprotected.

## Why "callus"

A callus is laid down by repeated friction, at exactly the site of the damage — and it
**resolves when the friction is engineered away.** That is the rule lifecycle, and it
is the part most guardrail tooling gets backwards.

Shrinking is the tool working, not rotting.

## License

Apache-2.0 — see [LICENSE](LICENSE) and [NOTICE](NOTICE).
Copyright (C) 2026 Kai Karlstrom.

Relicensed from AGPL-3.0 in 0.4.0; the reasoning is in [NOTICE](NOTICE).
