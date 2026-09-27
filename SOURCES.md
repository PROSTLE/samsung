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

## Model availability (OpenAI deprecations page)
- https://developers.openai.com/api/docs/deprecations (the old platform.openai.com URL
  redirects there), read 2026-09-25. Rows for the models Keel uses:
  - `gpt-4o` (agent LLM, Show & Fix vision, FDB-v3's judge): the alias is in no
    shutdown list. Only the snapshot `gpt-4o-2024-05-13` shuts down (October 23, 2026).
  - `tts-1`: not mentioned.
  - `whisper-1`: announced 2026-08-26, shutdown **Feb 26, 2027** (replacement
    `gpt-live-transcribe` or `gpt-transcribe`).
  - `gpt-realtime` family (the optional `--pipeline gpt_realtime`): announced
    2026-07-20, shutdown **Jan 20, 2027** (replacement `gpt-realtime-2.1`).
- A re-run after those dates needs `livekit.cascaded.stt_model` (or the realtime
  model) in `config/fdb_v3.toml` moved to the replacement. Keel changes no code
  for that.

## Web app (`keel/web/`)
- LiveKit text streams, https://docs.livekit.io/home/client/data/text-streams/ and
  https://docs.livekit.io/agents/build/text/ (read 2026-09-25): agents publish transcripts on
  the `lk.transcription` topic with `lk.segment_id` / `lk.transcription_final` attributes;
  `room.local_participant.send_text(text, topic=...)` in Python and
  `room.registerTextStreamHandler(topic, ...)` in JavaScript. The installed
  `LocalParticipant.send_text` takes `destination_identities` (checked with `inspect`,
  livekit 1.1.18), which the app uses to reach only its own participants.
- livekit-client 2.22.3: the jsDelivr package API's "latest" tag on 2026-09-25
  (https://data.jsdelivr.com/v1/packages/npm/livekit-client); pinned in `keel/web/static/live.js`.
- Interface research (2026-09-25): LiveKit's agent-starter-react
  (https://github.com/livekit-examples/agent-starter-react: welcome view, session view with
  transcript, control bar, one audio visualiser, bars by default) and Pipecat's Voice UI Kit
  (https://github.com/pipecat-ai/voice-ui-kit: live transcripts, mic and camera controls,
  audio visualisers). Call-log products (Vapi, Retell) break a call down turn by turn with
  its tool calls and timing. Keel's own addition is the action card: what the kernel did
  with each planned call, and why.
- Replay audio (2026-09-27): FDB-v3's `run_tool_benchmark.py` at the pinned commit writes,
  per scenario folder, `output_{provider}.wav` (the agent's audio recorded from the room,
  line 289) and `input_mono.wav` (line 344), records `stream_start_time` from its client's
  `STREAM_START_TIME=` line (lines 243-252, Unix seconds), and turns each executed call's
  `timestamp_start` into seconds after that start (lines 432-450). Keel's tool log gives
  that timestamp at dispatch (`keel/livekit/fdb.py`), and the trace records the same
  dispatch, so one executed call lines the recording up with the trace to within FDB-v3's
  10 ms rounding. Checked on finance_21 (Gemini run, room `eval-6420f1e7`): offset 2,072 ms;
  FDB-v3's own ASR puts "…a few options." at 8.24-9.20 s and "And…" at 9.52 s of the
  recording, i.e. 11.3 s and 11.6 s of the trace, where the replay's waveform shows them.
  New traces also record `unix_ms` in `session_start`, which lines them up directly.
- Design reference: the user's mock-up (sidebar, live conversation with waveform,
  transcript, tool-call status, Keel decision log, turn timeline, benchmark and system
  cards). Every number on the pages comes from `/api/*`; nothing in the mock-up's sample
  data is used.

## Gemini Live pipeline (`--pipeline gemini_realtime`)
- FDB-v3 `v3/lk_agent_tool.py` at the pinned commit, provider `gemini3_1`:
  `google.realtime.RealtimeModel(model="gemini-3.1-flash-live-preview", voice=os.getenv("GOOGLE_VOICE", "Puck"))`,
  key `GOOGLE_API_KEY`. `config/fdb_v3.toml [livekit.gemini]` uses the same model and
  voice; `tests/test_livekit_session.py` checks the model against that file.
- https://ai.google.dev/gemini-api/docs/pricing, read 2026-09-25: `gemini-3.1-flash-live-preview`
  is "Free of charge" for input and output on the free tier. Free-tier rate limits are
  not published there; https://ai.google.dev/gemini-api/docs/rate-limits says they are
  shown per account in Google AI Studio.
- https://ai.google.dev/gemini-api/docs/models, read 2026-09-25: `gemini-3.1-flash-live-preview`
  is listed as a Live API model ("legacy version"; `gemini-3.8-live` is the default).
- https://docs.livekit.io/agents/models/realtime/plugins/gemini/, read 2026-09-25:
  `livekit-agents[google]`, `google.realtime.RealtimeModel`, key `GOOGLE_API_KEY`, tool
  calling supported. The installed plugin (`livekit-plugins-google==1.8.3`) reports
  `supports_say=False`, so Keel's fillers are off in this pipeline, as for `gpt_realtime`.
- FDB-v3 `evaluate_tool_calls.py` / `evaluate_pass_rate.py`: without `--use-llm`, argument
  accuracy is rule-based and `response_qual` is `None` ("LLM judge disabled"). The script's
  `--judge none` is exactly that.
- Checked and not used: LiveKit Cloud's free plan includes $2.50 of LiveKit Inference
  credit, "~50 minutes" (https://livekit.com/pricing, 2026-09-25), too little for a full
  run; Groq's free tier caps `openai/gpt-oss-120b` at 8K tokens/min and 200K tokens/day
  (https://console.groq.com/docs/rate-limits, 2026-09-25).

## Free and open-weight models (pipeline `open`, Show & Fix on Gemini)

Researched 2026-09-26/27 for a run that costs nothing. Measurements are on the
development laptop (AMD Ryzen 5 5600H, 12 threads, NVIDIA RTX 3050 Laptop 4 GB, WSL2).

- **Gemini API pricing**, https://ai.google.dev/gemini-api/docs/pricing (read 2026-09-26):
  `gemini-3.1-flash-live-preview` "Free of charge" on the free tier (input and output);
  Gemini 2.5 Flash, 2.5 Flash-Lite and Gemma 4 are also free of charge on the free tier.
  The models this key can call were listed with `GET /v1beta/openai/models` (2026-09-27):
  `gemini-2.5-flash`, `gemma-4-26b-a4b-it`, `gemma-4-31b-it` among 61.
- **Gemini's OpenAI compatibility layer**, https://ai.google.dev/gemini-api/docs/openai:
  base URL `https://generativelanguage.googleapis.com/v1beta/openai/`, images as base64
  `image_url`, JSON output. Measured 2026-09-27: every model answered **HTTP 400
  "Unknown name \"seed\""** to a request with `seed`, although the page says unknown
  parameters are ignored; hence `[providers.gemini] seed = false`. Without it,
  `gemini-2.5-flash` called `search_flights` with the corrected date in 1.45 s and read
  an image in JSON mode in 3.1 s; `gemma-4-26b-a4b-it` made the call in 8.0 s with the
  wrong year and its reasoning in the reply text, so it is not offered as a default.
- **Groq**, https://console.groq.com/docs/openai: base URL `https://api.groq.com/openai/v1`;
  `temperature` 0 becomes 1e-8. Free-tier limits: Groq's rate-limit page renders its table
  in the browser, so they are taken from https://klymentiev.com/blog/groq-pricing (updated
  2026-09-12): gpt-oss-120b / gpt-oss-20b / Qwen 27B at 30 requests/min, 1,000/day,
  8,000 tokens/min, 200,000 tokens/day; Llama 3.1 8B and 3.3 70B left the free tier on
  16 August 2026; Whisper 2,000 requests/day; Orpheus TTS 100 requests/day. Enough for
  a demo, not for a 100-scenario run (FDB-v3's instructions and 12 tool schemas are
  thousands of tokens per request).
- **Not used**: Cerebras' free trial needs a verified payment method for its $5 credit
  (https://inference-docs.cerebras.ai/support/rate-limits, 2026-09-26); LiveKit
  Inference gives $2.50 a month on the free Build plan, about 50 minutes
  (https://livekit.com/pricing, 2026-09-26).
- **LiveKit OpenAI plugin 1.8.3** (installed source, `livekit/plugins/openai/`): `LLM`,
  `STT` and `TTS` take `base_url`; `LLM.with_ollama` defaults to
  `http://localhost:11434/v1`; `STT(use_realtime=...)` defaults to the plain
  transcription endpoint for non-realtime models; `TTS` decodes a compatible server's
  raw audio by its `Content-Type` ("OpenAI-compatible servers ignore it and answer with
  the audio bytes of `response_format`", the plugin's own comment).
- **Ollama** v0.34.4, https://github.com/ollama/ollama/releases/tag/v0.34.4 (latest on
  2026-09-26, MIT): Linux x86-64 ships only as `ollama-linux-amd64.tar.zst` (1,361 MB);
  the official install script needs `zstd` and `sudo`, so `scripts/open_models.sh`
  unpacks it with Python's `zstandard` into `third_party/ollama`.
- **LLM**: `qwen3:4b-instruct-2507-q4_K_M`, https://ollama.com/library/qwen3:4b-instruct-2507-q4_K_M
  (2.5 GB, tools); Qwen3-4B-Instruct-2507 is Apache-2.0 (Hugging Face model card
  `license: apache-2.0`, 2026-09-27). The "instruct" release has no thinking phase.
- **Display reader** (Show & Fix, pipeline open): `qwen3-vl:2b-instruct-q4_K_M`,
  https://ollama.com/library/qwen3-vl/tags (1.9 GB, text and image input);
  Qwen3-VL-2B-Instruct is Apache-2.0. Qwen2.5-VL-3B was passed over: its card says
  `license_name: qwen-research` (non-commercial), unlike the 7B (Apache-2.0).
- **TTS**: Kokoro-82M (Apache-2.0, https://huggingface.co/hexgrad/Kokoro-82M) through
  kokoro-onnx 0.6.1 (MIT, https://pypi.org/project/kokoro-onnx/, released 2026-08-19).
  Files from https://github.com/thewh1teagle/kokoro-onnx/releases/tag/model-files-v1.1:
  `kokoro-v1.0.onnx` (310 MB, sha256 `beb0d184…df3a`), `voices-v1.0.bin` (sha256
  `bca610b8…bf7d`, 54 voices). Measured on the CPU: the v1.1 int8 file fails to load in
  ONNX Runtime 1.23.2 ("Could not find an implementation for ConvInteger(10)"); the v1.0
  int8 file speaks 4.2 s of audio in 13.6 s; the full-precision file 5.0 s in 2.9 s.
- **STT**: faster-whisper 1.2.1 (MIT), model `Systran/faster-whisper-small.en` (MIT) at
  revision `d1d751a5f8271d482d14ca55d9e2deeebbae577f` (Hugging Face API). A Kokoro
  sentence round-trips exactly ("Find me flights to Miami on October 5th. Oh, wait. Make
  it the 7th."). On the CPU a 5 s clip takes 3.0 s (6 threads; 12 threads is slower,
  6.5 s; `base.en` 2.1 s): Whisper always encodes a 30 s window, so a short clip costs
  nearly as much as a long one. On a GPU this is a fraction of a second.
- **The LLM on the laptop** (2026-09-27): with FDB-v3's instructions and 12 tool schemas
  (about 1,850 prompt tokens), Qwen3-4B-Instruct made the right calls for the travel_10
  correction (`search_flights(Miami, 2026-10-07)`), "two of item P52… no, wait, just one"
  (`add_to_cart(P52, 1)`) and the gold-card-plus-yen request (both calls). Speed: the
  laptop was on battery (Win32_Battery status 1); the GPU copied memory at 17 GB/s and did
  4.0 fp16 TFLOPS; Ollama processed prompts at 120-440 tokens/s and generated 6-7 tokens/s,
  and with an 8,192-token context kept 11 of 37 layers on the CPU (2.8 GiB of the 4 GB
  card available). Generation is memory-bound: 2.4 GB of weights per token at 17 GB/s is
  about 7 tokens/s, as measured. The first chat request after loading took 65 s until
  `scripts/open_models.sh` warmed the model with a real chat request (then 5.7 s).
  Plugged in (Win32_Battery status 2), with `KEEL_OLLAMA_CONTEXT=4096`: GPU in P0,
  memory clock 6,001 MHz, prompts at 1,168 tokens/s, generation at 21.2 tokens/s.
- **LiveKit's request deadlines** (installed `livekit/agents/types.py`): `APIConnectOptions`
  defaults to `timeout=10.0`, `max_retry=3`; the OpenAI plugin's HTTP client allows 5 s
  between bytes. A local request timed out at 10.0 s and started again in our first local
  run, hence `[livekit.open].request_timeout_s`.
- **Speaches** (https://github.com/speaches-ai/speaches, an OpenAI-compatible
  faster-whisper + Kokoro server) was considered and not used: it requires exactly
  Python 3.12 and pulls PyTorch (pyannote-audio) and Gradio. `keel/speech/server.py`
  serves the two endpoints the plugin calls, in about 200 lines.
- **FDB-v3's "no own servers" rule** (updated guide §6: "Don't call your own servers at
  evaluation time; all agent logic lives in the submission") and "Do use public
  checkpoints": the open pipeline's servers are started by the reproduction script on
  the evaluation machine itself, from public checkpoints pinned by version and hash;
  nothing leaves that machine except LiveKit's audio.

## Show & Fix end to end (2026-09-27)

- Run: the Show & Fix agent on `gemini_realtime` in WSL (`KEEL_SHOW_AND_FIX_IMAGE` = a
  synthetic seven-segment "5E" display, labelled so in the image), and headless Chrome on
  the web app's Live page with a Kokoro-spoken script as its microphone ("…can you read it
  and tell me what it means?", "…book a technician for Friday morning. Oh, wait. No, make it
  Saturday morning.", "Did the booking go through?").
- First run: `gemini-2.5-flash` read the display as "SE" (confidence 1), and the agent
  explained the code without calling `lookup_error_code` while saying it came from
  Samsung's page. Booking (Saturday, held until the user finished) and the status check
  (`list_technician_bookings`) were right.
- Wikipedia, https://en.wikipedia.org/wiki/Seven-segment_display (read 2026-09-27):
  "Uppercase letters "B", "I", "S", "Z", and "D" & "O" conflict with the common
  seven-segment representation of digits "8", "1", "5", "2", and "0"". Hence the lookup
  treats S/5, O/0, I/1, Z/2 as one; B and D are not, because Samsung shows them as
  lowercase b and d, and D/0 would merge the table's dC (door) and OC (overflow).
- Second run, after the fixes (the reading carries Samsung's entry; seven-segment
  matching): the agent explained "the water isn't draining" and the drain-hose steps from
  the table, and booked `book_technician(code 5E, Saturday, morning)` once.

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

### Levelt (1983): the three phases of a self-repair
- W. J. M. Levelt, "Monitoring and self-repair in speech", *Cognition* 14:41–104, 1983.
  doi:10.1016/0010-0277(83)90026-4. Abstract read at
  https://www.mpi.nl/publications/item64752/monitoring-and-self-repair-speech (2026-09-25;
  the full-text PDF returned HTTP 403).
- Abstract: "The first phase involves the monitoring of one's own speech and the
  interruption of the flow of speech when trouble is detected." "The second phase is
  characterized by hesitation, pausing, but especially the use of so-called editing
  terms." "The third phase consists of making the repair proper." 959 spontaneous repairs.
- Used for: fence rule 4 (`keel/kernel/repair.py`): a turn made only of editing terms
  ends in phase two, so calls planned from it wait for the next turn. The abstract gives
  no pause length; the bound (`fence.repair_wait_ms = 5000` in the LiveKit profiles) is
  the FDB-v3 template's own `max_endpointing_delay=5.0` (cascaded_agent.py), the longest
  it waits for a user who is not finished. The term list is Keel's (general English
  hesitation and editing terms, `config/fdb_v3.toml`), not taken from benchmark items.
- Observed (our run `20260925T140743Z_keel_gemini_realtime_travel_10`, trace
  `eval-318ad861`): after "Oh, wait." Gemini Live re-planned the pre-correction call,
  which went out at 11.4 s; the user resumed about 1.6 s after "Oh, wait." ended.

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

| livekit-agents (+ openai, google, silero, turn-detector plugins) | the voice agent | Apache-2.0 | https://github.com/livekit/agents (GitHub API, 2026-09-25); 1.8.3 pinned |
| livekit (rtc) | LiveKit client used by the agent and FDB-v3's client | Apache-2.0 | https://github.com/livekit/python-sdks (GitHub API, 2026-09-25) |
| openai | OpenAI API client (agent vision reader; FDB-v3 judge) | Apache-2.0 | https://github.com/openai/openai-python (GitHub API, 2026-09-25) |
| nemo_toolkit[asr] | FDB-v3's ASR (benchmark environment only) | Apache-2.0 | https://github.com/NVIDIA/NeMo LICENSE (2026-09-25) |
| gdown | downloads FDB-v3's audio | MIT | https://github.com/wkentaro/gdown (GitHub API, 2026-09-25) |
| aiohttp | the web app and the local speech server | Apache-2.0 | https://github.com/aio-libs/aiohttp (GitHub API, 2026-09-27); 3.14.3 |
| livekit-api | signs the web app's room tokens, LiveKit check on the Setup page | Apache-2.0 | https://github.com/livekit/python-sdks (GitHub API, 2026-09-27) |
| faster-whisper (+ CTranslate2) | speech-to-text in the open pipeline | MIT, MIT | https://github.com/SYSTRAN/faster-whisper, https://github.com/OpenNMT/CTranslate2 (GitHub API, 2026-09-27) |
| kokoro-onnx (+ ONNX Runtime) | text-to-speech in the open pipeline | MIT, MIT | https://github.com/thewh1teagle/kokoro-onnx, https://github.com/microsoft/onnxruntime (GitHub API, 2026-09-27) |
| zstandard | unpacks Ollama's release archive | BSD-3-Clause | https://github.com/indygreg/python-zstandard (GitHub API, 2026-09-27) |
| Ollama (binary, not a Python package) | serves the open pipeline's LLM and vision model | MIT | https://github.com/ollama/ollama (GitHub API, 2026-09-27); 0.34.4 pinned in `scripts/open_models.sh` |

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
