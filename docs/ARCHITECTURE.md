# Keel architecture

Keel sits between a voice agent's perception/reasoning and its tools. Its job
is to keep the agent *correct* while the user interrupts, corrects, and
changes their mind. The kernel runs deterministically with no model at all.
Since the theme update it runs under a LiveKit voice agent and is scored by
Full-Duplex-Bench v3 (see "LiveKit layer" below).

## Event flow

```
 harness input queue                                        harness output queue
 (text, audio, frames,                                      (speak, tool_call, cancel,
  interrupts, results,                                       clarify, final + snapshot)
  manifests)                                                          ▲
        │                                                             │
        ▼                                                             │
  ┌───────────┐   events   ┌──────────────────────────────────────┐   │ validated
  │KitAdapter │──────────► │  Kernel.handle()   — single writer   │───┘ actions
  │ (Codec)   │            │                                      │
  └───────────┘            │  slots ─ ledger ─ fence ─ goal       │
                           │        converge() after every event  │
        workers            └──────────────────────────────────────┘
  ┌──────────────────┐        ▲ Interpretation   ▲ TimerFired
  │ ASR / OCR / LLM  │────────┘ (posted, never   │ (fence, timeouts,
  │ (slow path)      │          touch state)     │  ack, progress)
  └──────────────────┘                           │
                                          Clock (virtual or monotonic)
```

* **Single writer.** Only `Kernel.handle()` mutates state. `WriteGuard`
  raises if a store is touched outside it, from another thread, or re-entrantly.
* **Everything is an event.** Tool results, worker outputs and timers are all
  posted and handled one at a time, in order.

## State

| Store | Holds | Key rule |
|---|---|---|
| `SlotStore` | value, version, source, confidence, `updated_at`, lock, full history | a version bumps only when the value changes; an explicit correction locks the slot against later perception |
| `Ledger` | one entry per call: args, slot versions read, safety class, status, idempotency key | checked status machine; one live write per key |
| `CommitFence` | turn state, last change per slot | when may a write go |
| `Goal` | intent + steps; each argument bound to a slot, a constant, or a prior step's result | binding (not copying) is what makes invalidation exact |

## Converge (after every event)

1. Resolve each goal step against current slots and results → (arguments,
   slot versions read, idempotency key).
2. **Cancel** every active call whose key is no longer needed. The reason is
   the slot versions it read that changed, e.g. `appliance v1->v2`.
3. **Reuse** calls whose key is still needed (in flight or already
   succeeded). No duplicate work, no stale re-runs.
4. **Create** missing calls. Read-only → dispatched at once (speculative).
   State-changing → held until the fence opens.
5. Every step succeeded → one **final response** with the full snapshot.
   A step that failed, or whose write already went through with details the
   user has since changed, also ends the goal with one truthful final
   response, so no session ends without one.

