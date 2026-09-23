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

## Honesty

**Q: Are your numbers real?**
Every number in the README comes from a command in the repo and is labelled
*measured* (with the command) or *target*. Synthetic scenarios are marked
`synthetic` in the trace header and in the console.
