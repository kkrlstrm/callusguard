#!/usr/bin/env bash
# The dogfood case: a weekly unattended job that writes to a library directory
# with no write-set guard, under --permission-mode bypassPermissions.
#
# The shape is a scheduled (launchd/cron) job in which a headless `claude -p` pass
# narrows a candidate list, reads sources for each survivor, and writes one <slug>.md
# per result into a reference library. Jobs like this fail quietly: in the run this
# example is modelled on, one execution stalled and another wrote nothing at all, and
# neither surfaced until someone looked.
#
# WHY THE CLI PATH AND NOT HOOKS, FOR THIS JOB SPECIFICALLY
#   The job runs `--permission-mode bypassPermissions`. In that mode Claude Code's
#   Write/Edit run in-process via fs.writeFileSync and are not subject to sandbox
#   filesystem isolation (anthropics/claude-code#29048) — so a guard that trusts the
#   tool stream is guarding the honest path. `declare` → run → `verify` fingerprints
#   the tree instead, which catches a write however it happened: Write tool, Bash
#   heredoc, or a script the agent wrote and then ran.
#
#   Hooks are still worth wiring (they block earlier and attribute better). They are
#   an optimisation here, not the guarantee.

set -euo pipefail

REPO="${REPO:-$HOME/your-repo}"
LIB="context/reference-library"
RUN_ID="library-$(date +%Y-%m-%d)"
WROTEONLY="${WROTEONLY:-$(command -v wroteonly)}"

# --- 1. declare intent and snapshot the baseline ---------------------------
# Stated before the agent runs, so the comparison afterwards is against a promise
# rather than against a guess.
"$WROTEONLY" declare \
  --run-id "$RUN_ID" \
  --root "$REPO" \
  --intent "Weekly library refresh: add net-new deep-dives to the reference library." \
  --create "$LIB/*.md" \
  --modify "$LIB/_provenance.json" \
  --modify "$LIB/entries.jsonl" \
  --modify "$LIB/index.json" \
  --modify "$LIB/INDEX.md" \
  --forbid '**/*.env' \
  --forbid '.claude/**' \
  --forbid 'scripts/**' \
  --forbid 'config/*.json' \
  --check "json=python3 -c \"import json,glob,sys;[json.load(open(f)) for f in glob.glob('$LIB/*.json')]\"" \
  --fail-direction open

# --- 2. run the agent ------------------------------------------------------
# Unchanged from the real job. wroteonly does not wrap, proxy, or slow it down.
set +e
timeout 5400 claude -p "$(cat "$REPO/$LIB/_prompt.md" 2>/dev/null || echo 'Run the weekly library refresh.')" \
  --permission-mode bypassPermissions \
  --max-budget-usd 20
AGENT_EXIT=$?
set -e

# --- 3. verify -------------------------------------------------------------
# Exit 2 = the agent wrote outside what it declared, or newly broke a check.
# Anything the job would normally do on failure goes here. Wire it to whatever your
# scheduled jobs already use to raise a human — an alert, a queue, a notification —
# so the failure cannot pass silently, which is the whole point.
if ! "$WROTEONLY" verify --run-id "$RUN_ID" --root "$REPO"; then
  echo "library job: write-set verification FAILED — see above." >&2
  # notify-your-oncall "library job wrote outside its declared set"
  exit 1
fi

echo "library job: agent exited $AGENT_EXIT; write set verified clean."
