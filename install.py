#!/usr/bin/env python3
"""Wire callusguard's hooks into Claude Code and/or the OpenAI Codex CLI.

Two hooks, three events, two hosts — one merge-aware installer.

    Claude Code   ~/.claude/settings.json    hooks.<Event>[].hooks[]
    Codex         ~/.codex/hooks.json        hooks.<Event>[].hooks[]

    guard-hook.py       PreToolUse      screen every tool call
    wroteonly-hook.py   PreToolUse      block a write outside the declaration
                        PostToolUse     record what was touched (attribution)
                        Stop            verify the run, refuse to stop on a violation

Merge-aware (never clobbers another tool's hooks), idempotent (re-running updates in
place), and it backs up whatever was there first.

    python3 install.py                    # both hosts, whichever are present
    python3 install.py --host codex       # just one
    python3 install.py --only guard       # just one hook
    python3 install.py --dry-run          # print what would be written
    python3 install.py --uninstall        # remove only callusguard's entries

Codex trust-pins the hook command by hash (`trusted_hash` in config.toml), so it may
prompt once per hook. `bin/*.py` are deliberately stable shims that never need
editing — keep churn in the rules JSON and the declaration JSON, neither of which is
hashed, and you will not be re-prompted.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time

REPO = os.path.dirname(os.path.abspath(__file__))
MARKER = "callusguard-hook"  # written into every entry so uninstall is surgical

HOOKS = {
    "guard": {
        "script": os.path.join(REPO, "bin", "guard-hook.py"),
        "status": "callusguard screening tool call",
        "events": {
            "PreToolUse": "Bash|apply_patch|Edit|MultiEdit|Write|NotebookEdit|mcp__.*",
        },
        "timeout": 15,
    },
    "wroteonly": {
        "script": os.path.join(REPO, "bin", "wroteonly-hook.py"),
        "status": "callusguard verifying the declared write set",
        "events": {
            "PreToolUse": "Edit|MultiEdit|Write|NotebookEdit",
            "PostToolUse": "Edit|MultiEdit|Write|NotebookEdit",
            "Stop": ".*",
        },
        "timeout": 120,
    },
}

TARGETS = {
    "claude-code": os.path.join(os.path.expanduser("~"), ".claude", "settings.json"),
    "codex": os.path.join(
        os.environ.get("CODEX_HOME") or os.path.join(os.path.expanduser("~"), ".codex"),
        "hooks.json"),
}


def _entry(spec: dict) -> dict:
    return {
        "type": "command",
        "command": '%s "%s"' % (sys.executable, spec["script"]),
        "statusMessage": spec["status"],
        "timeout": spec["timeout"],
        "_source": MARKER,
    }


def _load(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _is_ours(hook: dict) -> bool:
    if hook.get("_source") == MARKER:
        return True
    cmd = hook.get("command") or ""
    return any(os.path.basename(s["script"]) in cmd for s in HOOKS.values())


def _strip(groups) -> list:
    """Remove only callusguard's hooks, preserving every other tool's."""
    kept = []
    for group in groups or []:
        hooks = [h for h in group.get("hooks", []) if not _is_ours(h)]
        if hooks:
            kept.append(dict(group, hooks=hooks))
        elif not group.get("hooks"):
            kept.append(group)
    return kept


def wire(doc: dict, which, uninstall: bool) -> dict:
    hooks = doc.get("hooks")
    if not isinstance(hooks, dict):
        hooks = {}
    doc["hooks"] = hooks

    events = sorted({e for name in which for e in HOOKS[name]["events"]})
    for event in events:
        groups = _strip(hooks.get(event, []))
        if not uninstall:
            for name in which:
                matcher = HOOKS[name]["events"].get(event)
                if matcher:
                    groups.append({"matcher": matcher,
                                   "hooks": [_entry(HOOKS[name])]})
        if groups:
            hooks[event] = groups
        elif event in hooks:
            del hooks[event]

    if not hooks:
        doc.pop("hooks", None)
    return doc


def apply_to(host: str, path: str, which, uninstall: bool, dry_run: bool) -> bool:
    parent = os.path.dirname(path)
    if not os.path.isdir(parent):
        print("·  %-12s skipped — %s does not exist" % (host, parent))
        return False

    doc = wire(_load(path), which, uninstall)
    rendered = json.dumps(doc, indent=2) + "\n"

    if dry_run:
        print("# --dry-run (%s) — would write %s:\n%s"
              % ("uninstall" if uninstall else "install", path, rendered))
        return True

    if os.path.exists(path):
        backup = "%s.bak-%s" % (path, time.strftime("%Y%m%d-%H%M%S"))
        shutil.copy2(path, backup)
        print("·  backed up %s -> %s" % (path, backup))

    os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(rendered)

    print("✓  %-12s %s %s" % (host, "cleaned" if uninstall else "wired", path))
    return True


def main() -> int:
    ap = argparse.ArgumentParser(description="Install callusguard's hooks.")
    ap.add_argument("--host", choices=sorted(TARGETS) + ["all"], default="all")
    ap.add_argument("--only", choices=sorted(HOOKS) + ["all"], default="all",
                    help="install just one of the two hooks")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--uninstall", action="store_true")
    args = ap.parse_args()

    which = sorted(HOOKS) if args.only == "all" else [args.only]
    for name in which:
        if not os.path.exists(HOOKS[name]["script"]):
            sys.stderr.write("✗ %s is missing — run from a complete checkout.\n"
                             % HOOKS[name]["script"])
            return 3

    hosts = sorted(TARGETS) if args.host == "all" else [args.host]
    touched = sum(apply_to(h, TARGETS[h], which, args.uninstall, args.dry_run)
                  for h in hosts)

    if not touched:
        sys.stderr.write(
            "\n✗ Nothing to do — neither ~/.claude nor ~/.codex was found.\n"
            "  callusguard also works with no hooks at all:\n"
            "      bin/callusguard scope declare --create 'docs/**/*.md' --run-id job1\n"
            "      <run your agent>\n"
            "      bin/callusguard scope verify --run-id job1\n")
        return 3

    if not args.dry_run and not args.uninstall:
        print("\n·  Start a new session so the hooks load.")
        print("·  Check it took:  bin/callusguard guard doctor")
        print("·  wroteonly stays inert until a declaration exists (.wroteonly.json).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
