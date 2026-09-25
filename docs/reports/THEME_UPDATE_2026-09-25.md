# Theme update, 2026-09-25: from a hypothetical kit to FDB-v3 on LiveKit

The organisers replaced the Theme 05 brief (`Theme05_Participant_Guide_UPDATED_FBD.docx`).
This report maps every requirement of the new guide to what the codebase now
does, what was verified and how, and what is still open.

## 1. What changed in the brief

| | Original guide (v1.0.0) | Updated guide |
|---|---|---|
| Scored harness | An unreleased "evaluation kit": two queues, a virtual clock, trace-based scoring | **Full-Duplex-Bench v3**, re-run by the organisers with our script |
| Agent framework | Any | **Inside LiveKit Agents** |
| Score | Task completion 40 / interruption recovery 35 / latency 15 / safety 10 | **Benchmark 60 / extension 20 / docs, architecture and video 20** |
| Deliverables | Code | Repo + README (architecture, setup, extension marked), **one-command reproduction**, declared provider, results and logs, 3–5 min video, ≤ 8 slides |
| Rules | Session-scoped memory | Also: pin seeds and versions, no hardcoding or tuning on test items, no own servers at evaluation, no cross-scenario caching |

## 2. What FDB-v3 actually scores (read in its code)

- Tool calls come from the agent's log `/tmp/agent_tool_calls.log`, matched by room.
  **Every executed call counts.**
- The strict pass rate needs exactly the expected calls (none missing, **none
  extra**) with correct arguments (gpt-4o judge). Tool selection F1, argument
  accuracy, response quality and latency are reported alongside.
- The published baselines (paper, Table 2): GPT-Realtime Pass@1 0.600, cascaded
  Whisper → GPT-4o 0.450. On self-correction, the cascaded pipeline scores 0.176,
  because STT finalises the first part before the correction arrives.

This makes Keel's core rule (never execute work planned from a superseded
request) directly score-relevant: in FDB-v3 a stale call is a failed scenario.

## 3. Requirement by requirement

| Guide | Status | Where |
|---|---|---|
| §3.1 LiveKit voice agent, any architecture | **Done.** The FDB-v3 cascaded template's pipeline with Keel under every tool call; realtime option | `keel/livekit/agent.py`, `session.py`, `gate.py` |
| §3.1 Start from the benchmark's templates | **Done.** Tools and instructions are *read from* `cascaded_agent.py` (AST), not copied; both templates give identical definitions (tested) | `keel/livekit/template.py` |
| §3.2 Clone FDB-v3, LiveKit Cloud, download data, run | **Scripted** | `scripts/reproduce_fdb_v3.sh` |
| §3.3 Iterate on self-correction and multi-step chains | **Mechanism in place, unmeasured.** Hold, drop-on-new-speech and idempotency target exactly these; the end-of-utterance model targets pauses | `docs/ARCHITECTURE.md` "LiveKit layer" |
| §3.4 One extension use case, end to end | **Built and tested offline** (Show & Fix: camera → vision → Samsung code table → booking); needs keys and a camera for the live run | `extension/show_and_fix/` |
| §3.5 Demo video | **Needs you** | — |
| §4 README with architecture diagram, setup, extension marked | **Done** | `README.md` |
| §4 One-command reproduction + declared provider | **Done**: OpenAI via LiveKit Cloud | `scripts/reproduce_fdb_v3.sh`, `README.md` |
| §4 Results and run logs from our best run | **Needs keys**; the script collects everything into `results/fdb_v3/` | — |
| §4 ≤ 8 slides | **Needs you** (content is in this report, README, JUDGE_QA) | — |
| §4 API keys documented, not included | **Done** | `.env.example`, `.gitignore` |
| §5 Re-run on a standard machine | **Pinned** lock files resolved for Linux x86-64 / Python 3.10; CUDA wheels via PyPI torch 2.14 | `requirements/*.lock.txt` |
| §6 Pin seeds and versions | **Done**: mock jitter seeded per session, LLM temperature 0 + seed, exact pins | `config/fdb_v3.toml`, locks |
| §6 No hardcoding or tuning on test items | **Held.** Nothing reads benchmark answers; data is used only for format-compatibility tests | `tests/test_livekit_template.py` |
| §6 No own servers | **Held.** Only OpenAI and LiveKit Cloud; the turn detector runs locally | — |
| §6 No cross-scenario caching | **Held.** Everything is per room/session: ledger, mock registry (a fresh one per session, unlike the templates' module-global one), bookings | `keel/livekit/fdb.py` |

## 4. Decisions, with the reason for each

1. **Keep the models identical to FDB-v3's cascaded baseline** (whisper-1, gpt-4o,
   tts-1). Any score difference from the published 0.450 is then Keel's, not a
   model swap. `--pipeline gpt_realtime` is available for a second run.
