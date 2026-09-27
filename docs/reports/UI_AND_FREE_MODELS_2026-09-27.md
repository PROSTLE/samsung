# Web app redesign, free model paths, and fixes (2026-09-27)

Asked for: a simpler, more interactive web app in the spirit of a supplied mock-up,
with nothing hard-coded; bugs found and fixed; everything cross-checked; and a free
substitute for the Gemini model, because nothing in this project may cost money.

## 1. The web app

One console, used for a live conversation and for replaying any recorded session:

| Panel | Shows | From |
|---|---|---|
| Conversation | voice visualiser; who is speaking; what Keel is holding or running; controls | live: microphone and agent audio levels; replay: the recorded audio's envelope |
| Transcript | what was said, Keel's own lines, Keel's notes | live: LiveKit transcription streams; replay: trace |
| Tool calls | each planned call: Planned, Held, Running, Executed, Dropped, Reused, with its arguments and reason | trace (`keel/web/story.py`) |
| Keel decision log | every state change of every call, filterable (All, Held, Dropped, Executed); click to jump there | trace |
| Turn timeline | lanes for you, the agent (speaking, thinking), tool calls and Keel (holding, executing), with both recordings' waveforms when there is audio; click to seek, zoom "whole conversation" or "follow" | trace + recordings |

Pages: **Overview** (what Keel did across all recorded sessions, the latest run, the
three rules with the configured timings), **Live demo**, **Sessions** (search, filter by
source; each opens its replay), **Benchmark** (FDB-v3's reports per run, per scenario,
each linked to its replay), **Setup** (keys set or not, providers, profiles and
pipelines, fence settings, every tool with Keel's label, and Test buttons that make
one real request each). Light and dark themes; works at phone width. Plain HTML, CSS
and JavaScript, no build step.

**Nothing is typed in.** Every number comes from `/api/*`, which reads traces, FDB-v3's
reports and result files, the config profiles and the environment. The mock-up's sample
figures (pass rate 0.93, "+0.76 vs baseline", latencies) appear nowhere; where data is
missing the page says so.

**Replay with real audio.** FDB-v3 keeps, per scenario, the input recording and the
agent's reply as recorded from the room. The server lines them up with Keel's trace by
the first executed call, which both record (FDB-v3 rounds to 10 ms), and new traces also
store their wall-clock start. Checked against FDB-v3's own word timestamps on finance_21:
the replay puts "…a few options." at 11.3 s and "And…" at 11.6 s of the trace, exactly
where the waveform shows them. That replay also shows why the scores need Keel: Gemini
reported the end of the first sentence 2.3 s late, while the user was already speaking,
the model planned `get_card_benefits` from it, and Keel dropped that call.

## 2. Bugs found and fixed

