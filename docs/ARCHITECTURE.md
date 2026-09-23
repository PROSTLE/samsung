# Keel architecture

Keel sits between a voice agent's perception/reasoning and its tools. Its job
is to keep the agent *correct* while the user interrupts, corrects, and
changes their mind. It is scored on the guide's four categories, and it
runs deterministically with no model at all.

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
