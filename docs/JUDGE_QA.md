# Judges' cross-questions: prepared answers

**Read the "Theme update" section first.** Scoring now uses Full-Duplex-Bench
v3 over LiveKit. Answers further down that cite "the original guide" describe
the first version of the brief; the mechanisms they explain are unchanged.

Short answers first, evidence second. Every external claim is sourced in
`SOURCES.md`. Updated at the end of each phase.

## Free models and the web app (2026-09-27)

**Q: Your default pipeline is OpenAI's, which costs money. Can anyone run this for free?**
Yes, two ways, both with Keel under every tool call. `--pipeline gemini_realtime`
uses Gemini Live, which Google's pricing page lists as free of charge on the free tier;
it is FDB-v3's own `gemini3_1` model. `--pipeline open` uses only open-weight models on
the machine itself (faster-whisper, Qwen3-4B-Instruct through Ollama, Kokoro): no key,
no account, no request limit. The default stays OpenAI's cascade because it is FDB-v3's
cascaded template and the published baseline, so a score difference is Keel's, not a
model swap.

**Q: Isn't a local model server "your own server", which the guide forbids?**
The rule is "Don't call your own servers at evaluation time; all agent logic lives in
the submission." The open pipeline calls nothing outside the evaluation machine: the
reproduction script downloads public checkpoints (pinned by version, revision and
sha256), starts Ollama and `keel/speech/` on 127.0.0.1, and stops them afterwards. The
code of both is in the submission or a pinned public release. The guide also says "Do
use public checkpoints". Only LiveKit's audio leaves the machine, as for every pipeline.

**Q: Why write a speech server instead of using an existing one?**
We looked at Speaches, the usual OpenAI-compatible faster-whisper + Kokoro server. It
requires exactly Python 3.12 and pulls PyTorch and Gradio. LiveKit's plugin calls only
two endpoints, so `keel/speech/server.py` serves those in about 200 lines, refuses a
model it has not loaded instead of answering with another, and is tested with the
OpenAI SDK itself (`tests/test_speech.py`).

**Q: Why these models?**
Each is the smallest that works on a 4 GB laptop GPU and is permissively licensed:
Qwen3-4B-Instruct-2507 (Apache-2.0, tool calling, no thinking phase to add latency),
Kokoro-82M (Apache-2.0; the full-precision file, because the int8 files either fail to
load in ONNX Runtime or run 3.5x slower than real time on our CPU), faster-whisper
small.en (MIT), Qwen3-VL-2B for Show & Fix (Apache-2.0; Qwen2.5-VL-3B's licence is
non-commercial). Measurements are in `SOURCES.md`. We also tried Gemma 4 on the free
Gemini API: it called the tool but with the wrong year, and took 8 s.

**Q: What does the web app's replay actually play?**
FDB-v3's own files: the scenario's input audio and the agent's reply as FDB-v3
recorded it from the room. They are lined up with Keel's trace by the first executed
call, which both record (FDB-v3 to 10 ms), and we checked the result against FDB-v3's
word timestamps. New traces also store their wall-clock start. A session without a
recording plays without sound and says so. No number on any page is typed in: they come
from traces, FDB-v3's reports, the config and the environment.

**Q: What did running on slow, local speech-to-text teach you?**
A fifth fence rule. In our first open-pipeline run of travel_10, local Whisper took
5.5 s, 2.5 s and 5.7 s for the three stretches of speech. LiveKit committed the turn
from the first two ("…October 5th. Oh, wait.") at 22.6 s, although the user had
finished the third ("…make it October 7th instead.") at 22.2 s. The model planned the
Oct 5 search, Keel held it 600 ms, saw a closed turn and a quiet user, and sent it;
the correction's words arrived at 28.5 s. The rule: a call also waits until every
stretch of speech that has ended has had its words arrive (capped, because LiveKit
sends nothing for a stretch that yields no words). It is general: any cascade whose
speech-to-text lags its voice detection, OpenAI's included, has the same race; the
local run only made it wide enough to see. A test reproduces the race through the
real gate, with a control run showing the stale call going out without the rule.

**Q: What does the replay show that the scores do not?**
Why a call was or wasn't made. In our Gemini run of finance_21 the user said "…the gold
card because I'm comparing a few options." and went straight on with "And then…". Gemini
reported the end of that first turn 2.3 s late, while the user was already speaking,
and the model planned `get_card_benefits` from it. Keel dropped that call, and both
calls then ran once, after the whole request. You can hear it and see it on the timeline.

## Theme update: FDB-v3 and LiveKit (2026-09-25)

