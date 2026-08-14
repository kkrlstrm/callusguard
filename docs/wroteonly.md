# wroteonly

<!-- portfolio-status -->
**Status:** New — built against a real unattended job in my own stack; not yet production-aged. · **Layer:** Execution controls · **[Portfolio map ›](https://github.com/kkrlstrm)**

**Make the agent say what it will touch. Then check.**

An agent declares its intended write set before it acts. wroteonly fingerprints the
tree, gets out of the way, and afterwards diffs what actually changed against what was
declared — surfacing only the errors *this run* introduced.

It works with Claude Code and the OpenAI Codex CLI as hooks, and with any agent at all
as three lines of shell.

```console
$ wroteonly verify --run-id archref-2026-08-14
✗ wroteonly: deny — Wrote 1 path(s) outside the declaration: scripts/build.py

  Outside the declaration:
    modified scripts/build.py

  Declared: context/knowledge-hub/architecture-reference/*.md

  To resolve: revert the writes listed above, or re-run with a
  declaration that includes them if they were intended.
$ echo $?
2
```

## The failure mode

Coding agents do more than you asked. Not maliciously — they refactor a helper on the
way past, fix an unrelated import, leave a file behind. The change is usually *correct*
and in the *wrong place*, which is exactly what review is worst at catching.

The tooling that exists checks the wrong thing:

> A path allowlist says where the agent **may** write.
> A declaration says where it **said it would**.

Those differ, and the gap is where scope creep lives. `permissions.deny` on `src/**`
happily passes an agent that named four files and touched seven — every one of them
inside `src/`.

Worse, the allowlist is routable-around. Block `Write` and the model uses a Bash
heredoc. Block `rm` and it reaches for `perl -e "unlink(...)"`. And under
`--permission-mode bypassPermissions`, Claude Code's Write/Edit run in-process via
`fs.writeFileSync` and skip sandbox filesystem isolation entirely
([claude-code#29048](https://github.com/anthropics/claude-code/issues/29048)) — which
is the mode unattended jobs actually run in.

So wroteonly does not gate the tool. **It fingerprints the tree.** A write is caught
however it happened.

## The loop

```
  declare ───► baseline ───► [ the agent runs ] ───► observe ───► verify
     │            │                                     │           │
  intended     file hashes                          what really   diff + new
  write set    + current                            changed       errors only
               checker output                                        │
                                                                     ▼
                                                        allow · warn · escalate · deny
```

The baseline is captured **per invocation, immediately before the agent acts, and
never committed.** That is the whole difference from every other baseline tool — see
[Prior art](#prior-art), which is longer than the novelty section on purpose.

## Install

```bash
git clone https://github.com/kkrlstrm/wroteonly.git ~/wroteonly
python3 ~/wroteonly/install.py --dry-run     # see exactly what it would write
python3 ~/wroteonly/install.py               # wire both hosts, merge-aware, backed up
```

Zero dependencies. Python 3.9+. No network, no model calls, nothing to configure
globally. **wroteonly stays completely inert until a declaration exists** — installing
it does not change a single run until you write one.

Uninstall removes only its own entries: `python3 install.py --uninstall`.

## How it works

Write a declaration at `.wroteonly.json` in the project (or point
`$WROTEONLY_DECLARATION` at one):

```json
{
  "intent": "add password reset to the auth module",
  "create": ["src/auth/reset.py"],
  "modify": ["src/auth/__init__.py", "src/app.py"],
  "forbid": ["**/*.env", "config/**"],
  "checks": [{"name": "lint", "command": "ruff check ."}],
  "fail_direction": "open"
}
```

`create` and `modify` are separate because they are different risks: creating a file
the agent named is nearly always fine, silently rewriting one it did not name is the
thing you are looking for. `modify` permits creation; `create` does not permit
modification. `forbid` beats both and cannot be lifted.

Globs are gitignore-flavoured — `*` does not cross a `/`, `**` does. (`fnmatch` would
have quietly matched `src/sub/deep.py` against `src/*.py`; there is a unit test pinning
that it doesn't.)

### What runs when

| Event | What wroteonly does | Can it stop the write? |
|---|---|---|
| `PreToolUse` | Blocks a `Write`/`Edit`/`MultiEdit`/`NotebookEdit` naming a path outside the declaration | **Yes** — exit 2, survives `bypassPermissions` on both hosts |
| `PostToolUse` | Records the call, for attribution | No on Claude Code ("tool already ran"); yes on Codex |
| `Stop` | Rescans, diffs, re-runs the checks, emits the verdict | **Yes** — refuses to let the agent stop and hands it the report |

`Bash` is deliberately **not** parsed. Guessing which files a shell command will touch
is exactly the guesswork this tool refuses to do — those writes are caught by the scan
instead, which is why the scan is the guarantee and the hook is the optimisation.

### Without hooks

The hookless path is not second-class. It is the better integration for unattended
jobs, and it works with any agent on any host:

```bash
wroteonly declare --run-id "$JOB" --intent "refresh the library" \
    --create 'context/**/*.md' --forbid '**/*.env'
claude -p "$PROMPT" --permission-mode bypassPermissions    # or codex, or anything
wroteonly verify --run-id "$JOB" || exit 1
```

A worked version against a real weekly job is in
[examples/archref_job/](examples/archref_job/).

## New errors only

If a declaration answers *where did it write*, the checks answer *what did it break* —
without drowning you in what was already broken.

wroteonly runs your project's own checks once before the agent acts and once after,
then subtracts. **The tool does the subtraction, not the model.** Handing an agent the
full error list and asking it to work out which ones are its fault burns context, makes
correctness depend on the model's arithmetic, and reintroduces the exact bug where a
pre-existing failure masks a new one.

Findings are identified by `(file, code, normalised message)` — **not line number**.
Insert ten lines at the top of a file and every finding below shifts; keying on
position would report the whole file as newly broken. The trade, stated plainly: two
identical errors in one file share an identity, so a *second* occurrence of an error
that already exists is not flagged as new.

## Verdicts

wroteonly does not define a verdict vocabulary. It emits Microsoft's
[Agent Control Specification](https://github.com/microsoft/agent-governance-toolkit)
shape — `allow` · `warn` · `escalate` · `deny` — so the output is readable by anything
that already speaks ACS.

That is a deliberate choice. Nine closed-source vendors independently invented nine
incompatible graded verdict vocabularies; publishing a tenth is how that ends badly.

| Verdict | When | Exit |
|---|---|---|
| `allow` | Everything written was declared; no new findings | 0 |
| `warn` | New checker findings, or a degraded check | 0 |
| `escalate` | Files changed but the run declared nothing — a human should look | 0 |
| `deny` | Wrote, deleted, or escaped outside the declaration | 2 |

`fail_direction` decides what a *tooling* failure does — a checker that won't run, a
scan that can't complete. Default `open`. It never applies to a violated declaration,
which always fails closed, and a fail-open is never silent: the verdict carries
`degraded: true` and names what broke.

> **A wroteonly bug must never wedge a session.** Every entry point falls open on an
> internal error. The only thing allowed to stop your agent is a decision, not a crash.

## Tests

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
```

64 tests, stdlib only. The hook tests drive the real entry script as a subprocess with
a JSON payload on stdin and assert on exit code and stdout shape, because that is the
actual contract with the host — a test of the Python function would pass while the
thing the host sees was broken.

## Prior art

**Most of this is not new, and the parts that aren't are the parts that already
worked.** Read this section before believing anything above it.

| Piece | Who already does it |
|---|---|
| Declaring an intended file set and diffing it | **[agent-guardrails](https://github.com/logi-cmd/agent-guardrails)** — `--intended-files` per task, flags changes outside it. **[agent-spec](https://github.com/ZhangHanDong/agent-spec)** — `Boundaries` globs enforced against a VCS change set |
| Reporting only new errors vs a baseline | **[linthell](https://github.com/vrslev/linthell)** (Python), **[betterer](https://github.com/phenomnomnominal/betterer)** (JS/TS), **[reviewdog](https://github.com/reviewdog/reviewdog)** (diff-keyed) |
| The graded verdict vocabulary | **[Microsoft Agent Control Specification](https://github.com/microsoft/agent-governance-toolkit)** — adopted here verbatim, not reinvented |
| Observing an agent's actual write set | Witan Labs' `accesses` array — observation without a declaration to compare it to |
| Holding changes until verification passes | Witan's in-memory workbook until `--save`; standard staging patterns |

What is left, and it is narrower than it first looked — the **conjunction**:

- the intent is stated **by the agent, per invocation**, not configured by a human in
  advance (agent-spec's boundaries are authored ahead of time, like a `.gitignore`);
- the baseline is **ephemeral and per-run**, so it catches an error introduced in a
  file the agent never opened (reviewdog structurally cannot; betterer and linthell
  need a committed, deliberately regenerated file);
- enforcement happens at the **agent's own tool boundary**, and survives
  `bypassPermissions`.

| Tool | Baseline is… | Keyed to | Catches a break in an *unchanged* line? |
|---|---|---|---|
| reviewdog | the PR diff | changed **lines** | no — structurally cannot |
| betterer / linthell | a **committed** file | the codebase over time | yes |
| **wroteonly** | captured **per run**, never committed | **one agent invocation** | yes |

Full write-up, including the checks that shrank this project's claims:
[docs/existence-checks-2026-08-14.md](docs/existence-checks-2026-08-14.md).

## Scope: a verifier, not a sandbox

State the limits as loudly as the capability.

- **Not a sandbox.** An agent determined to escape can `chmod`, spawn a detached
  process, or write outside the scanned root. wroteonly reports; it does not confine.
  The durable boundary is an OS sandbox or a container.
- **The `PreToolUse` gate is partial by design.** It sees path-naming tools only. Bash
  writes are caught at `Stop`, after they have happened — recoverable, not prevented.
- **`Stop` is a gate, not a rollback.** It refuses to let the agent finish and hands it
  the report. It does not revert the writes. There is no undo.
- **The scan is the whole tree.** On a very large repo the fingerprint pass costs real
  seconds; narrow the root, or accept it. Files over 8 MiB fall back to size+mtime and
  the report says which rows those are.
- **Nothing here is tamper-proof.** An agent that can edit `.wroteonly.json` can widen
  its own declaration. Keep the declaration outside the tree the agent may write, or
  pin it with `$WROTEONLY_DECLARATION`.
- **A declaration is a claim, not a proof.** wroteonly checks that the claim held. It
  cannot tell you the claim was the right one to make.

## Docs

- [docs/existence-checks-2026-08-14.md](docs/existence-checks-2026-08-14.md) — what was already built, and what that killed off in this project

Every module also opens with a docstring explaining the job it owns and the invariant it
upholds; those are the real spec. Start with
[`wroteonly/report.py`](wroteonly/report.py) for the decision table and
[`wroteonly/hosts/__init__.py`](wroteonly/hosts/__init__.py) for where the two hosts
genuinely differ.

The build spec and the source-evidence corpus behind the design are not published —
they carry local paths and unrelated private-stack detail.

## License

GNU AGPL-3.0 — see [LICENSE](LICENSE). Copyright (C) 2026 Kai Karlstrom.

---

<!-- portfolio-footer -->
## Where this fits

Part of a portfolio of **governed, AI-native GTM systems** — reference implementations and reusable patterns extracted from a private production stack. In that system this is the check that turns an agent's stated intent into a machine-verified transaction.

**Full portfolio map → [github.com/kkrlstrm](https://github.com/kkrlstrm)**

Works with:
- [agent-guard](https://github.com/kkrlstrm/agent-guard) — screens the tool call before it runs; wroteonly checks what the whole run actually did
- [codex-guard](https://github.com/kkrlstrm/codex-guard) — the same control surface for the Codex CLI, the second host wroteonly targets