2. **Hold reads as well as writes** (`fence.hold_reads`, FDB-v3 profile only). The
   scorer counts every executed call. The fence is anchored to the end of the
   user's turn, so it adds no delay once the LLM takes longer than 600 ms to propose.
3. **Drop, don't cancel.** LiveKit never cancels a running tool and a logged call
   cannot be un-logged, so every Keel cancellation happens before dispatch.
4. **Drop only on new *transcribed* speech** (cascaded) or when LiveKit interrupts
   the reply. The recordings contain background noise, so speech that transcribes
   to nothing must not stall the agent (tested).
5. **The LLM speaks; Keel's kernel speech becomes notes.** The trace never shows
   words that were not spoken. Keel's truthful hold and progress lines go through
   LiveKit's own filler mechanism and are logged when they play.
6. **Canonicalise arguments before the idempotency key**: defaults filled, number
   types unified, and numbers or booleans for string parameters converted to
   strings. The last came from the benchmark's own data (housing_03, housing_08).
7. **Two environments** for the reproduction (agent vs FDB-v3's scripts), each from
   an exact lock, so LiveKit's dependency tree cannot conflict with NeMo's.
8. **Local end-of-utterance model**, not LiveKit's new hosted `inference.TurnDetector`,
   so a re-run needs no extra hosted service.

## 5. Bugs found and fixed while building this

| Bug | Found by | Effect if shipped |
|---|---|---|
| **On Linux no job process ever started, so the agent never joined a room.** The prewarm hook was a closure; LiveKit's forkserver pickles it (`Can't pickle local object`) | the end-to-end dry run on Linux: FDB-v3's client streamed and "succeeded" into an empty room | **Every scenario silent: benchmark score 0**, with no error visible in the runner's output. On Windows LiveKit runs jobs in threads (livekit-agents `worker.py`: `JobExecutorType.THREAD` on win32; processes via forkserver on Linux), so nothing was pickled and the local Windows test passed. The hooks are now module-level functions, and a test pickles both agents' hooks |
| Parallel tool calls interleaved lines in the scored tool log | a flaky gate test, then a 20-call stress test | Corrupted or lost tool calls in FDB-v3 scoring |
| Keel traces were buffered until the job ended (~20 s after the session) | the local LiveKit room test | The last scenario's trace lost; any crash loses a trace |
| The VAD model loaded on the session's event loop (blocked ~0.4 s) | LiveKit's watchdog in the local room test | Audio and turn handling delayed at the start of every scenario |
| Status probe could not read `{"status": ..., "items": []}` as empty | extension test | "Did it go through?" answered "can't confirm" for most real APIs |
| Numbers or booleans for `str` parameters were rejected | benchmark-shape test | Extra LLM round trips; possibly a missing call |
| The benchmark lock pulled PyPI's torch 2.14.0, which is built for CUDA 13.0 (`+cu130`) | the clean-Linux run printed the build | On a CUDA 12.x driver, allowed by guide §5, FDB-v3's runner calls `model.cuda()` and the ASR step crashes: benchmark scored 0. Now pinned to `torch==2.14.0+cu126` from PyTorch's CUDA 12.6 index, which runs on 12.x and 13.x drivers |
| The smoke-test example (`travel_09`) has no recording: 79 of the 100 scenarios are recorded, and the runner silently processes nothing | the end-to-end dry run on Linux | A "successful" smoke test that tested nothing. The script now refuses an example without a recording; the default is `travel_10`, a recorded self-correction |
| FDB-v3's ASR model is downloaded from Hugging Face inside the runner; one failed transfer ended the run | the end-to-end dry run on Linux | A flaky connection scores zero. The script now fetches the runner's own `ASR_MODEL_NAME` first, with retries (plain HTTP after the first) |
| The benchmark environment lacked `livekit-api`: FDB-v3's client does `from livekit import api`, which its README gets indirectly through livekit-agents | the end-to-end dry run on Linux (`livekit_inference.py failed with exit code 1`) | Every scenario fails inference. Now pinned in `requirements/bench.lock.txt`, and the script import-checks both environments before any long step |
| One failing evaluation ended the script before the others ran and before anything was collected | the same dry run (FDB-v3's `evaluate_tool_calls.py` crashes formatting an empty result set) | No reports or logs from a partly failed run. Evaluations are now non-fatal, as in FDB-v3's own `run_all_evaluations_released.sh` |
| No pip retry/resume for multi-hundred-MB wheels | the first clean-Linux run died on a network drop | A flaky connection on the organisers' machine fails the whole reproduction |
| Show & Fix's camera frame pump was an unreferenced asyncio task | a linter (ruff RUF006) in the cross-verification pass | asyncio keeps only weak references to tasks: the pump could be garbage-collected mid-stream and the camera silently freeze. Now held and cancelled at shutdown |
| A still photo (`KEEL_SHOW_AND_FIX_IMAGE`) was always sent as `image/jpeg` | the cross-verification pass | A PNG photo mislabelled to the vision API. The MIME type now comes from the file's signature; a non-image is an error, not a guess |
| A camera frame was JPEG-encoded on the event loop | the cross-verification pass | Audio and turn handling paused while encoding; now in a worker thread |
| `main()` read `KEEL_*` settings before loading `.env` | the cross-verification pass | `KEEL_CONFIG`/`KEEL_PIPELINE`/`KEEL_FDB_DIR` in `.env` ignored (the script exports them, so it was unaffected) |
| The template reader would silently drop a keyword-only or `*args` parameter from a tool's schema | the cross-verification pass | The LLM would never be offered that argument; now an error |
| No test exercised `fence.hold_reads` (every fixture tool classified as unknown), and the failed-parallel-call test could not fail | mutation testing in the cross-verification pass | The rule FDB-v3 scoring depends on most was untested. Added tests with a read-only tool; six deliberate mutations of the key rules are now each caught by a test |
| `load_config` could not layer profiles; no config for LiveKit | — | (new capability) |

Plus the earlier audit's six fixes (docs/reports/AUDIT_2026-09-24.md).

## 6. How it was verified (without API keys)

| Check | Result |
|---|---|
| Unit and integration tests (kernel, compiler, gate, extension, console) | all pass; 3,072-run grid: 0 violations, 0 runs without a final response |
| A real LiveKit 1.8.3 `AgentSession` (text mode, scripted LLM) running Keel's tools | 7 tests pass (`tests/test_livekit_session.py`, including a pickling check of both agents' process hooks); the tool-execution tests passed 10/10 repeated runs |
| FDB-v3's real template and mock APIs (pinned commit) | tools, instructions, argument shapes and log format tested |
| Local `livekit-server --dev`, FDB-v3's **own** `livekit_inference.py` streaming a real recording into Keel's agent | agent dispatched and joined; VAD events reached the gate; STT reached (stopped by the fake key); output WAV and `STREAM_START_TIME` produced as the runner expects |
| `agent.py start` against a dummy URL; `download-files` | worker starts and retries the connection; model weights downloaded |
| **Full end-to-end dry run on Linux**: `scripts/reproduce_fdb_v3.sh --example travel_10` against a local `livekit-server --dev` (RTX 3050, WSL Ubuntu), fake OpenAI key | exit code 0. Agent registered; its job process started and joined the room; FDB-v3's runner streamed the recording; Keel's gate saw the manifest (12 tools) and all 3 user speech segments; STT was reached and stopped by the fake key; NeMo ASR ran on the GPU; a `completed` result was written; all three evaluations produced reports (0/1, the agent was silent); results, tool log, trace, console, pip freezes and run info were collected. Found five bugs on the way (§5) |
| Reproduction script on a clean Linux (WSL Ubuntu 24.04, no sudo, NVIDIA GPU), dummy keys | installed both locked environments (livekit-agents 1.8.3; nemo 3.0.0, torch 2.14.0, CUDA visible), cloned FDB-v3 at the pinned commit, downloaded and unpacked the audio (100 recordings), downloaded the model weights, started the agent (13 prewarmed job processes), then stopped cleanly at "agent did not register with LiveKit within 120 s" (the dummy URL). The first attempt failed on a network drop while downloading a 248 MB wheel; pip retries and resume are now set in the script |

## 7. Not verified yet

- **A scored FDB-v3 run.** Needs LiveKit Cloud and OpenAI keys.
- **The GPT-Realtime pipeline end to end** (builds, not run).
- **Show & Fix with a live camera and the real vision model** (tested with a fake reader).
- A CUDA 12.x *driver*: the CUDA 12.6 build (`torch==2.14.0+cu126`) was installed from the new lock on the same Linux machine and works there (`torch.cuda.is_available()` true, CUDA 12.6 runtime, NeMo ASR imports; pip's retries recovered several dropped connections), but that machine's driver is CUDA 13-capable, so a 12.x-only driver was not tested.
