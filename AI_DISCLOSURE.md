# AI Disclosure

This document states where AI was used in building Keel and where AI runs inside it.

## How the project was built

Keel was built by the team with an AI coding assistant.

**By the team:**
- The idea and the problem framing: an execution layer that lets a voice agent's
  model plan early but acts only once the user has finished speaking.
- The system's logic and design: the hold / drop / run-once / truthful-outcome rules,
  the commit fence and its timings, how Keel sits between the LLM and its tools inside
  LiveKit Agents, and the Show & Fix extension.
- Code implementation, integration with LiveKit and Full-Duplex-Bench v3, running and
  checking the benchmark, and every decision on what to keep.
- The presentation.

**With AI assistance (Claude, Anthropic):**
- Help writing and refactoring parts of the code and tests.
- Help drafting and editing documentation, and condensing and formatting the final
  8-slide version of the presentation.
- Help scripting, recording and editing the demo video.

All AI-assisted work was reviewed, tested and integrated by the team. Every number
in this repository comes from our own runs or from the cited sources (`SOURCES.md`).

## AI models used at runtime

The agent is a voice assistant, so it uses AI models by design. Which ones depends on
the selected pipeline (`KEEL_PIPELINE`):

| Pipeline | Speech-to-text | Language model | Text-to-speech | Show & Fix display reader |
|---|---|---|---|---|
| `cascaded` | OpenAI whisper-1 | OpenAI gpt-4o | OpenAI tts-1 | gpt-4o |
| `gpt_realtime` | OpenAI gpt-realtime-1.5 (one model) | | | gpt-4o |
| `gemini_realtime` | Google Gemini Live, gemini-3.1-flash-live-preview (one model) | | | gemini-2.5-flash |
| `open` | faster-whisper small.en | Qwen3-4B-Instruct-2507 (Ollama) | Kokoro-82M | Qwen3-VL-2B |

All pipelines also use LiveKit's Silero voice-activity detection and its
end-of-utterance model. Keel's own decisions (hold, drop, reuse, report) are
deterministic rules, not model outputs.

## AI-generated media

The illustrations in the presentation are AI-generated and are labelled
"Illustration, AI-generated" on the slides. The washer display used to test Show & Fix
without a physical washer is a synthetic image and is labelled so.

In the demo video (`Keel_Demo.mp4`):
- The narration is synthesised speech (Kokoro-82M, run locally).
- The Show & Fix customer's voice is synthesised (Gemini text-to-speech); the video says so.
  The benchmark calls use Full-Duplex-Bench v3's own recorded human audio.
- The subtitles were transcribed with faster-whisper and corrected by hand.
- Every conversation shown is a real, unscripted run of the agent; only the caller's lines
  were prepared in advance.

## Evaluation integrity

No model was trained, fine-tuned or prompted on Full-Duplex-Bench v3's test items.
Keel's timings come from published conversation research, and its instructions are
general sentences that apply to any tool (`config/fdb_v3.toml`).
