# Existence checks — run before implementation

**Run:** 2026-08-14 · **Instruction being followed:** *"Spend 30 minutes on GitHub/PyPI before
implementing each component and write down what you find, including if it kills the component."*
and *"If either is already well-served, **stop and report** rather than duplicating."*

> **A note on the citations below.** This document was written against an internal build spec
> (`SPEC.md`) and a 16k-word evidence corpus extracted from a private 93-company reference library.
> Neither is published — they contain local paths and unrelated private-stack detail. References to
> them are kept verbatim rather than scrubbed, because editing the record of what was checked, after
> the checking, would defeat the point of the record. Everything a reader needs to *evaluate* the
> findings is the public evidence: the repos, specs and issues linked inline, all of which were
> fetched directly rather than taken from search snippets.

**Verdict of this document: STOP AND REPORT. Do not build as specified.**
One component (`verdict`) is largely closed. The other (`wroteonly`) has its stated headline
contribution occupied, with a narrower wedge surviving.

Every finding below was verified by fetching the source repo or its specification, not by search
snippet alone. Star counts and push dates come from the GitHub API on 2026-08-14.

---

## Summary table

| Component | `SPEC.md` claim | Status after check | Killer evidence |
|---|---|---|---|
| `verdict` | "no MODIFY/CONSTRAIN anywhere"; "**No source publishes a verdict object schema**" (Appendix C) | ⛔️ **LARGELY CLOSED** | `microsoft/agent-governance-toolkit` — 5,912★, MIT, Python, pushed 2026-08-14. Five verdicts **including `transform`**, a formal MUST-language specification, and the unrecognized-transform-fails-closed rule already codified. |
| `wroteonly` | "Declaration ↔ actual write-set diff — ✅ **Yes — this is the contribution**" (§3.2) | ⚠️ **PRIMITIVE OCCUPIED, narrower wedge survives** | `logi-cmd/agent-guardrails` ships `--intended-files` (agent-declared, per task) and flags "changes outside the declared task". `ZhangHanDong/agent-spec` (445★, active) enforces declared `Boundaries` globs against a VCS change set. |

---

## Check A — `verdict`: graded policy-decision vocabulary with a transform outcome

### A.1 The killer: Microsoft Agent Governance Toolkit (AGT)

`https://github.com/microsoft/agent-governance-toolkit` — **5,912 stars, MIT, Python, created
2026-03-02, last pushed 2026-08-14 (same day as this check).** Self-described: *"Policy enforcement,
zero-trust identity, execution sandboxing, and reliability engineering for autonomous AI agents.
Covers 10/10 OWASP Agentic Top 10."*

Its `policy-engine/` ships the **Agent Control Specification (ACS)**, a formal contract at
`policy-engine/spec/SPECIFICATION.md`. Verified verbatim from that file:

**The enum — five outcomes, including transform:**

> "One of `allow`, `deny`, `warn`, `escalate`, `transform`."

**The verdict object is a published schema.** Members: `decision`, `reason`, `message`, `transform`,
`evidence`, `result_labels`.

**Transform structure:**

> "`path` is a string MUST be rooted at `$policy_target`. `value` is any JSON value to set at `path`."

**Fail-closed, mandated:**

> "The runtime fails closed. Any error during evaluation MUST yield a `deny` verdict whose reason is
> one of the reserved identifiers in section 16. The runtime MUST NOT apply a transform on any path
> that ends in a runtime error."

**The rule `SPEC.md` §2.2 calls "non-negotiable" and "the difference between a safe spec and a
liability" — Microsoft already has it:**

> "A `transform` whose `path` is rooted outside `$policy_target` MUST fail closed with
> `runtime_error:transform_target_forbidden`. A `transform` whose `path` cannot be parsed, whose
> `path` does not resolve against the policy target, whose `value` cannot be set because of a path
> type mismatch MUST fail closed with `runtime_error:transform_invalid`."

### A.2 What this falsifies in the spec, precisely

`docs/evidence-corpus.md` §2.5 relayed the corpus's existence check as: *"`verdict` … ✅ **OPEN** —
**OPA binary, Cedar adds Escalate only, no MODIFY/CONSTRAIN anywhere**"* — and correctly warned that
this rested on a negative search result. It did. The negative was wrong.

