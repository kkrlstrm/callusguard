# Turning your telemetry into rules

The three rules callusguard was born from weren't guessed — they came from ~37k
logged tool calls in a real repo, where a handful of failures kept recurring. A
failure that keeps happening is a job the model can't do reliably on its own yet.
That's the definition of a candidate rule.

`bin/callus derive` finds those clusters for you and writes them out as candidate
`monitor` rules. It never arms anything — you review, refine, and promote.

## The loop

```
observe failures  ──>  derive candidates  ──>  review + refine  ──>  promote      ──>  audit
(recorder / logs)     (derive_rules.py)       (edit the JSON)       monitor→nudge/block   (hash-chained)
        ^                                                                                    │
        └────────────────────────────  the audit log is the next round's telemetry  ─────────┘
```

## From a recorder database (the rich path)

The recorder records every tool call — `tool_name`,
`tool_input`, `status`, `error` — into Postgres. That's exactly the substrate a rule
needs. `derive_rules.py` shells out to `psql` (no Python driver required) and runs
[`sql/recurring_failures.sql`](../callusguard/derive/recurring_failures.sql):

```bash
python3 bin/callus derive --from-cc-logger --days 7 --min-count 3 \
    --db-url "$NEON_CC_LOGGER_URL" --out candidates.rules.json
```

You can also run the query by hand to eyeball the clusters first:

```bash
psql "$NEON_CC_LOGGER_URL" -f sql/recurring_failures.sql
```

### The denominator, and why the query reads successes too

The query returns `attempt_count` next to `fail_count`: every *settled* call of the
same command shape, successes included. `core/tiers.py` turns the pair into a tier
that caps how far the resulting rule may ever be armed — see the table in the
README. `--min-attempts N` moves the anecdotal floor (default 5); `--keep-anecdotal`
shows the thin clusters instead of withholding them.

Two details worth knowing before you tune it:

- **The denominator clusters on command *shape*, not on the error signature.** A
  successful call has no error to key on, so it cannot share the failure key. Bash
  shapes to its first real token after any `VAR=val` prefixes, plus the subcommand
  for known multiplexers — `git push`, not `git`, and `PGPASSWORD=x /usr/bin/psql`
  shapes to `psql`. Other tools shape to the tool, with MCP collapsed to its server
  surface. This is mirrored by `command_shape()` in `rules.py`; **if you change one,
  change both**, or the two input modes will grade the same workload differently.
- **`pending` and `orphaned` calls are not attempts.** They are not evidence in
  either direction, and counting them would deflate every failure rate.

The `--from-log` JSONL path computes the same denominator from the same file — but a
log that was already filtered down to failures has no successes to count, which would
hand every cluster a fabricated 100% failure rate. That case is detected: with no
successful call anywhere in the file, no denominator is supplied at all, every cluster
grades `unknown` (capped at a nudge), and a note says so on stderr.

### Read-only and context tools are skipped

A logger may capture tools that exist for *attribution* rather than for action —
callusguard records `Read` and `Skill` so you can tell which instruction, memory, or
skill a run actually loaded. Their failures are not rule material: a `Read` that
misses is the agent probing whether a file exists, not a failure mode worth guarding.
They are also high-volume enough to clear the threshold in any window, so left in they
would put the same "tool surface Read failed 47x" candidate in front of you every
single review — spending exactly the attention this tool exists to conserve.

`Read`, `Skill`, `Glob`, `Grep`, `TodoWrite` and `NotebookRead` are therefore excluded
by default. Adjust per run:

```bash
--exclude-tool SomeNoisyTool     # add one
--include-tool Read              # derive on a default-excluded tool anyway
```

## From a JSONL log (zero dependencies)

No recorder running? Point it at any newline-delimited JSON log of tool calls. A line needs
a tool name, an optional command, and a failure signal (`status: "failure"`, a
non-empty `error`, or `is_error: true`):

```bash
python3 bin/callus derive --from-log tool_calls.jsonl --out candidates.rules.json
```

## What you get

- **Bash failures** cluster by normalized error signature and become a candidate with
  a starting command pattern (`\bpsql\b`, `\bgit\s+push\b`, …). The regex is a
  *draft* — tighten it and write a message that tells the model what to do instead.
- **A whole tool surface that keeps failing** (an MCP server, `WebFetch`) becomes one
  tool-wide candidate. MCP methods roll up to their server (`mcp__Acme__*`), so a
  misconfigured server surfaces as a single signal — this is exactly how the original
  "MCP bound to the wrong account" deny rule was found.

Every candidate ships as `action: "monitor"` with `meta` recording the fail count,
the error signature, and the window. Nothing blocks or nudges until you promote it.

## Refine, then promote

1. Open `candidates.rules.json`. For each candidate worth keeping:
   - tighten the `any` regex (the draft matches on the leading command token only),
   - rewrite `message` to say what the model should do instead (add a correct example),
   - decide the action: `nudge` (recoverable) or `block` (irreversible) — see
     [WHEN_TO_USE.md](./WHEN_TO_USE.md).
2. Add a fixture in `tests/fixtures/` for the new rule (the fixture is its spec).
3. Merge it into your `rules/starter.rules.json` (or a repo-specific ruleset you
   point `AGENT_GUARD_RULES` at).

## Automating the review

The derive step is deterministic; the *judgment* (which candidates matter, how to
phrase them) is not. A natural pattern is a weekly agent that runs `derive_rules.py`
against your telemetry store, reasons over the candidates, and proposes rule/system changes
for you to approve. The failure counts tell you where the model keeps tripping — and
sometimes the right fix isn't a guard rule at all, but a helper script or a doc fix
so the failure can't happen in the first place.
