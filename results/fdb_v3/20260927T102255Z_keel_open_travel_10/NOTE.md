# Local run of `--pipeline open`, plugged in: every model on this machine

- faster-whisper small.en and Kokoro-82M through `keel.speech` (CPU), Qwen3-4B-Instruct-2507
  through Ollama 0.34.4 with `KEEL_OLLAMA_CONTEXT=4096`; the default `config/fdb_v3.toml`,
  no model key. Scored with `--judge none` (exact-match arguments).
- On AC power the laptop's GPU ran at full clock: Ollama processed prompts at 1,168 tokens/s
  and generated 21 tokens/s (6-7 on battery, runs 20260927T053340Z and T054338Z).
- FDB-v3 logged one call, `search_flights(Miami, 2026-10-07)`; the model planned it 2.4 s
  after the corrected turn; the agent spoke at 38.9 s, inside the 47.6 s recording:
  turn-taking 1.0, tool selection 1.0. It fails only the exact-match date check
  ("2026-10-07" where the benchmark expects "October 7").
- FDB-v3's `evaluate_pass_rate.py` crashes after writing its report when no scenario is
  without rollback (it formats `None`, its line 579); upstream.