A required tool argument that the goal left unbound reads the session slot
of the same name (the compiler's slot map), or Keel asks for it.

Cancellation happens in the same virtual millisecond as the change that
caused it, and no model call sits on that path.

## Write safety

* Exactly-once: a write can't be created while another entry with its key is
  pending, in flight, succeeded, unknown, or cancelled-but-maybe-executed.
* Writes to one tool are serialised: a new write waits while another write to
  that tool is in flight or in doubt, so an old and a corrected write can
  never both land.
* A write that succeeded for a step is *committed*. If the user then changes
  what it read, Keel says so and does not re-send. Changing it needs a new plan
  (e.g. a modify tool).
* In doubt (timeout, or cancelled after dispatch): Keel never claims
  success and never re-sends blindly. It probes with a read-only tool if the
  compiler found one; otherwise it says it cannot confirm.

## Invariants checked on every trace (`keel/sim/invariants.py`)

I1 no stale dispatch · I2 cancellation in the same ms as invalidation ·
I3 no duplicate state-changing calls · I4 no effect twice in the world ·
I5 final snapshot equals the slot store.

## Trace console (`keel/console/`)

`python -m keel.console trace.jsonl` renders one self-contained HTML page per
set of traces. The view model is computed in Python from trace records only
(tested in `tests/test_console.py`); the page draws it. `python -m
eval.showcase` produces five labelled synthetic sessions and their page.

## LiveKit layer (`keel/livekit/`), added with the theme update

The scored harness is now FDB-v3 driving a LiveKit voice agent. The LLM decides
*what* to call; Keel decides *whether and when* it runs.

```
LiveKit AgentSession                         KeelGate (gate.py)                     Kernel
───────────────────                          ──────────────────                     ──────
user_state_changed: speaking  ────────────►  Interrupt            ───────────────►  fence closed (user speaking)
user_state_changed: stopped   ────────────►  TextChunk("", eot=False) ───────────►  fence: speaking ended; its words pending (rule 5)
user_input_transcribed final  ────────────►  TextChunk(words, eot=False) ────────►  fence: those words arrived (rule 5)
                                             slot `utterance` v+1 ───────────────►  held calls planned earlier: dropped
conversation_item_added user  ────────────►  TextChunk(eot=True)  ───────────────►  turn closed; fence opens after quiet_ms
LLM tool call (raw-schema tool) ──────────►  Step(Const args, basis=utterance) ──►  ledger entry: held → dispatched → result
                              ◄────────────  result JSON | "superseded" | "unknown"
```

A tool call's life:

1. **Completed and validated** (`fdb.py` `arguments`, compiler schema): signature
   defaults are filled in and scalars take the schema's type, *before* the
   idempotency key is computed. So `add_to_cart(p)` and `add_to_cart(p, quantity=1)`
   are one call. Invalid arguments go back to the LLM and never run.
2. **Held** behind the commit fence. With `fence.hold_reads` (the FDB-v3
   profile) this applies to reads too, because the benchmark scores every
   executed call. The fence counts from the end of the user's turn, so it only
   delays a call the LLM proposes sooner than `quiet_ms` after the user stopped.
   In a cascade it also waits for the words of every stretch of speech that has
   ended (rule 5, `fence.transcript_wait_ms`): LiveKit can commit a turn from the
   first stretches while the last, already spoken, is still being transcribed.
3. **Dropped** if new transcribed speech arrives first, or if LiveKit
   interrupts the reply the call belongs to. The kernel cancels it with a
   reason (`utterance v1->v2`), and the LLM is told it was superseded. It never
   executed, so it never reaches the benchmark's tool log.
4. **Dispatched** to the backend, which runs FDB-v3's mock API in a worker thread
   and appends one whole line to the tool log under a lock.
5. **Reused**: an identical call later in the same session gets the first result.

Why "never dispatch what might need cancelling": LiveKit does not cancel a
running tool (voice/generation.py waits for it), and a logged call cannot be
un-logged. So all of Keel's cancellation happens *before* dispatch.

Speech: in LiveKit mode the LLM speaks (`floor.speak = false`). The kernel's
own lines are recorded as `unspoken_*` notes, so the trace never shows speech
that did not happen. Keel says only three kinds of truthful line, through
LiveKit's `RunContext.with_filler`: the hold phrase while a call waits, the tool's
acknowledgment once it has gone out, and a progress line for a slow call (at
most three per call). Each is recorded as `filler_said`.

The Show & Fix extension (`extension/show_and_fix/`) uses the same gate and
session code with its own manifest, where reads may run speculatively
(`hold_reads = false`) because nothing scores a wasted read there.

## Model providers and the open pipeline (`keel/providers.py`, `keel/speech/`)

Every model call goes through an OpenAI-compatible client; only its endpoint and
key differ. `config/keel.toml [providers]` names the endpoints (openai, gemini,
groq, ollama, speaches) with the environment variable that holds each key, and
whether it accepts OpenAI's `seed` (Gemini's layer rejects it). `KEEL_PIPELINE`
selects the pipeline for either agent; `pipeline_providers()` says which providers
it will call, which the preflight and the Setup page use.

```
pipeline open:  Silero VAD + end-of-utterance model   (as in cascaded, [livekit.cascaded])
                STT  -> [livekit.open].stt_provider   (speaches: keel.speech, faster-whisper)
                LLM  -> [livekit.open].llm_provider   (ollama: Qwen3-4B-Instruct)
                TTS  -> [livekit.open].tts_provider   (speaches: keel.speech, Kokoro)
Show & Fix:     display reader -> [livekit.show_and_fix.vision].<pipeline>, else OpenAI's vision_model
```

`scripts/open_models.sh` reads those sections, installs what the local providers
need (pinned), starts them on 127.0.0.1 and waits until they answer. A stage moved
to a hosted provider starts nothing locally. The speech server answers only for
the model it loaded, so a config naming another model fails loudly.

## Web app (`keel/web/`)

```
traces (each profile's livekit.trace_dir, results/fdb_v3/*/traces)
   -> story.py: trace records -> events (said, voice activity, agent state, each
      call's state history, Keel's own lines)
FDB-v3 checkout (fdb_v3_data_released/*/result_*.json, input_mono.wav, output_*.wav)
   -> results.py: recordings per room, the offset that lines them up, verdicts
results/fdb_v3/<run>/ (FDB-v3's reports, run_info.txt) -> results.py: scores
config/*.toml + environment -> system.py: profiles, providers, keys (set or not), tools
   -> server.py: /api/* (JSON), recordings, static page
   -> static/: one console (console.js) for live (live.js, LiveKit) and replay (player.js)
```

Live: the agent attaches `keel/web/live.py` to its trace, which sends each new
story event on the `keel.story` text-stream topic to the web app's participants
only. Replay: the same events come from the trace file, and the page derives the
console's state at any time `t` from them (an action's state at `t` is its last
history entry at or before `t`), so seeking backwards is exact.