Appendix C's line *"A verdict `reason` field, policy-id field, or decision envelope schema anywhere.
… **No source publishes a verdict object schema.**"* was true of the 93-company corpus and false of
the open-source world at the time it was written. AGT has `decision` + `reason` + a bound `policy.id`
+ a full envelope, formally specified.

Also relevant, not checked in depth: **AWS Cedar shipped as the policy engine inside Amazon Bedrock
AgentCore Policy in March 2026**, intercepting agent tool calls at the gateway boundary. So the two
largest cloud vendors both moved into this exact space in Q1 2026 — after the reference library's
sources were read.

### A.3 What genuinely survives AGT

Four things. They are real, but they are **features of AGT, not a project**:

1. **`NOT_APPLICABLE` vs `INDETERMINATE`.** AGT does not have this split — every failure, including a
   missing policy binding, collapses to `deny` with a reserved reason. This is the spec's own claimed
   "real improvement over every implementation surveyed," and it still stands. But note AGT's
   collapse is a *deliberate fail-closed design*, not an oversight.
2. **Multi-policy combining algorithms.** AGT explicitly evaluates *"one intervention point of one
   agent at a time,"* with each point binding exactly one policy — so it has no precedence problem to
   solve. agent-guard, kgg and Openbox all do (most-restrictive-wins). This is the largest surviving
   gap, and the one closest to Kai's own code.
3. **A `target` discriminator richer than a JSON path.** AGT's `{path, value}` rooted at
   `$policy_target` discriminates *where* structurally. It does not express the six-way taxonomy in
   `evidence-corpus.md` §2.5 (tool args / payload / tool result / **tool surface** / **reasoning
   context** / response envelope) — "narrow the available toolset" and "inject into the reasoning
   context" are not JSON-path edits.