**Q: The theme changed to FDB-v3 on LiveKit. Did you throw away the kernel?**
No. The kernel was built behind an adapter precisely because the kit was
unknown. The LiveKit layer (`keel/livekit/`) is that adapter: LiveKit session
events and LLM tool calls become kernel events, and the kernel's ledger, commit
fence and idempotency decide what runs. `docs/KIT_ASSUMPTIONS.md` records what
each of the 19 wire-format guesses became.

**Q: What exactly does Keel change in a FDB-v3 run?**
Relative to FDB-v3's cascaded template, six things. The models are the same
(Whisper, GPT-4o, OpenAI TTS), so any score difference comes from these:
1. Every tool call is held until the user has finished (turn ended, not
   speaking, 600 ms quiet), and dropped if they keep talking before it is sent.
2. An identical call later in the session is answered from the first one.
3. End of turn uses LiveKit's end-of-utterance model instead of VAD silence alone.
4. Four general instructions about self-corrections and superseded calls,
   appended to the template's own instructions.
5. The LLM runs at temperature 0 with a fixed seed (the template sets neither),
   so a re-run is as repeatable as the API allows.
6. While a call is held or running, Keel may say a short truthful line itself
   ("One moment.", the tool's acknowledgment, "Still working on it.") through
   LiveKit's filler mechanism, at most three per call.

**Q: Why hold read-only calls too? That costs latency.**
In FDB-v3 it costs correctness not to. The strict pass rate fails a scenario on
any *unexpected* call (`evaluate_pass_rate.py`, check 1), and a read that ran on
"Paris" before the user said "no, Berlin" is exactly that. The fence counts
from the end of the user's turn, not from the LLM's proposal, so a call the LLM
proposes more than 600 ms after the user stopped is not delayed at all. In the
extension, where nothing scores a wasted read, reads run speculatively
(`config/show_and_fix.toml`). It's one flag, set per deployment with a stated
reason.

**Q: Why not just cancel the stale call when the correction arrives?**
Because it's too late by then. LiveKit does not cancel a running tool
(voice/generation.py waits for it to finish), and once FDB-v3's tool log has the
line, the scorer counts it. So Keel never dispatches a call it might have to
take back.

**Q: How do you know the tool log you write is what FDB-v3 scores?**
The format and path are read from FDB-v3's runner at the pinned commit (step 6
of `run_tool_benchmark.py`). A test runs FDB-v3's real mock registry and checks
the line shape. Another fires 20 concurrent calls and checks every line is
whole: an early version interleaved lines from parallel calls, which would have
silently corrupted scoring.

**Q: Did you tune anything on the benchmark?**
No, and the guide forbids it. The timing values come from conversation research
(Roberts & Francis 2013; Jefferson 1988), the classifier threshold was fixed in
phase 3, and the extra instructions mention no benchmark item. The only use of
the benchmark data is a compatibility test: every argument shape the benchmark
expects must pass Keel's validation. That test found two, a number and a
boolean for `value: str`, which Keel now converts to strings rather than bouncing
back to the LLM.

**Q: Why is there no score in the README?**
A run needs a LiveKit Cloud project and an OpenAI key, which we had not
configured when this was written. We ran everything we could without them. A
real `AgentSession` executes Keel's tools in text mode. A local LiveKit server
received FDB-v3's own client streaming a real benchmark recording into Keel's
agent, which joined, detected speech and reached STT, where the fake key stopped
it. On a clean Linux machine (WSL Ubuntu, GPU) the whole reproduction script ran
end to end against a local LiveKit server: install, FDB-v3's own runner, NeMo ASR
on the GPU, all three evaluations, and collection. The only thing missing was a
real OpenAI key, so the agent heard the user but could not transcribe. That dry
run found five bugs, including one that would have kept the agent out of every
room on Linux. The score
will come from `scripts/reproduce_fdb_v3.sh`.

**Q: What is the extension, and why that one?**
Show & Fix: point the camera at a Samsung washer's display, and the agent reads
the code, explains it from Samsung's own published table, and books a
technician, exactly once, even if you change the day mid-sentence. It is the
guide's own example ("device troubleshooting with a camera frame"), it is
Samsung's product domain, and it uses every part of Keel: perception with a
confidence threshold, a state-changing call behind the fence, self-correction,
and a status probe for "did it go through?".

**Q: Could the vision model invent an error code?**
It can misread one. That is why its reading only counts above
`perception.clarify_below` confidence; below it the agent asks the user. Its
prompt forbids inferring a code from what codes usually exist. And a code
missing from Samsung's table is answered "I don't have that one", never with a
guess.

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
unstable. The original guide scored "prompt cancellation of *invalidated* calls" (§5),
not of all calls.

