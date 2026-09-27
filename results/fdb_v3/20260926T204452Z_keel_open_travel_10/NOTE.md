# Local verification run of `--pipeline open`

- Speech-to-text and text-to-speech: the local `keel.speech` server (faster-whisper
  small.en on the CPU, Kokoro-82M), as in `[livekit.open]`.
- LLM stage: `gemini-2.5-flash` through the Gemini API's free tier (`[providers.gemini]`),
  not the local Qwen3-4B, because Ollama's 1.4 GB download was still in progress. The
  profile used is `fdb_v3.toml` in this folder (`[livekit.open]` with
  `llm_provider = "gemini"`).
- Scored with `--judge none` (FDB-v3's rule-based scoring: exact-match arguments).
  FDB-v3's `evaluate_pass_rate.py` then crashes printing a `None` rate for a
  one-scenario run (its line 579); its report JSON is written before that.
- This run is before fence rule 5 (the Oct 5 search went out while the correction was still being transcribed).
