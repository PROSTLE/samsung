# Judges' cross-questions: prepared answers

Short answers first, evidence second. Every external claim is sourced in
`SOURCES.md`. Updated at the end of each phase.

## Positioning

**Q: Pipecat and LiveKit already handle interruptions. What is new here?**
They make the *developer* decide, per tool, what an interruption does. Pipecat:
`@tool_options(cancel_on_interruption=...)`, default `True`, so any barge-in
cancels the call. LiveKit: by default an interruption does *not* cancel tool
work, and its docs tell developers to call `disallow_interruptions()` inside
every tool that mutates state. Keel derives the policy from the tool manifest
(read-only vs state-changing, which session slots each argument depends on),
and cancels a call only when a slot it actually read changes. An interruption
that doesn't change anything a call depends on doesn't cancel it.

**Q: Why not just cancel everything when the user interrupts?**
Over-cancelling throws away valid work (latency) and makes the agent look
unstable. The guide scores "prompt cancellation of *invalidated* calls" (§5),
not of all calls.

**Q: How does this map to the Samsung use cases?**
The guide lists four (§2): in-car reroutes (a destination change invalidates
the route call), support bookings (a parameter change mid-booking must not
double-book), field troubleshooting from camera frames (Show & Fix demo), and
accessibility (self-repairs, hesitations).

## Engineering

**Q: How do you know your tests are deterministic?**
The kernel runs on a discrete-event virtual clock: timers fire in (deadline,
insertion) order and time only moves when the driver advances it. Traces
written with wall time disabled are byte-identical across runs; there is a
test for that (`tests/test_trace.py`).

**Q: The kit isn't out. Won't you have to rewrite everything?**
No. All wire-format guesses sit behind a `Codec` in
`keel/protocol/adapter.py`, and each is tagged `TODO(kit): [Kxx]` and listed
in `docs/KIT_ASSUMPTIONS.md`. A test fails if the code and that list disagree.

**Q: Where did your tool data come from? Did you label it yourselves?**
τ-bench and τ²-bench (Sierra Research, MIT) and BFCL (Berkeley, Apache-2.0),
pinned to commit SHAs. The read/write labels are the τ²-bench authors' own
`@is_tool(ToolType.READ/WRITE)` decorators. One deliberate divergence:
`transfer_to_human_agents` is treated as state-changing, because a hand-off
can't be undone.

**Q: Why integer milliseconds?**
The guide's budgets are "few ms" and "few hundred ms"; integers make replay
exact. Real reaction time is also logged in wall-clock nanoseconds.

## Kernel (phase 2)

**Q: How do you decide which calls to cancel?**
Every argument of every call is *bound* to a slot (or a constant, or an
earlier result), and the ledger records the slot version each call read. When
a slot gets a new version, the calls whose inputs no longer resolve to the
same arguments are cancelled; the reason names the versions, e.g.
`appliance v1->v2`. Calls that read other slots keep running. Test:
`test_slot_change_cancels_exactly_the_calls_that_read_it`.

**Q: How fast is cancellation?**
In the same virtual millisecond as the event that changed the slot
(invariant I2, checked on every trace). The path is pure bookkeeping with no
model call. Wall-clock time is recorded in the trace too; phase 5 reports it
as a measured number.

**Q: What stops a double booking when the user says "Friday… no, Saturday"?**
Three layers. (1) The commit fence: writes wait for end-of-turn plus a quiet
period, so most self-repairs land before anything is sent. (2) The
idempotency key: a write with the same (session, tool, args) can't be sent
twice. (3) Per-tool write serialisation: while an older write is in flight or
in doubt, a corrected write waits, and if the older one turns out to have
executed, Keel says so instead of booking again. Evidence:
`python -m eval.grid_self_repair` runs 3072 synthetic timing/fault
combinations and reports 0 double bookings, 0 invariant violations, 0 false
success claims.

**Q: Why not just retry a write that timed out?**
It may have succeeded; a retry could double-book, and the guide scores "zero
duplicate state-changing calls". Keel marks it `unknown`, tells the user it
isn't sure, and checks with a read-only tool. If nothing can check, it says
it cannot confirm. It never claims success without a success result.

**Q: Why wait 600 ms before a write? Isn't that slow?**
The *user* doesn't wait: the fast path speaks within 300 ms. 600 ms is where
Roberts & Francis (JASA 2013) found listeners start judging a silence
negatively, so it is the longest pause we can hide behind an
acknowledgment. It is provisional; phase 4 replaces it with a measurement on
self-repair audio.

**Q: Why run read-only calls speculatively but not writes?**
The same reason HTTP separates safe methods: RFC 9110 §9.2.1 says safe
methods exist "to allow … pre-fetching to work without fear of causing harm".
A wasted read costs a little time; a wrong write costs a booking.

**Q: Is the single-writer rule real or just a convention?**
Enforced. `WriteGuard` raises if a store is touched outside `handle()`, from
another thread, or re-entrantly; there are tests for all three.

**Q: Did your own tests ever catch a real bug?**
Yes. The invariant checker (I3) caught a case where, in one tick, a
corrected write was sent before the old one was cancelled. That's why writes
to one tool are now serialised. See `docs/reports/PHASE_2.md`, decision 4.

## Honesty

**Q: Are your numbers real?**
Every number in the README comes from a command in the repo and is labelled
*measured* (with the command) or *target*. Synthetic scenarios are marked
`synthetic` in the trace header and in the console.