**Q: How does this map to the Samsung use cases?**
The original guide listed four (§2): in-car reroutes (a destination change invalidates
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
The original guide's budgets were "few ms" and "few hundred ms"; integers make replay
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
It may have succeeded; a retry could double-book, and the original guide scored "zero
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

## Manifest compiler (phase 3)

**Q: How accurate is your read/write classifier?**
Measured leave-one-domain-out on 263 real tools from τ²-bench and BFCL, so
every tool is scored as if never seen. At the shipped (pre-registered)
confidence, 1 of 123 writes would be treated as read-only, and 41% of reads
are recognised as safe to speculate. Accuracy of the treated label is 0.684.
The single unsafe case is `get_flight_cost`, which looks like a price quote
but caches a value the booking later uses. Full table:
`python -m eval.compiler_eval`.

**Q: A verb list gets higher recall than your model. Why ship the model?**
At the threshold we fixed before evaluating, yes: 0.636 vs 0.414 read
recall. At t = 0.55 the model beats the verb list on all three metrics
(0.852 accuracy / 1 unsafe / 0.729 recall). We didn't move the threshold
after seeing test results; that would be tuning on the test set. The model
also explains each decision and learns from data rather than a hand list.

**Q: Where do the labels come from? Did you label your own test set?**
No. τ²-bench's authors label their tools READ/WRITE in code. For τ-bench v1
and BFCL we derive labels from each tool's *reference implementation* by
static analysis (does the code mutate its environment?). That analyser agrees
with τ²-bench's authors on 28/28 tools the two benchmarks share. Two policy
overrides (hand-off / contacting a person counts as state-changing) are
listed in `data/labels/POLICY.md`.

**Q: What happens when the classifier is wrong?**
If it wrongly says "write", the call just waits behind the commit fence
(latency, not correctness). If it's unsure, the tool is `unknown`, which is
also treated as a write. A wrong "read" is the dangerous direction, and it
only happens above the confidence threshold. Explicit manifest hints (MCP
`readOnlyHint`, HTTP method, etc.) always override the classifier.

**Q: Why not use an LLM to classify tools?**
It's supported (`PromptClassifier`: fixed prompt, few-shot real examples,
strict JSON schema, falls back on any failure) but not the default. It's
unknown whether hosted models are reachable during evaluation, and we won't
claim an accuracy we haven't measured. The lexical model runs offline in
~0.1 ms per tool (measured) with no warm-up.

**Q: How does Keel answer "did it go through?" for a tool it has never seen?**
It looks in the same manifest for a read-only tool about the same thing
(shared object words, e.g. `book_reservation` ↔ `get_user_reservations`)
that it can call with arguments the write already had. It then reads the
result structurally: a matching record means executed, and an empty result
or no match means not executed. Anything else, and Keel says it can't confirm.

## Audit and console (2026-09-24)

**Q: The first booking went through before the user's correction arrived. Now what?**
Keel never books a second time. It ends the goal with a truthful final
response ("That was already submitted with the earlier details, so it needs
a change rather than a second submission.") carrying the user's latest
slots. A later goal that uses a modify tool can still change it. Before
this fix Keel waited silently for a replan that nothing could send, and 356
of the 3,072 grid runs ended with no final response. The grid now fails if
any run lacks one (`python -m eval.grid_self_repair`).

**Q: What if the LLM's plan forgets a required argument?**
The compiler maps every tool argument to the session slot of the same name.
A required argument left unbound reads that slot. If the slot is empty, Keel
asks ("What user id should I use?"). Before the audit this map was computed
but never used, so such a call could never pass validation and the session
stalled after "One moment."

**Q: Can I see what Keel did, not just a number?**
`python -m eval.showcase` then open `traces/console.html`. Every session shows
I1-I5 checked from its own trace, time to first response per turn, a
timeline (fence holds hatched, cancellations with their reason, in-doubt
writes dashed), the conversation, each slot's version history, and the raw
event log. Showcase sessions are labelled synthetic because the slow path is
scripted.

**Q: What can't Keel do yet?**
Understand anything by itself. There's no slow path yet (phase 4), so today
text, audio and frames reach a scripted interpreter only. Against the real
kit, Keel would acknowledge and then wait. Everything downstream of an
interpretation is built and measured; turning speech into interpretations
is next.

## Honesty

**Q: Are your numbers real?**
Every number in the README comes from a command in the repo and is labelled
*measured* (with the command) or *target*. Synthetic scenarios are marked
`synthetic` in the trace header and in the console.
