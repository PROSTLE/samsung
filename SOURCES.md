# Sources

Every external fact, dataset and license Keel relies on, with where it was
checked and when. Sections between `<!-- source:… -->` markers are rewritten
by the matching `data/fetch/` script; everything else is maintained by hand.

## Brief

### Theme 05 guide
- `docs/Theme_5_Guide.pdf` (v1.0.0, 3 pages), supplied by the organisers. Read in full 2026-09-23.
- The PDF is watermarked with a registrant's identity on every page, so it is
  git-ignored and must not be published with the repo.

### Updated participant guide (the theme update)
- `Theme05_Participant_Guide_UPDATED_FBD.docx`, supplied by the organisers
  (document metadata: created 2026-09-24). Read in full 2026-09-25. Kept
  local and git-ignored, like the original PDF.
- What it changes: scoring is a re-run of **Full-Duplex-Bench v3** (60%), a
  use-case extension (20%) and documentation/architecture/video (20%). Agents
  "run inside the LiveKit agents framework". Submissions need a one-command
  reproduction script, a declared model provider, pinned seeds and versions,
  and must not hardcode, memorize or fine-tune on benchmark items, call your
  own servers at evaluation time, or cache across scenarios. The original
  guide's kit, wire protocol and trace-based scorer no longer apply.

## Scored benchmark: Full-Duplex-Bench v3

### Code and data
- https://github.com/DanielLin94144/Full-Duplex-Bench, directory `v3/`, at
  `3e799c45a045256f47d5f1c9cda90157e2d2ec9e` (main, 2026-05-20; cloned 2026-09-25).
- License: Creative Commons Attribution-NonCommercial 4.0 (repository `LICENSE`;
  the GitHub API reports NOASSERTION). Keel copies none of its code or data. It
  reads the agent template at run time and calls its mock APIs from a checkout.
- Audio: Google Drive file `1SO_4MTazWQ_jvCx0dtmpQ-t40bdd07yz` (link in
  `v3/README.md`, "Data"), downloaded 2026-09-25: 736 MB zip, 100 `input.wav`
  (16 or 48 kHz, mono or stereo, 39–118 s). The recordings cover 79 of the 100
  scenarios in `benchmark_data_v2.json` (folders are `<scenario>_<speaker>`);
  `travel_09`, for example, has none. Only these format facts were taken from
  the audio. Nothing was tuned on it.
- Facts Keel depends on, each read in the code at that commit:
  - The runner scores tool calls from the agent's log `/tmp/agent_tool_calls.log`,
    lines `{"room", "call": {"function", "args", "timestamp_start", "timestamp_end"}}`,
    matched by room name (`run_tool_benchmark.py`, step 6). The latency
    breakdown comes from `LATENCY_TRACK_JSON:` lines in `/tmp/agent_heartbeat.log` (step 4.6).
  - The strict pass rate fails a scenario on any missing or **unexpected** tool
    or any wrong argument (`evaluate_pass_rate.py`, `evaluate_scenario_pass`).
    Arguments are judged by gpt-4o with `--use-llm`.
  - The recording window equals the input WAV's duration (`livekit_inference.py`).
  - Mock APIs are deterministic apart from latency. `latency_injector.py` applies
    per-API defaults to `search_flights`, `search_apartments`, `calculate_commute`
    and `update_search_filter` even under the "instant" profile, with unseeded jitter.
  - The templates call the mock inline in an async tool (`time.sleep` blocks the loop).
  - Both templates define the same 12 tools and the same instructions
    (checked by `tests/test_livekit_template.py`).
  - Expected arguments include values whose type differs from the template
    signature (`update_search_filter(value=1800)` and `(value=True)` against
    `value: str`; scenarios housing_03 and housing_08).
  - PyPI's `torch==2.14.0` wheel is built for CUDA 13.0 (`torch.__version__` printed
    `2.14.0+cu130` after installing the lock on Linux, 2026-09-25). PyTorch
    publishes `torch-2.14.0+cu126` for cp310 at https://download.pytorch.org/whl/cu126
    (no cu128/cu129 build of 2.14.0 there, checked 2026-09-25). `requirements/bench.lock.txt`
    pins that build so a CUDA 12.x driver (guide §5) can run FDB-v3's ASR.
  - The README installs `livekit-agents[...]~=1.3`, which resolves to 1.8.3 as of
    2026-09-25 (PyPI), so the benchmark's own setup is not pinned. Keel pins exact
    versions (`requirements/*.lock.txt`).