4. **`deterministic: bool` on a transform.** AGT asserts determinism by construction ("the runtime is
   deterministic"). That structurally excludes Archestra's `Dual LLM` — a second model's lossy rewrite
   — from being expressible at all. Carrying the flag is more expressive; AGT's choice is simpler.

**Honest read:** items 1–4 are a pull request to `microsoft/agent-governance-toolkit`, or a small
adapter layer. They are not a standalone vocabulary project. A 200-line library whose pitch is "like
the Microsoft one but with two more enum members" will not be adopted, and `SPEC.md`'s own stated
killer risk — *a vocabulary with no consumer dies* — gets worse, not better, when a 5.9k-star
alternative already has consumers.

### A.4 Prior art the spec already anticipated, confirmed

XACML-in-Python is well-served and was correctly identified as the ancestor: `ndg-xacml` (XACML 2.0,
parses Policies **and Obligations**), `py-abac` (ABAC toolkit, "design stems from the XACML
standard"), `sabac`. None is agent-tool-call oriented — the spec's framing ("port XACML to agent tool
calls") was right, but Microsoft got there first with the port.

### A.5 Name availability

`verdict` is **taken on both PyPI and npm** (both return 200). This is secondary to A.1 but would
have forced a rename regardless.

---

## Check B — `wroteonly`: declaration ↔ actual write-set diff

### B.1 The direct hit: `logi-cmd/agent-guardrails`

`https://github.com/logi-cmd/agent-guardrails` — **8 stars, MIT, JavaScript, created 2026-03-22,
last pushed 2026-04-26** (≈3.5 months stale). *"Merge gates and safety checks for AI coding agents.
Works with Claude Code, Cursor, Windsurf, Codex via MCP. Detect scope violations, missing tests, and
risks before merge."*

It implements the primitive `SPEC.md` §3.2 claims as the contribution. Verified from its README:

```
agent-guardrails plan --task "Add input validation" \
  --intended-files "src/add.js,tests/add.test.js" \
  --allow-paths "src/,tests/,evidence/"
```

The intended write set is **declared per task, at invocation**, and the check flags *"changes outside
the declared task, allowed paths, or intended files."* It emits *"score, verdict, findings, next
actions"* with a configurable `violationSeverity` (`error` blocks, `warning` passes) and a
`violationBudget`.

That is declaration → act → diff-against-declaration → graded outcome. The claim
*"Nothing found in 93 companies or in the OSS landscape"* (§3.2) is **false**.

**Mitigating facts, stated fairly:** 8 stars, one fork, no commits in 3.5 months, JavaScript, and
positioned as a *pre-merge* gate rather than an in-run hook. It is occupancy, not dominance.

### B.2 The healthy neighbour: `ZhangHanDong/agent-spec`

`https://github.com/ZhangHanDong/agent-spec` — **445 stars, MIT, Rust, created 2026-03-07, last
pushed 2026-08-14 (today).** An AI-native BDD/spec verification tool.

Its task contracts carry a `Boundaries` section with **"Allowed Changes"** and **"Forbidden"** glob
patterns, and *"path-like entries are mechanically enforced against a change set"* discovered via
`--change-scope {none|staged|worktree|jj}`. Verdicts: `pass`, `fail`, `skip`, `uncertain`,
`pending_review`.

**The distinction that survives here is real:** `agent-spec`'s boundaries are **human-authored in a
spec file** ahead of the work, like a `.gitignore` for writes. `wroteonly`'s declaration is stated by
**the agent, per invocation, in its own words** — the difference between "policy says you may touch
`src/**`" and "*you said* you would touch these four files; you touched seven." The second catches an
in-policy but out-of-intent write. That is a genuinely different check, and it is the sharpest
remaining claim.

### B.3 The static-permissions alternative (the "why not just use hooks?" objection)

Claude Code already supports path-scoped `permissions` deny/allow rules and
`sandbox.filesystem.allowWrite`, and there is an open feature request (anthropics/claude-code #49783)
for first-class `allowed_paths`. A README must answer why declaration beats configuration.

Two facts from the check make that answer easy, and both **strengthen** `wroteonly`:

- **Static allowlists are bypassable in exactly the spec's dogfood scenario.**
  anthropics/claude-code #29048 reports that with `permissionMode: "bypassPermissions"` and sandbox
  enabled, Write/Edit run in-process via `fs.writeFileSync` and are **not** subject to
  `sandbox.filesystem.allowWrite`. The archref job (`SPEC.md` §3.5) runs exactly
  `--permission-mode bypassPermissions`.
- **Pre-hoc path blocking is whack-a-mole.** Practitioner consensus: block `Write` and the model uses
  a Bash heredoc; block `rm` and it uses `perl -e "unlink(...)"`. This is a direct argument for
  **post-hoc observation of the actual write set** over pre-hoc path gating — i.e. for `wroteonly`'s
  design over the feature request's.

### B.4 What is genuinely unoccupied

**Nothing found does per-run ephemeral baseline error filtering.** Neither `agent-guardrails` nor
`agent-spec` suppresses pre-existing checker failures; `agent-spec` explicitly verifies the contract
"deterministically at present" with no differential filtering. The §3.2 table's honest concession —
that `betterer`/`linthell`/`reviewdog` own new-errors-only — remains right, and *combining* it with a
per-invocation declaration remains unbuilt.

### B.5 Name availability

`wroteonly` is **free** on PyPI, npm, and GitHub (zero repo-name matches). No blocker.

---

## Recommendation

**`verdict`: do not build as a standalone project.** The gap it was scoped to fill was closed in
March–August 2026 by Microsoft (and, adjacently, AWS). The four surviving deltas (§A.3) are best
spent as an upstream contribution or a thin adapter, and that decision is Kai's, not mine.

**`wroteonly`: buildable, but the pitch must change.** The contribution is no longer
"the declaration↔actual diff." It is the **conjunction**, which nothing found does:

> agent-stated intent (not human-configured policy)
> × per-run ephemeral baseline (not a committed one)
> × enforcement at the Claude Code tool boundary that survives `bypassPermissions`.

That is narrower and less quotable than `SPEC.md` §3.2, and it is what the evidence supports.

## What was NOT checked

Stated so the limits of this document are clear:

- AGT's actual code quality, adoption, or whether the ACS spec matches its implementation. Only the
  README and `SPECIFICATION.md` were read.
- AWS Cedar / Bedrock AgentCore Policy beyond a search-result summary. Not fetched.
- Whether `agent-guardrails`'s `--intended-files` is enforced at the tool boundary or only compared at
  check time. Its MCP mode suggests in-run capability; not confirmed in source.
- npm/crates.io beyond the two names above; no crates.io search was run.