| # | Bug | Effect | Fix |
|---|---|---|---|
| 1 | The web app read only `traces/livekit/`; Show & Fix writes `traces/show_and_fix/` | Show & Fix sessions never appeared; "Open the session" after one was a 404 | trace directories come from every profile's `livekit.trace_dir` |
| 2 | A trace with no closing record showed "In progress" forever | Killed runs looked live | "Live" only if written in the last 2 minutes, else "Cut short" |
| 3 | `pip install -e ".[livekit]"` lacked the Google plugin, which `keel/livekit/session.py` imports at start-up | The README's Show & Fix install failed with ImportError | `livekit` extra includes `google` |
| 4 | Show & Fix needed OpenAI for its pipeline and its display reader | With no OpenAI credit the extension (20% of the score) could not run | `KEEL_PIPELINE=gemini_realtime` runs it on a Gemini key alone; the reader follows the pipeline |
| 5 | Gemini's OpenAI layer rejects `seed` with HTTP 400 | The Gemini display reader would have failed on its first call | `[providers.gemini] seed = false`; seed sent only where accepted |
| 6 | Every `/api/sessions` call re-parsed every trace and report | Slower as runs accumulate | parsed once per file change (mtime, size) |
| 7 | The web page's files were not package data | A wheel install had no web app | `keel.web` package data |
| 8 | Port already in use: a traceback | Unclear | "port N is in use … start with --port" |
| 9 | Session timeline started at 0 s, before the benchmark's lead-in silence | The conversation was squeezed into the right third | the replay starts shortly before the first speech |
| 10 | (new code) the speech server crashed (500) on a non-multipart upload | — | 400 with the expected form (found by its tests) |
| 11 | (new code) Whisper transcribes silence as "you" | a phantom user turn would drop a held call | faster-whisper's voice-activity filter on |
| 12 | (new code) the WSL sync (`rsync --delete`) would delete `.venv-speech` | the speech environment rebuilt on every run | excluded, like the other environments |
| 13 | **Keel: a call could go out while the user's last words were still being transcribed** | In a cascade, LiveKit can commit a turn from the first stretches of speech while the last one, already spoken, is still in speech-to-text; the fence saw a closed turn and a quiet user. Our open-pipeline travel_10 run sent the Oct 5 search 5 s before "make it October 7th" arrived | fence rule 5: a call waits for the words of every stretch of speech that has ended (capped at 8 s; cascades only). Reproduced through the real gate in `tests/test_livekit_gate.py`, with a control run |
| 14 | With `KEEL_CONFIG` set, the reproduction script still copied `config/fdb_v3.toml` into the run folder | The run folder could misstate the models used | it copies the profile actually used and records its path in `run_info.txt` |
| 15 | Show & Fix: a seven-segment "5E" was read as "SE", which the code table did not match | The right code would have been "not found" | lookup treats the glyphs a seven-segment display cannot tell apart (S/5, O/0, I/1, Z/2) as one and answers with the table's spelling |
| 16 | Show & Fix: Gemini explained a code without calling the lookup, claiming Samsung's page | A meaning from the model's memory presented as Samsung's | the display reading returns the table's entry itself; instructions forbid meanings from memory |
| 17 | A run that crashed before FDB-v3's evaluation showed "in progress" forever | Misleading on the Benchmark page | runs are complete, running (logs changed in the last 10 min) or stopped |
| 18 | Leaving the Live page mid-conversation: the "conversation ended" view replaced the page being opened | Wrong page shown | the ending session is detached first |
| 19 | The first chat request after Ollama loaded the model took 65 s; offline starts re-contacted the registry and Hugging Face | A lost first scenario; no offline start | a real warm-up request; already-present models are not fetched again |

## 3. A free substitute for Gemini

Researched and measured (sources in `SOURCES.md`, "Free and open-weight models"):

| Option | Verdict |
|---|---|
| Gemini Live free tier (`gemini_realtime`, already in place) | Free of charge per Google's pricing page; FDB-v3's own `gemini3_1` model. Kept as the zero-setup free path, now also for Show & Fix |
| **Open-weight models on the machine (`--pipeline open`)** | **The substitute.** No key, no account, no request limit, public checkpoints pinned by version and hash. faster-whisper small.en + Qwen3-4B-Instruct-2507 (Ollama) + Kokoro-82M; Qwen3-VL-2B reads the Show & Fix display. Fits a 4 GB GPU |
| Groq free tier (gpt-oss-20b/120b, Whisper) | Free and fast, but 1,000 requests and 200,000 tokens a day: a demo, not a 100-scenario run. Any `open` stage can point at it with two config lines |
| Gemma 4 on the Gemini API | Free and open-weight, but called the tool with the wrong year and took 8 s |
| Cerebras, LiveKit Inference, OpenRouter free | Need a payment method, or too few minutes |

Measured on this laptop (Ryzen 5 5600H, RTX 3050 4 GB): Kokoro speaks 5.0 s of audio
in 2.9 s on the CPU (its int8 files fail to load or run 3.5x slower than real time);
Whisper small.en transcribes a 5 s clip in 3.0 s on the CPU (it always encodes a 30 s
window) and round-trips a Kokoro sentence exactly. On the evaluation machine's GPU,
speech would take a fraction of that.

## 4. How to run

```
python -m keel.web                                             # the app (pip install -e ".[web]")
wsl bash scripts/keel_live_wsl.sh --pipeline gemini_realtime  # app + benchmark agent, free tier
wsl bash scripts/keel_live_wsl.sh --show-and-fix --pipeline gemini_realtime
scripts/open_models.sh start                                   # local model servers for --pipeline open
scripts/reproduce_fdb_v3.sh --pipeline open --judge none --example travel_10
```

## 5. Verification

- Tests: 223 pass in WSL with LiveKit 1.8.3 (192 before this work); new
  `tests/test_providers.py`, `tests/test_speech.py` (including the OpenAI SDK against
  the real server), web API tests (replay audio, overview, setup never leaks a key),
  LiveKit tests that build the `open` pipeline with the real plugin classes, and fence
  rule 5 at unit and gate level. `python -m eval.grid_self_repair`: 3,072 runs,
  0 invariant violations, 0 double bookings, 0 false success claims.
