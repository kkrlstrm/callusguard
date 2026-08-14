#!/usr/bin/env bash
# The whole loop, on one failure, in about ten seconds.
#
#   ./examples/demo/run.sh
#
# No dependencies, no network, no model calls, no config. It writes only to a
# temp directory and deletes it on exit — your real audit log and rulesets are
# never touched.
#
# ABOUT THE TRACE
#   examples/demo/trace.jsonl is a synthetic 7-event trace, shaped after the
#   failure pattern that produced one of the shipped starter rules. It is NOT a
#   dump of anyone's real session — the point is that you can run it and get the
#   same artifacts the real pipeline produces, not that these particular rows
#   happened.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
CALLUS="$REPO/bin/callus"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

b() { printf '\n\033[1m%s\033[0m\n' "$*"; }
d() { printf '\033[2m%s\033[0m\n' "$*"; }

b "1. RECORD — what the agent actually did"
d "   examples/demo/trace.jsonl — 7 tool calls, 5 of them the same failure"
grep -c . "$HERE/trace.jsonl" | xargs printf '   %s events\n'
python3 - "$HERE/trace.jsonl" <<'PY'
import json, sys, collections
rows = [json.loads(l) for l in open(sys.argv[1]) if l.strip()]
fails = collections.Counter(r["error"].split(":")[0] for r in rows if r.get("status") == "failure")
for err, n in fails.items():
    print("   %dx  %s" % (n, err))
PY

b "2. DERIVE — turn the recurring failure into a candidate rule"
d "   \$ callus derive --from-log trace.jsonl --out candidate.rules.json"
python3 "$CALLUS" derive --from-log "$HERE/trace.jsonl" \
    --min-count 3 --out "$WORK/candidate.rules.json" | sed 's/^/   /'
python3 - "$WORK/candidate.rules.json" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
for r in d["rules"]:
    print("   id      : %s" % r["id"])
    print("   action  : %s   <- monitor-only. It is NOT armed." % r["action"])
    print("   matches : %s" % (r.get("any") or [])[:1])
PY

b "3. REVIEW — a human promotes it. This step is deliberately not automated."
d "   monitor -> block, and give it a message worth reading"
python3 - "$WORK/candidate.rules.json" "$WORK/armed.rules.json" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
for r in d["rules"]:
    r["action"] = "block"
    r["message"] = "Bare psql keeps failing here — pass a DSN or use scripts/db.py."
json.dump(d, open(sys.argv[2], "w"), indent=2)
print("   promoted %d rule(s) to block" % len(d["rules"]))
PY

b "4. GUARD — the agent tries it again"
d "   the hook receives a PreToolUse payload and decides"
cat > "$WORK/payload.json" <<'JSON'
{"hook_event_name":"PreToolUse","session_id":"demo","cwd":"/tmp",
 "tool_name":"Bash","tool_input":{"command":"psql -c \"select 1\""}}
JSON
# Read the GUARD's exit status, not a pipeline's. `echo ... | guard | sed` would
# hand back echo's status via PIPESTATUS[0] and cheerfully report exit=0 on a
# hard block — a demo that misreports its own result is worse than no demo.
set +e
AGENT_GUARD_RULES="$WORK/armed.rules.json" AGENT_GUARD_AUDIT="$WORK/audit.jsonl" \
  python3 "$REPO/bin/guard-hook.py" < "$WORK/payload.json" > "$WORK/out.txt" 2>&1
RC=$?
set -e
sed 's/^/   /' "$WORK/out.txt"
echo "   exit=$RC   (2 = hard block; survives --permission-mode bypassPermissions)"

b "5. AUDIT — the verdict is on a hash chain, with the command redacted"
python3 "$CALLUS" guard audit --path "$WORK/audit.jsonl" | sed 's/^/   /'
python3 - "$WORK/audit.jsonl" <<'PY'
import json, sys
e = json.loads(open(sys.argv[1]).read().strip().splitlines()[-1])
print("   decision: %s   fired: %s" % (e["decision"], e["fired"]))
print("   command : %s" % e.get("command_preview"))
print("   sha256  : %s…" % e.get("command_sha256", "")[:16])
PY

b "6. PRUNE — six weeks later, someone fixed the connection string for good"
d "   the rule stops firing, so it stops earning its place"
# Age the real audit log past the window, rather than pointing prune at a missing
# file. Both produce "PRUNE", but only this one demonstrates the actual judgement:
# the log is present and readable, and the rule simply stopped firing. (The
# missing-log path deliberately warns instead; that is covered by tests.)
python3 - "$WORK/audit.jsonl" "$WORK/aged.jsonl" <<'PY'
import json, sys
from datetime import datetime, timezone, timedelta
old = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat(timespec="seconds")
with open(sys.argv[2], "w") as out:
    for line in open(sys.argv[1]):
        if line.strip():
            e = json.loads(line); e["ts"] = old
            out.write(json.dumps(e) + "\n")
print("   (same audit log — its last verdict is now 60 days old)")
PY
python3 "$CALLUS" guard prune "$WORK/armed.rules.json" \
    --audit "$WORK/aged.jsonl" --days 30 | sed 's/^/   /'

b "7. SCOPE — and separately: did the run touch only what it declared?"
mkdir -p "$WORK/proj/docs" "$WORK/proj/scripts"
echo "build" > "$WORK/proj/scripts/build.py"
export WROTEONLY_STATE="$WORK/state"
python3 "$CALLUS" scope declare --run-id demo --root "$WORK/proj" \
    --create 'docs/**/*.md' --forbid 'scripts/**' >/dev/null
d "   declared: may create docs/**/*.md · must not touch scripts/**"
echo "# notes" > "$WORK/proj/docs/notes.md"          # allowed
echo "tampered" > "$WORK/proj/scripts/build.py"      # not allowed
set +e
python3 "$CALLUS" scope verify --run-id demo --root "$WORK/proj" | sed 's/^/   /'
echo "   exit=${PIPESTATUS[0]}"
set -e

b "Done."
d "   Nothing outside $WORK was written. It is now deleted."
