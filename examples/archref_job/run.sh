#!/usr/bin/env bash
# The dogfood case: a weekly unattended job that writes to a library directory
# with no write-set guard, under --permission-mode bypassPermissions.
#
# This is the shape from kai-gtm-agents/scripts/launchd/archref-discover-run.sh:
# a headless `claude -p` pass narrows a candidate list, reads docs for each
# survivor, and writes <slug>.md files into the reference library. It has already
# had one run stall and one run write nothing.
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

REPO="${REPO:-$HOME/kai-gtm-agents}"
LIB="context/knowledge-hub/architecture-reference"
RUN_ID="archref-$(date +%Y-%m-%d)"
WROTEONLY="${WROTEONLY:-$HOME/wroteonly/bin/wroteonly}"

# --- 1. declare intent and snapshot the baseline ---------------------------
# Stated before the agent runs, so the comparison afterwards is against a promise
# rather than against a guess.
"$WROTEONLY" declare \
  --run-id "$RUN_ID" \
  --root "$REPO" \
  --intent "Weekly archref discovery: add net-new company deep-dives to the library." \
  --create "$LIB/*.md" \
  --modify "$LIB/_provenance.json" \
  --modify "$LIB/drivers.jsonl" \
  --modify "$LIB/concept-index.json" \
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
timeout 5400 claude -p "$(cat "$REPO/$LIB/_prompt.md" 2>/dev/null || echo 'Run the weekly archref extract.')" \
  --permission-mode bypassPermissions \
  --max-budget-usd 18
AGENT_EXIT=$?
set -e

# --- 3. verify -------------------------------------------------------------
# Exit 2 = the agent wrote outside what it declared, or newly broke a check.
# Anything the job would normally do on failure goes here; in the real job this is
# where the attention inbox gets an entry instead of the run passing silently.
if ! "$WROTEONLY" verify --run-id "$RUN_ID" --root "$REPO"; then
  echo "archref: write-set verification FAILED — see above." >&2
  # python3 -c "import sys;sys.path.insert(0,'$REPO/scripts/lib');
  #             from attention import enqueue;
  #             enqueue(source='archref', severity='error',
  #                     title='archref wrote outside its declared set', detail='...')"
  exit 1
fi

echo "archref: agent exited $AGENT_EXIT; write set verified clean."
