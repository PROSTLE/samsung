# Demo video script (3–5 minutes)

The guide (§3, step 5; §4) asks for one video: **a real interruption being
handled on the benchmark, then the extension use case in action**, preferring
"unedited single takes over polish". This is a shot list for one take of about
4 minutes, using Keel's web app and the free Gemini pipeline (no paid key). The
words to say are suggestions; say them naturally, with real hesitations. Every
step below was run on 2026-09-27 (`docs/reports/UI_AND_FREE_MODELS_2026-09-27.md`).

## Before recording (10 minutes, once)

1. `.env` has `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET` and `GOOGLE_API_KEY`.
   The WSL environments exist (`wsl bash scripts/reproduce_fdb_v3_wsl.sh --pipeline
   gemini_realtime --example travel_10` once).
2. **Run only one Keel agent at a time.** Both agents use LiveKit's automatic
   dispatch, so every running agent joins every room in the project.
3. For Part B, have the washer's display showing a code (or a clear photo of one on a
   second screen or on paper). A 5E or 4C code works well: Samsung's table has several
   steps for each.
4. Laptop plugged in; browser at http://localhost:8765 with microphone and camera allowed.
   Use headphones, so the agent does not hear itself.

## Part A: the benchmark agent handles a self-correction (about 1:45)

| Time | Show | Do / say |
|---|---|---|
| 0:00 | Terminal | "This is Keel, an execution layer under a LiveKit voice agent, evaluated on Full-Duplex-Bench v3." Run `wsl bash scripts/keel_live_wsl.sh --pipeline gemini_realtime`. |
| 0:15 | Web app, Overview | One sentence on the three rules (held, dropped, run once), with the numbers counted from recorded sessions. |
| 0:25 | Live demo → Start conversation | Say, with a real correction: *"Find me flights to Miami on October fifth. Oh, wait. Make it the seventh."* |
| 0:45 | The console | Point at the tool-call card: **Held** while you spoke, **Dropped** if the model planned the 5th early, then one search for the 7th **Executed**. The decision log says why at each step; the timeline shows it against your speech. |
| 1:15 | End session → Replay this session | Click a decision in the log: the replay jumps there. |
| 1:25 | Sessions → the finance_21 benchmark session ("Hesitant Multi-Task") | Press play: this is FDB-v3's own recording and the agent's recorded reply. At about 13 s the model plans `get_card_benefits` while the user is still talking; Keel drops it; both calls then run once. "FDB-v3 counts every executed call; that one never ran." |

## Part B: Show & Fix, the extension (about 2:00)

Stop the benchmark agent first (Ctrl+C), then run
`wsl bash scripts/keel_live_wsl.sh --show-and-fix --pipeline gemini_realtime`.

| Time | Show | Do / say |
|---|---|---|
| 1:45 | Terminal | "Now something beyond the benchmark: troubleshooting a Samsung washer through the camera." |
| 2:00 | Live demo, camera on, pointed at the display | *"My washing machine is showing an error, can you read it and tell me what it means?"* |
| 2:15 | The agent reads the code, explains it | The card for `read_error_display` shows the code; the agent says what Samsung's support page says, one step at a time. |
| 2:35 | (Optional) move the camera away, ask again | It says the display is unclear and asks you to read it or move closer. It never guesses a code. |
| 2:50 | Speak with a self-correction | *"Please book a technician for Friday morning. Oh, wait. No, make it Saturday morning."* |
| 3:10 | The agent confirms | One `book_technician` card: Saturday, morning, **Held** until you finished, then **Executed** once. |
| 3:20 | *"Did the booking go through?"* | A `list_technician_bookings` card: it answers from the booking list, not from memory. |

## Close (about 0:20)

| Time | Show | Say |
|---|---|---|
| 3:40 | Setup page, then the README diagram | "The LLM decides what to call; Keel decides whether and when it is safe to run. It runs on OpenAI, on Gemini's free tier, or entirely on open-weight models on this laptop, and the benchmark run is one command: `scripts/reproduce_fdb_v3.sh`." |

## If something goes wrong on camera

- The agent answers from memory instead of calling a tool: say so, and move on. An
  honest take beats a re-edit; the guide prefers unedited takes.
- No camera frame: the card says "No recent camera frame". Show it, turn the camera
  on and ask again. That is Keel refusing to guess, which is worth showing.
- The agent does not join: the Live page says so after 15 s. Check that the terminal
  shows "registered worker" and that no other agent is running.
