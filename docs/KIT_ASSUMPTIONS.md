# Evaluation-kit assumptions

The Theme 05 evaluation kit has not been released (guide section 4: "The
evaluation kit will be released post the registrations"). Everything below is
a provisional choice made so we can build now. Each ID is tagged in the code
as `TODO(kit): [Kxx]`; `tests/test_kit_assumptions.py` fails if the code and
this list drift apart.

When the kit ships, go through this list top to bottom. Most items should only
change `keel/protocol/provisional.py` and a new `Codec` in
`keel/protocol/adapter.py`.

| ID | Where | Our assumption | What the real kit must confirm |
|---|---|---|---|
| K01 | provisional.py `Id` | IDs are ASCII `[A-Za-z0-9._:-]`, 1–128 chars, starting alphanumeric | The identifier format the scorer treats as "valid" |
| K02 | provisional.py `TimeMs` | Timestamps are integer ms of virtual time from scenario start | Units (ms vs s), int vs float, and the epoch |
| K03 | provisional.py `_Event` | Every event has `event_id`, `session_id`, `ts_ms` | The actual envelope field names |
| K04 | provisional.py `MediaRef` | Media arrives as a `uri` (path) or inline `data_b64` | How WAV/PNG payloads are delivered |
| K05 | provisional.py `TextChunk` | End-of-turn is a boolean on the text chunk | Whether end-of-turn is a flag or a separate event |
| K06 | provisional.py `AudioClip` | `duration_ms` optional; no sample-rate field | Which audio metadata is supplied |
| K07 | provisional.py `Interrupt` | An interrupt is a bare signal | Whether it carries text or a reference to the interrupting input |
| K08 | provisional.py `ToolResult` | Status is one of `ok`, `error`, `timeout` | The mock environment's result/fault vocabulary |
| K09 | provisional.py `ToolSpec` | Tools are `{name, description, parameters}` (JSON Schema) plus arbitrary extras | Manifest tool shape, and whether it carries read-only / side-effect hints |
| K10 | provisional.py `_Action` | Every action has `action_id`, `session_id`, `ts_ms` | The actual envelope field names, and whether the agent or harness stamps time |
| K11 | provisional.py `_Action.caused_by` | Actions may name the event that caused them | Whether latency scoring needs this link or uses timestamps only |
| K12 | provisional.py `Speak.purpose` | Fillers are tagged `acknowledge` / `progress` / `status` | Whether the kit distinguishes filler kinds; how "excessive fillers" is judged |
| K13 | provisional.py `StateSnapshot` | One `intent` string plus a flat `slots` map | Snapshot shape (nesting, multiple intents, slot typing) |
| K14 | adapter.py `ProvisionalCodec` | Wire messages are JSON matching provisional.py exactly | The wire format |
| K15 | adapter.py `KitAdapter.warmup` | Warm-up is an async method called once before the first scenario | How the 300 s setup hook is invoked |
| K16 | adapter.py `END_OF_STREAM` | `None` on the input queue ends a scenario | How end of scenario is signalled |

## Open questions from reading the guide

These are not tagged in code yet because no code depends on them.

- **Quality multiplier.** Guide section 5 scales each scenario score by
  0.80×–1.20× for "transcript naturalness, truthfulness, and relevance". The
  build prompt's local scorer omits it. How it is computed (LLM judge? rubric?)
  is unknown.
- **"Multimodal" scenarios.** The 1.5× hidden-set multiplier applies to
  "multimodal scenarios". We assume audio and visual both count, which gives
  audio ≈36% and visual ≈24% of the weighted score (50 + 30×1.5 + 20×1.5 = 125).
  If only visual counts, both audio and visual are ≈27% (30 of 50 + 30 + 20×1.5 = 110).
- **Public suite mix.** Nine scenarios cannot be split exactly 50/30/20.
- **Over-cancellation.** Whether cancelling a call that did *not* depend on a
  changed slot is penalised.
- **Hosted model APIs.** Whether network access to hosted models exists during
  evaluation.
