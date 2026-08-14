"""callusguard — record what your agent did, derive guards from it, enforce, verify.

A closed loop for agentic coding, in four parts that were previously five repos:

    telemetry   record tool calls (allowlisted on Claude Code; rollout
                files on Codex)
    derive      mine recurring failures into candidate rules
    guard       enforce at the tool boundary, four graded outcomes
    wroteonly   verify the run stayed in scope
    lifecycle   prune the rules that stopped earning their place

Claude Code and the OpenAI Codex CLI are both first-class hosts throughout.

THE DEPENDENCY WALL
    `guard`, `wroteonly` and `core` are stdlib-only and always will be — they run
    synchronously inside every tool call, and "no dependencies, no network, no model
    calls" is what makes that defensible. Every third-party dependency lives behind
    `callusguard.telemetry`, which is an optional extra. A CI job enforces the split.
"""

__version__ = "0.3.0"

#: Bumped from agent-guard's 0.2.0 — the audit records a `version`, and it must keep
#: moving forward across the merge so a chain spanning the change is still ordered.
