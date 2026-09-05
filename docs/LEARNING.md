# Learning from shared execution history

CallusGuard 0.6 adds an **offline learning/control plane** beside the existing guard runtime.
It does not put a model in the enforcement path and it does not let a model activate its own
changes.

```text
Claude       Codex       Gemini / future hosts
   \           |            /
    \          |           /
       execution telemetry
              |
       deterministic mining
              |
        EvidencePacket
              |
      persistent knowledge
   patterns + interventions
              |
      strong reasoning model
              |
            distill
   rules / skills / workflows /
   tool fixes / orchestration /
            no action
              |
       validation + review
              |
      working agent scaffold
              |
      more execution history
```

## Why the extra layer exists

`callus derive` is intentionally narrow: it finds recurring failure clusters and emits
monitor-only rule candidates. That remains useful and deterministic.

But not every recurring problem wants a guard. A repeated failure may be better solved by a
skill, a workflow change, a tool fix, an orchestration change, or no intervention at all.
The learning layer preserves that distinction:

1. **raw execution** says what happened;
2. **patterns** say what has been learned across executions;
3. **interventions** record what was tried and what happened next;
4. **proposals** are reversible artifacts produced from bounded evidence.

The pattern survives if an intervention is rejected or retired. This prevents the system from
forgetting that a previously plausible idea made things worse.

## Strong-model distillation

A powerful model can be used as an analyst over bounded `EvidencePacket` JSON rather than as
the executor of every task. `callus learn distill` accepts an explicit provider command:

```bash
callus learn distill packet.json \
  --provider-command './scripts/my-model-wrapper' \
  --out proposal.json
```

The provider receives JSON on stdin with `system` and `input` keys and must return one JSON
proposal. The intervention type is one of:

- `rule`
- `skill`
- `workflow`
- `tool_fix`
- `orchestration_change`
- `no_action`

The model is told to prefer the least restrictive intervention supported by evidence and not
to claim causal proof from observational telemetry.

This provider boundary is deliberately vendor-neutral. A wrapper can call OpenAI, Anthropic,
Google, OpenRouter, a local model, or another service without coupling CallusGuard's runtime
to that SDK.

## Portable skills

A learned skill is emitted as two files:

```text
skill-name/
  SKILL.md      # the procedural artifact a working agent may receive
  PURPOSE.md    # provenance: why it exists, expected effect, validation plan
```

The working agent should get the distilled skill, not the historical telemetry and rejected
interventions used to produce it. The larger knowledge base belongs to the learning process.

## Source normalization

Claude and Codex telemetry remain free to use their existing schemas. Learning code consumes
canonical `ToolAttempt`, `EvidenceRef`, and `EvidencePacket` objects through the normalization
boundary in `callusguard.learn.sources`.

Gemini should be added as another ingestion adapter rather than forcing all recorders into one
database schema. Future hosts follow the same pattern.

## Knowledge storage

The first implementation intentionally uses JSONL under:

```text
~/.callusguard/knowledge/
  patterns.jsonl
  interventions.jsonl
```

Set `CALLUS_HOME` or pass `callus learn --home ...` to move it. JSONL keeps the knowledge
portable, inspectable, append-friendly, and independent of the telemetry databases. If scale
later warrants a database or indexed retrieval layer, that can change behind `KnowledgeStore`
without changing the artifact model.

## Safety invariant

**A model proposal is not a deployed change.**

The strong-model step only produces reviewable artifacts. Existing guard tier ceilings,
historical replay, held-out traces, tests, human review, and lifecycle evidence remain the
places where an intervention earns the right to act.

The synchronous enforcement path remains dependency-free, network-free, and model-free.
