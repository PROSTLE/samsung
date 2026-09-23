# Phase 1 report: protocol, adapter, virtual clock, trace, tool data

Status: complete. 48 tests pass on Python 3.10.20, 3.11.4 and 3.12.13.

```
uv run --isolated --python 3.10 --with-editable ".[dev]" pytest   -> 48 passed in 1.38s
uv run --isolated --python 3.11 --with-editable ".[dev]" pytest   -> 48 passed in 1.39s
uv run --isolated --python 3.12 --with-editable ".[dev]" pytest   -> 48 passed in 1.46s
```

## Features added

| Feature | File | What it does |
|---|---|---|
| Provisional protocol | `keel/protocol/provisional.py` | Pydantic models for the 6 input kinds and 5 output kinds named in guide §3.1. Envelopes reject unknown fields; IDs and timestamps are validated. |
| Kit adapter | `keel/protocol/adapter.py` | `Codec` (wire ↔ model) + `KitAdapter` (transport). `QueueAdapter` runs over two asyncio queues, as the guide describes. Outgoing actions are re-validated so a malformed action can never leave Keel. |
| Deterministic IDs | `keel/protocol/ids.py` | Counter-based, session-prefixed IDs so replayed traces are byte-identical. |
| Virtual clock | `keel/kernel/clock.py` | Discrete-event scheduler: timers fire in (deadline, insertion) order; time never goes backwards. `MonotonicClock` for live runs. |
| Trace logger | `keel/trace.py` | JSONL trace: inputs, outputs, kernel notes, session header (schema version, config digest, `synthetic` flag). Optional wall-clock ns next to virtual time. |
| Config | `config/keel.toml`, `keel/config.py` | Typed loader; unknown keys are errors. |
| Tool data | `data/fetch/*.py` → `data/tools/*.jsonl` | 30 τ-bench schemas, 99 τ²-bench tools with the authors' READ/WRITE labels, 162 BFCL stateful-API functions. Pinned to commit SHAs; licenses copied. |
| Kit-assumption guard | `docs/KIT_ASSUMPTIONS.md`, `tests/test_kit_assumptions.py` | 16 provisional choices (K01–K16); the test fails if code and doc drift. |

## Decisions and why

1. **Discrete-event virtual clock, not asyncio time-warping.** Callbacks run
   synchronously in a fixed order, so the fuzzer and scorer get exact
   reproducibility, matching the guide's "deterministic event replay" (§4).
2. **Integer milliseconds for time** (K02). The guide talks in "few ms" and
   "few hundred milliseconds"; integers avoid float drift in replay.
3. **Wire format behind a Codec.** The kit is unreleased; only the codec
   should change when it ships.
4. **Manifest tools keep unknown fields.** MCP defines `readOnlyHint` /
   `destructiveHint` / `idempotentHint` tool annotations; if the kit's
   manifests carry hints like these, the compiler must see them.
5. **τ²-bench as the gold standard for read/write labels.** Its authors tag
   every tool `READ`/`WRITE`/`GENERIC`/`THINK`. Using their labels means the
   compiler's accuracy (phase 3) is measured against someone else's ground
   truth, not ours.
6. **Keel's policy on τ²-bench `GENERIC` tools:** `transfer_to_human_agents`
   (upstream: not state-mutating) is treated as **state-changing** by Keel,
   because a hand-off is an irreversible action in the world and must not run
   speculatively. `calculate` stays read-only. The divergence is recorded
   where gold labels are built (phase 3).
7. **Normalised tool data is committed.** MIT and Apache-2.0 allow
   redistribution with the license text (copied to `data/tools/LICENSES/`).
   Committing it means the compiler works offline during evaluation, where
   network access is unknown.
8. **Parse third-party Python with `ast`, never import it.** No foreign code
   executes during data collection.
9. **`MonotonicClock` reads `perf_counter_ns`.** Measured on the dev machine:
   the asyncio loop clock on Windows has 15.625 ms resolution and fired a 1 ms
   timer early. A regression test pins the fix.
10. **Guide PDF is git-ignored**, because every page carries a registrant watermark.

## Pitch check (done early because it could change the project)

The claim "Keel derives interruption policy from the manifest; existing
frameworks make developers configure it per tool" holds against current docs:
Pipecat uses a developer-set `cancel_on_interruption` per tool; LiveKit tells
developers to call `disallow_interruptions()` inside mutating tools. Neither
derives it, and neither cancels by *which slot changed*. Quotes and URLs are in
`SOURCES.md`.

## How to use it

```
uv venv --python 3.11 .venv && uv pip install --python .venv -e ".[dev]"
.venv/Scripts/python -m pytest                     # run tests
python -m data.fetch.tau_bench                     # re-fetch τ-bench + τ²-bench
python -m data.fetch.bfcl                          # re-fetch BFCL
make test-all                                      # 3.10/3.11/3.12 (Linux/macOS, needs uv)
```

## Guide vs build prompt

- The guide applies a 0.80×–1.20× quality multiplier (naturalness,
  truthfulness, relevance); the build prompt's local scorer omitted it. Phase
  5 will include it.
- "Multimodal" is undefined for the 1.5× multiplier; see KIT_ASSUMPTIONS.md.
