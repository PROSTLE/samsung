# Demo video script (3–5 minutes)

The guide (§3, step 5; §4) asks for one video: **a real interruption being
handled on the benchmark, then the extension use case in action**, preferring
"unedited single takes over polish". This is a shot list for one take of about
4 minutes. The words to say are suggestions; say them naturally, with real
hesitations.

## Before recording (10 minutes, once)

1. `.env` filled in (LiveKit Cloud + OpenAI), `pip install -e ".[livekit]"` done,
   and `python -m keel.livekit.agent download-files` run once.
2. **Run only one Keel agent at a time.** Both agents use LiveKit's automatic
   dispatch, so every running agent joins every room in the project.
3. For Part B, have the washer's display showing a code (or a clear photo of one
   on a second screen or on paper). A 4C/4E code works well, because Samsung's
   table has several steps for it.
4. Open the LiveKit Cloud dashboard, Agent Console (or the Agents Playground), in a
   browser, with microphone and camera permission granted.
5. Screen layout: browser on the left, a terminal on the right.

## Part A: the benchmark agent handles a self-correction (about 1:45)

| Time | Show | Do / say |
|---|---|---|
| 0:00 | Terminal | "This is Keel, running as a LiveKit agent for Full-Duplex-Bench v3." Run `KEEL_FDB_DIR=third_party/Full-Duplex-Bench/v3 python -m keel.livekit.agent dev`. |
| 0:15 | Agent Console, connected | Say, with a real correction: *"Hi… um, can you look up flights to Paris — actually no, scratch that, make it Berlin, on September tenth."* |
| 0:35 | Agent replies | Point out that it searched Berlin, not Paris. |
| 0:45 | Terminal | `python -m keel.console traces/livekit/*.jsonl -o demo.html`, then open `demo.html`. |
| 1:00 | Console: this session | Walk the timeline: the user's speech, the call **held** until the user finished, then sent once. If the LLM had planned a Paris call before the correction arrived, it shows as **cancelled before it was sent**, with the reason `utterance v1->v2`. |
| 1:25 | Console: "Tool calls" | "One call, Berlin. FDB-v3 counts every executed call, so a Paris call that had run would have failed this scenario, and here it never ran." |
| 1:35 | `results/fdb_v3/<run>/` (if a scored run exists) | Show the pass-rate report and this scenario's line. Otherwise skip. |

Optional, shows the benchmark's own path: run
`scripts/reproduce_fdb_v3.sh --example travel_10` beforehand and show its
`results/fdb_v3/<run>/per_scenario/*.json` (`actual_tool_calls`).

## Part B: Show & Fix, the extension (about 2:00)

Stop the benchmark agent first (step 2 above).

| Time | Show | Do / say |
|---|---|---|
| 1:45 | Terminal | "Now something beyond the benchmark: troubleshooting a Samsung washer through the camera." Run `python -m extension.show_and_fix.agent dev`. |
| 2:00 | Agent Console, camera on, pointed at the display | *"My washing machine is showing an error, can you tell me what it means?"* |
| 2:15 | Agent reads the code, explains it | It says the code and what Samsung's page recommends, one step at a time. |
| 2:35 | (Optional) move the camera away or blur it, then ask again | It should say the display is unclear and ask you to read it or move closer. It never guesses a code. |
| 2:50 | Speak with a self-correction | *"I tried that already. Can you book a technician for Friday morning — no wait, Saturday morning."* |
| 3:10 | Agent confirms | Saturday morning, with a booking reference. |
| 3:20 | *"Did that go through?"* | It checks the booking list and answers from it. |
| 3:30 | Terminal: `python -m keel.console traces/show_and_fix/*.jsonl -o sf.html`, open it | Show the Friday booking held behind the fence and cancelled before it was sent, the Saturday booking sent once, and the `perception` note with the vision confidence. |

## Close (about 0:20)

| Time | Show | Say |
|---|---|---|
| 3:50 | README architecture diagram | "The LLM decides what to call; Keel decides whether and when it is safe to run. Everything you saw is in the trace, and the benchmark run is one command: `scripts/reproduce_fdb_v3.sh`." |

## If something goes wrong on camera

- The agent answers from memory instead of calling a tool: say so, and move on.
  An honest take beats a re-edit; the guide prefers unedited takes.
- No camera frame: the agent says "No recent camera frame". Show it, turn the
  camera on and ask again. That is Keel refusing to guess, which is worth showing.