### Paper
- G.-T. Lin, C. Chen, Z. Chen, H.-y. Lee, "Full-Duplex-Bench-v3: Benchmarking Tool
  Use for Full-Duplex Voice Agents Under Real-World Disfluency", arXiv:2604.04847,
  submitted 2026-04-06 (abstract page read 2026-09-25).
- Table 2 (Pass@1): GPT-Realtime 0.600, Gemini Live 3.1 0.540, Gemini Live 2.5 0.490,
  Cascaded (Whisper, GPT-4o, OpenAI TTS) 0.450, Grok 0.430, Ultravox 0.410. On
  self-correction scenarios, Pass@1 for the cascaded pipeline is 0.176 and for
  GPT-Realtime 0.588.
- Quote on premature calls: "Gemini Live 3.1's tool-call latency of −2.27 s means
  the API was invoked before the user finished correcting, locking in
  destination='Rome' (the original, uncorrected value)."
- On the cascaded baseline's self-correction failure (same section): "Whisper
  finalizes the initial transcription before the correction arrives, so the
  downstream LLM never receives the updated intent."
- All figures and quotes above were checked verbatim in the paper's HTML
  (https://arxiv.org/html/2604.04847, Tables 2 and 3 and the case studies), 2026-09-25.

## LiveKit Agents 1.8.3 (the agent framework the guide requires)
- `livekit-agents==1.8.3` (Apache-2.0, https://github.com/livekit/agents), with
  `livekit` 1.1.18 (Apache-2.0). Facts read in the installed source, 2026-09-25:
  - A pipeline reply runs tools only after the turn is authorised
    (`voice/agent_activity.py`, `_pipeline_reply_task_impl`), so preemptive
    generation (enabled by default, `voice/turn.py`) does not execute tools early.
  - Running tools are not cancelled on interruption: "waiting for function call to
    finish before fully cancelling" (`voice/generation.py`, `perform_tool_executions`).
  - Raw-schema tools are sent to OpenAI unchanged (`llm/_provider_format/openai.py`, `to_fnc_ctx`).
  - `RunContext.with_filler` schedules filler speech while a tool runs (`voice/events.py`).
  - Jobs run in separate processes, started with multiprocessing's `forkserver` on
    Linux and `spawn` elsewhere, except on Windows, where the default executor is
    threads (`worker.py`: `JobExecutorType.THREAD` on win32). Process start
    pickles the setup function, so it must be module-level.
  - `livekit.plugins.turn_detector` is deprecated in favour of
    `livekit.agents.inference.TurnDetector`, a hosted service with a local
    fallback. Keel keeps the local plugin model so a re-run needs no extra
    hosted service.
- The hosted Agents Playground is being replaced by the Agent Console in the
  LiveKit Cloud dashboard (https://docs.livekit.io/agents/start/playground/,
  via search 2026-09-25). Either can publish a microphone and a camera for the
  Show & Fix demo.
- Local test only: `livekit-server` v1.13.7 in `--dev` mode
  (https://github.com/livekit/livekit/releases, Apache-2.0) was used to run
  FDB-v3's own `livekit_inference.py` against Keel's agent on this machine.

## Show & Fix extension data
- Samsung Singapore, "About the Information Codes On a Samsung washing machine",
  https://www.samsung.com/sg/support/home-appliances/check-out-the-information-codes-on-my-washing-machine/
  (fetched 2026-09-25). `extension/show_and_fix/washer_codes.json` is the page's
  information-code table (7 rows), extracted verbatim from the static HTML. Other
  codes that a summarising fetch reported, but that are not in that table, are
  deliberately left out.

## Prior art (checked for the "automatic interruption policy" claim)

### Pipecat — per-tool `@tool_options`
- https://docs.pipecat.ai/guides/learn/function-calling (fetched as `.md`, 2026-09-23)
- Quote: "`cancel_on_interruption` (default `True`): When `True`, the call is
  cancelled if the user interrupts. When `False`, the call is treated as
  **asynchronous**…" Also `cancellable_by_llm` (default `False`) and
  `timeout_secs`, all set by the developer via `@tool_options(...)` or
  `register_function(...)`.
- Finding: the policy is developer-configured per tool, and cancellation is
  triggered by *any* interruption, not by which slot changed.

### LiveKit Agents — `disallow_interruptions()`
- https://docs.livekit.io/agents/logic/tools/definition/ (fetched 2026-09-23 as
  `definition.md`; that URL returns 404 as of 2026-09-24, and the quotes below
  were re-checked on the page at the new URL that day)
- Quote: "By default, tools can be interrupted if the user speaks. A tool
  continues running in the background until it returns; interrupting the agent
  doesn't cancel the work." and "Call `context.disallow_interruptions()`
  (Python) or `ctx.disallowInterruptions()` (Node.js) at the start of any tool
  that mutates external state." There is also a `CANCELLABLE` tool flag that
  lets the LLM cancel a running tool.
- Finding: LiveKit's docs name the read-only vs mutating distinction but leave
  it to the developer to act on inside each tool.

Re-verified 2026-09-24: the Pipecat defaults (`cancel_on_interruption` True,
`cancellable_by_llm` False, `timeout_secs` None) still read as quoted.

## Standards

### Model Context Protocol tool annotations
- https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/schema/2025-06-18/schema.ts (fetched 2026-09-23)
- `ToolAnnotations` has `readOnlyHint` (default false), `destructiveHint`
  (default true, meaningful only when not read-only), `idempotentHint`
  (default false), `openWorldHint`.
- https://modelcontextprotocol.io/specification/2025-06-18/server/tools says
  clients must consider tool annotations untrusted unless they come from
  trusted servers.
- Use in Keel: the manifest compiler's first step honours these hints when
  present. When a manifest carries `annotations` but omits `readOnlyHint`,
  Keel applies the MCP default (false, i.e. state-changing) and does not let
  the classifier override it (fixed 2026-09-24; before that the classifier
  could call such a tool read-only). A tool with no annotations at all goes
  to the classifier, then to `unknown`, which is treated as state-changing.
- Re-verified 2026-09-24 from
  https://raw.githubusercontent.com/modelcontextprotocol/modelcontextprotocol/main/schema/2025-06-18/schema.ts:
  "If true, the tool does not modify its environment. Default: false", and
  "Clients should never make tool use decisions based on ToolAnnotations
  received from untrusted servers."

## Conversation timing research (basis for config/keel.toml defaults)

### Roberts & Francis (2013): tolerance for silent gaps
- F. Roberts, A. L. Francis, "Identifying a temporal threshold of tolerance for
  silent gaps after requests", *J. Acoust. Soc. Am.* 133(6), EL471–EL477, 2013.
  doi:10.1121/1.4802900 (metadata verified via Crossref API, 2026-09-23;
  abstract read from https://web.ics.purdue.edu/~froberts/Threshold%202013%20JASA%20Roberts%20&%20Francis.pdf).
- Abstract: 380 participants rated responses after gaps of 200–1200 ms in
  100 ms steps; "There was a notable drop-off in ratings at 600 ms and a
  statistically significant difference in ratings between 700 and 800 ms."
- Used for: `fence.quiet_ms = 600` (provisional) and `floor.ack_after_ms = 300`
  (keeps the first spoken action well inside 600 ms).

### Jefferson: the ~1 s "standard maximum" silence
- G. Jefferson, "Notes on a possible metric which provides for a 'standard
  maximum' silence of approximately one second in conversation", in D. Roger &
  P. Bull (eds.), *Conversation: An Interdisciplinary Perspective*, Clevedon:
  Multilingual Matters. The Jefferson archive dates it 1988
  (https://liso-archives.liso.ucsb.edu/Jefferson/, checked 2026-09-23); it is
  often cited as 1989, the volume's publication year. We cite it as 1988.
- Used for: `fence.stale_turn_ms = 1000` and `floor.progress_after_ms = 1000`.

### Stivers et al. (2009): turn-taking gaps are short and universal
- T. Stivers et al., "Universals and cultural variation in turn-taking in
  conversation", *PNAS* 106(26):10587–10592, 2009. doi:10.1073/pnas.0903616106.
  Abstract via NCBI E-utilities (PMID 19553212), 2026-09-23.
- Abstract: all 10 languages show "a general avoidance of overlapping talk and a
  minimization of silence between conversational turns", with language
  averages "within a range of 250 ms from the cross-language mean".
- Used for: the argument that a correction arriving as a *new* turn comes
  quickly after the previous one, which the fence's quiet period covers.

### Blackmer & Mitton (1991): self-repairs are fast
- E. R. Blackmer, J. L. Mitton, "Theories of monitoring and the timing of
  repairs in spontaneous speech", *Cognition* 39(3):173–194, 1991.
  doi:10.1016/0010-0277(91)90052-6. Abstract via NCBI E-utilities (PMID
  1841032), 2026-09-23.
- Abstract: 1525 repairs from 61 radio call-in speakers; "Many of the
  cut-off-to-repair times observed were faster than would be predicted by any
  model in the literature."
- Used for: why most self-repairs land *inside* one turn, which the fence
  handles by waiting for end-of-turn. The exact interval distribution is not
  in the abstract (UNVERIFIED beyond it); phase 4 measures our own.

### RFC 9110 §9.2.1: safe methods
- https://www.rfc-editor.org/rfc/rfc9110.txt (fetched 2026-09-23).
- "Of the request methods defined by this specification, the GET, HEAD,
  OPTIONS, and TRACE methods are defined to be safe." and "The purpose of
  distinguishing between safe and unsafe methods is to allow automated
  retrieval processes (spiders) and cache performance optimization
  (pre-fetching) to work without fear of causing harm."
- Used for: Keel's rule that only read-only calls run speculatively, and the
  HTTP-method hint in the manifest compiler.

## Upstream code facts

### τ²-bench `ToolType` / `is_tool`
- https://github.com/sierra-research/tau2-bench/blob/main/src/tau2/environment/toolkit.py (fetched 2026-09-23)
- `ToolType` members READ, WRITE, THINK, GENERIC. `is_tool(tool_type=ToolType.READ, mutates_state=None)`;
  when `mutates_state` is None it is inferred as True for WRITE and False otherwise.

## Re-verification log

2026-09-24, every external claim above re-checked against the source:
MCP schema defaults (quoted above); Pipecat and LiveKit quotes (LiveKit URL
moved, updated); Roberts & Francis metadata and abstract via
https://api.crossref.org/works/10.1121/1.4802900; Jefferson (1988) entry on the
UCSB archive; Stivers et al. and Blackmer & Mitton via NCBI E-utilities
(PMIDs 19553212, 1841032: titles, volumes, pages, DOIs match); RFC 9110 §9.2.1
sentences via https://www.rfc-editor.org/rfc/rfc9110.txt (lines 3834-3839);
τ²-bench `ToolType`/`is_tool` via the raw file on `main`; the three pinned
commit SHAs resolve (GitHub API 200) and the repo licences are still MIT, MIT,
Apache-2.0. No claim needed correcting apart from the LiveKit URL and the MCP
default behaviour noted above.

## Python dependencies

| Package | Use | License | Checked |
|---|---|---|---|
| pydantic | protocol models, validation | MIT | https://github.com/pydantic/pydantic (GitHub API, 2026-09-23) |
| tomli | TOML on Python 3.10 only | MIT | https://github.com/hukkin/tomli (GitHub API, 2026-09-23) |
| jsonschema | argument validation (Draft 2020-12) | MIT | https://github.com/python-jsonschema/jsonschema (GitHub API, 2026-09-23); 4.26.0 installed |
| pytest | tests (dev only) | MIT | https://github.com/pytest-dev/pytest (GitHub API, 2026-09-23) |
| hypothesis | property tests (dev only) | MPL-2.0 | GitHub API reports NOASSERTION; https://github.com/HypothesisWorks/hypothesis/blob/master/LICENSE.txt says MPL 2.0, and package metadata `License-Expression: MPL-2.0` (6.168.0), 2026-09-23 |

| livekit-agents (+ openai, silero, turn-detector plugins) | the voice agent | Apache-2.0 | https://github.com/livekit/agents (GitHub API, 2026-09-25); 1.8.3 pinned |
| livekit (rtc) | LiveKit client used by the agent and FDB-v3's client | Apache-2.0 | https://github.com/livekit/python-sdks (GitHub API, 2026-09-25) |
| openai | OpenAI API client (agent vision reader; FDB-v3 judge) | Apache-2.0 | https://github.com/openai/openai-python (GitHub API, 2026-09-25) |
| nemo_toolkit[asr] | FDB-v3's ASR (benchmark environment only) | Apache-2.0 | https://github.com/NVIDIA/NeMo LICENSE (2026-09-25) |
| gdown | downloads FDB-v3's audio | MIT | https://github.com/wkentaro/gdown (GitHub API, 2026-09-25) |

MPL-2.0 is file-level copyleft. Hypothesis is a dev-only test dependency that
Keel neither modifies nor redistributes, so it places no obligations on Keel's
own code.

## Datasets

<!-- source:tau_bench -->
### τ-bench and τ²-bench tool schemas (Sierra Research)

- Repos: https://github.com/sierra-research/tau-bench @ `59a200c6d575d595120f1cb70fea53cef0632f6b`, https://github.com/sierra-research/tau2-bench @ `b7ea9074c1cba482b30687fecdb5c8425fd6f619`
- License: MIT (tau-bench), MIT (tau2-bench), per GitHub API; copies in `data/tools/LICENSES/`
- Retrieved: 2026-09-23 by `python -m data.fetch.tau_bench`
- Output: `data/tools/tau_bench.jsonl` (30 tools with JSON schemas),
  `data/tools/tau2_bench.jsonl` (99 tools with author labels: GENERIC=9, READ=45, WRITE=45)
- Labels come from τ²-bench's own `@is_tool(ToolType.…, mutates_state=…)` decorators, not from us.
- Parse failures: 0
<!-- /source:tau_bench -->

<!-- source:bfcl -->
### Berkeley Function Calling Leaderboard, multi-turn API docs

- Repo: https://github.com/ShishirPatil/gorilla @ `6ea57973c7a6097fd7c5915698c54c17c5b1b6c8`, path `berkeley-function-call-leaderboard/bfcl_eval/data/multi_turn_func_doc/`
- License: Apache-2.0, per GitHub API (repository root LICENSE; the subdirectory has none of its own); copy in `data/tools/LICENSES/`
- Retrieved: 2026-09-23 by `python -m data.fetch.bfcl`
- Output: `data/tools/bfcl.jsonl` (162 functions: gorilla_file_system=18, math_api=17, memory_kv=15, memory_rec_sum=5, memory_vector=12, message_api=10, posting_api=14, ticket_api=9, trading_bot=20, travel_booking=18, vehicle_control=22, web_search=2)
- No read/write labels upstream. Schema dialect is BFCL's (`"type": "dict"`, `"float"`), stored verbatim.
- `impl_effects`: whether the reference implementation in `berkeley-function-call-leaderboard/bfcl_eval/eval_checker/multi_turn_eval/func_source_code/` mutates
  its state, by static analysis (`data/labels/ast_effects.py`). Functions with
  no matching implementation method: 0.
<!-- /source:bfcl -->
