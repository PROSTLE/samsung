# Local run of `--pipeline open`: every model on this machine

- faster-whisper small.en and Kokoro-82M through `keel.speech` (CPU), Qwen3-4B-Instruct-2507
  through Ollama 0.34.4; the default `config/fdb_v3.toml`, no model key. Scored with
  `--judge none` (exact-match arguments).
- FDB-v3 logged one call, `search_flights(Miami, 2026-10-07)`: the corrected one only.
- The agent's spoken answer came after FDB-v3's 47.6 s recording ended, so the output
  transcript is empty and turn-taking scores 0. Why: the laptop ran on battery; its GPU
  measured 17 GB/s memory bandwidth and Ollama generated 7 tokens/s, with 11 of the
  model's 37 layers on the CPU (4 GB card shared with the desktop). LiveKit's default per-request deadline (10 s, then a retry) was still in force: one LLM request timed out at 10.0 s and started again.
- FDB-v3's `evaluate_pass_rate.py` crashes after writing its report when no scenario is
  without rollback (it formats `None`, its line 579); upstream.