- Screenshots of every page, light and dark, desktop and 500 px, taken with headless
  Chrome against the real traces and results.
- Real requests: Show & Fix's reader on Gemini (`preflight vision`, and the Setup
  page's Test button); the open pipeline's preflight (all three stages answered); the
  speech round trip.
- Replay alignment: on the open-pipeline run the clock anchor (1,599 ms) and the
  tool-call anchor (1,595 ms) agree within 4 ms.

FDB-v3 travel_10 on `--pipeline open` (LiveKit Cloud, FDB-v3's unmodified runner; local
faster-whisper and Kokoro; the LLM stage on `gemini-2.5-flash` through `[providers]`
because Ollama was still downloading; `--judge none`). Both runs are in `results/fdb_v3/`
with a `NOTE.md`:

| Run | Tool calls FDB-v3 logged | Tool selection | Why |
|---|---|---|---|
| `20260926T204452Z_keel_open_travel_10` (before rule 5) | `search_flights(Miami, 2026-10-05)`, `search_flights(Miami, 2026-10-07)` | 66.7% | the Oct 5 call went out at 23.2 s; the correction's words arrived at 28.5 s |
| `20260926T205942Z_keel_open_travel_10` (after rule 5) | `search_flights(Miami, 2026-10-07)` | 100% | held at 20.9 s "speech not yet transcribed", dropped at 26.2 s when the words came |

Both still fail FDB-v3's rule-based argument check only on the date's form ("2026-10-07"
where the benchmark expects "October 7"), which its gpt-4o judge compares semantically
in the official re-run. FDB-v3's `evaluate_pass_rate.py` crashes after writing its report
when a run has no scenario without rollback (it formats `None`, line 579); upstream, not ours.

Fully local (every model on the laptop, default `config/fdb_v3.toml`, no model key):

| Run | Tool calls FDB-v3 logged | Note |
|---|---|---|
| `20260927T053340Z_keel_open_travel_10` | `search_flights(Miami, 2026-10-07)` only | one LLM request timed out at LiveKit's 10 s default and restarted |
| `20260927T054338Z_keel_open_travel_10` | `search_flights(Miami, 2026-10-07)` only | with `request_timeout_s = 60`: no restart |

In both, the spoken answer came after FDB-v3's 47.6 s recording ended, so turn-taking
scores 0: the laptop was on battery, its GPU measured 17 GB/s and Ollama generated
7 tokens/s (11 of 37 layers on the CPU with an 8,192-token context). Found and fixed on
the way: the first chat request after loading took 65 s (the warm-up now runs a real
chat request: 5.7 s), and LiveKit's 10 s request deadline restarted a slow request
(`request_timeout_s`). `KEEL_OLLAMA_CONTEXT=4096` puts all layers on a 4 GB card.

The Live page, end to end: the benchmark agent on `gemini_realtime` in WSL, and headless
Chrome joining from the Live page with FDB-v3's travel_10 recording as its microphone
(driven over the DevTools protocol). The page showed the agent joining, both
transcripts, `search_flights` planned, held and dropped twice while the user was still
correcting ("Oh, wait."), then executed once with Miami and 2026-10-07; "End session"
showed "3 calls planned · 1 executed · 2 dropped", and "Replay this session" opened it.

Show & Fix, end to end, free (the extension): the agent on `gemini_realtime`, a synthetic
seven-segment "5E" display as the camera, and headless Chrome speaking a Kokoro-voiced
script into the Live page. It read the display, explained Samsung's entry for 5E ("water
is not draining", with the drain-hose steps), booked a technician once for Saturday
morning after "Friday morning. Oh, wait. No, make it Saturday morning." (held until the
user finished), and answered "did it go through?" from `list_technician_bookings`. The
first attempt found bugs 15 and 16, fixed before the second.

## 6. Open items

- **The local LLM's speed on this laptop.** Plugged in (not on battery) it will be faster;
  on the evaluation machine's GPU far faster. Not measured here.
- **Show & Fix with a real camera** needs a person in front of a washer (or a photo with
  `KEEL_SHOW_AND_FIX_IMAGE`); the Gemini reader and the tools are verified separately.
- **Speech on the laptop CPU** adds about 3 s per turn (Whisper's fixed 30 s window);
  the evaluation machine's GPU removes it.
- **No full 100-scenario scored run yet**, and the video and slides need you.
