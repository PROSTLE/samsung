# Protocol assumptions, and how the theme update resolved them

Phases 1–3 were built before any evaluation kit existed (original guide v1.0.0,
section 4: "The evaluation kit will be released post the registrations"). Every
guess about its wire format was tagged in code as `ASSUMPTION [Kxx]` and listed
here. `tests/test_kit_assumptions.py` fails if the code and this list drift apart.

**The theme update (Theme05 participant guide, 2026-09-24) replaced the kit.**
Scoring is now Full-Duplex-Bench v3 (FDB-v3), run against a LiveKit voice agent.
So `keel/protocol` is no longer a guess at an external format. It is Keel's
**internal** event vocabulary, and the external boundary is LiveKit:
`keel/livekit/session.py` (`wire`) and `keel/livekit/gate.py` translate LiveKit
session events and LLM tool calls into these events. The table records what
each assumption became. "Internal" means the choice stands as Keel's own design
and no external party depends on it.

FDB-v3 facts below were read from its code at commit
`3e799c45a045256f47d5f1c9cda90157e2d2ec9e` (SOURCES.md).

| ID | Where | Original assumption | Resolution after the theme update |
|---|---|---|---|
| K01 | provisional.py `Id` | IDs are ASCII `[A-Za-z0-9._:-]`, 1–128 chars | Internal. LiveKit room names (the runner uses `eval-<8 hex>`) are made valid by `keel.livekit.agent.session_id`. |
| K02 | provisional.py `TimeMs` | Integer ms of virtual time | Internal. In live runs the clock is `MonotonicClock` (ms since the session started); FDB-v3 measures latency itself from audio and wall-clock timestamps. |
| K03 | provisional.py `_Event` | `event_id`, `session_id`, `ts_ms` envelope | Internal. The gate stamps them. |
| K04 | provisional.py `MediaRef` | Media by `uri` or inline `data_b64` | Internal. Audio arrives as a LiveKit track; Show & Fix reads camera frames from a LiveKit video track. |
| K05 | provisional.py `TextChunk` | End-of-turn is a flag on a text chunk | Internal. LiveKit's committed user turn (`conversation_item_added`, role user) becomes a `TextChunk(end_of_turn=True)`. |
| K06 | provisional.py `AudioClip` | Optional duration, no sample rate | Not used: LiveKit handles audio. |
| K07 | provisional.py `Interrupt` | A bare signal | Internal. LiveKit's `user_state_changed` → speaking becomes an `Interrupt`, which also closes the fence while the user speaks. |
| K08 | provisional.py `ToolResult` | Status `ok` / `error` / `timeout` | Internal. FDB-v3's mock APIs return a dict; a raised exception or `{"status": "error"}` becomes `error` (keel/livekit/fdb.py). |
| K09 | provisional.py `ToolSpec` | `{name, description, parameters}` plus extras | Confirmed in practice. FDB-v3 tools are Python methods; `keel.livekit.template` turns their signatures into exactly this shape. |
| K10 | provisional.py `_Action` | `action_id`, `session_id`, `ts_ms` envelope | Internal. |
| K11 | provisional.py `_Action.caused_by` | Actions may name their cause | Internal (kept for the trace console). FDB-v3 computes latency from audio timestamps. |
| K12 | provisional.py `Speak.purpose` | Fillers tagged acknowledge / progress / status | Internal. FDB-v3 scores speech only through ASR of the recorded audio; Keel's fillers are recorded in the trace (`filler_said`) with their purpose. |
| K13 | provisional.py `StateSnapshot` | One intent plus a flat slot map | Internal. FDB-v3 has no snapshot; it scores tool calls, arguments and the spoken response. |
| K14 | adapter.py `ProvisionalCodec` | JSON wire format | Superseded by LiveKit for the scored path; still used by the simulator and tests. |
| K15 | adapter.py `KitAdapter.warmup` | Async warm-up hook | Resolved: LiveKit's `download-files` command and the process prewarm hook (`keel.livekit.session.prewarm`). |
| K16 | adapter.py `END_OF_STREAM` | `None` ends a scenario | Superseded: a scenario ends when FDB-v3's client leaves the room. |
| K17 | compiler/manifest.py `explicit_hints` | Hints arrive as MCP annotations and similar | Resolved: FDB-v3 tools carry no side-effect hints, so the lexical classifier (or `unknown`, treated as a write) decides; with `fence.hold_reads` every call waits anyway. The Show & Fix manifest uses MCP `readOnlyHint`. |
| K18 | kernel/loop.py `_dispatch` | 10 s Keel-side call timeout | Checked: FDB-v3's slowest mock profile ("timeout_risk") sleeps 3–8 s (latency_injector.py), below the 10 s timeout. |
| K19 | kernel/loop.py `_step_state` | A cancel may be honoured silently; a cancelled in-flight write is "maybe executed" | Resolved: LiveKit never cancels a running tool (voice/generation.py: "waiting for function call to finish before fully cancelling"), so a dispatched call always executes and is logged. Keel therefore never dispatches a call it might need to cancel: held calls are dropped *before* dispatch. |
