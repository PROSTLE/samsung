#!/usr/bin/env bash
# The open-weight model servers behind the "open" pipeline, on this machine.
#
#   scripts/open_models.sh install    # pinned downloads: Ollama, the LLM, the speech models (idempotent)
#   scripts/open_models.sh start      # install if needed, start what [livekit.open] uses, wait until it answers
#   scripts/open_models.sh status
#   scripts/open_models.sh stop
#
# Which servers: the local providers that [livekit.open] (and, for Show & Fix,
# [livekit.show_and_fix.vision].open) name in config/keel.toml's [providers]:
#   ollama    Ollama serving an open-weight LLM (and a vision model for Show & Fix)
#   speaches  Keel's OpenAI-compatible speech server (python -m keel.speech):
#             faster-whisper for speech-to-text, Kokoro for text-to-speech
# A stage served by a hosted provider (e.g. groq) starts nothing here.
# Binaries and models live under third_party/, Python packages in .venv-speech,
# logs and pids in traces/open_models/. No key, no account, no sudo.
#
# Environment: KEEL_CONFIG (profile, default config/fdb_v3.toml), PYTHON (3.10).
# Needs: Linux x86-64, curl, Python 3.10, and .venv-agent (made by reproduce_fdb_v3.sh).
# The LLM uses an NVIDIA GPU when there is one (4 GB is enough for the default model).

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
PY="${PYTHON:-python3.10}"
AGENT_PY="$ROOT/.venv-agent/bin/python"
SPEECH_PY="$ROOT/.venv-speech/bin/python"
RUN="$ROOT/traces/open_models"
MODELS="$ROOT/third_party/models"
OLLAMA_DIR="$ROOT/third_party/ollama"
export KEEL_CONFIG="${KEEL_CONFIG:-$ROOT/config/fdb_v3.toml}"

# Pins. Ollama: github.com/ollama/ollama/releases. Kokoro-82M (Apache-2.0) as
# ONNX: github.com/thewh1teagle/kokoro-onnx/releases/tag/model-files-v1.1.
# faster-whisper models: huggingface.co/Systran (MIT, converted from OpenAI Whisper).
OLLAMA_VERSION="0.34.4"
OLLAMA_SHA256="c238986e61d40c0cc5f4a9b9e40b9eea104350b77efa34741fc134e105cb9533"   # the release's own sha256sum.txt
KOKORO_URL="https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.1"
declare -A FILE_SHA256=(
  [kokoro-v1.0.onnx]="beb0d1848dee9a49da392cc3df26958d46cfa35d321edf434f52949153f0df3a"
  [voices-v1.0.bin]="bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d"
)
declare -A WHISPER_REVISION=(
  [Systran/faster-whisper-small.en]="d1d751a5f8271d482d14ca55d9e2deeebbae577f"
)

say() { printf '==> %s\n' "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
sha_ok() { [[ "$(sha256sum "$1" | cut -d' ' -f1)" == "$2" ]]; }

[[ -x "$AGENT_PY" ]] || die "no .venv-agent: run scripts/reproduce_fdb_v3.sh once (or create it from requirements/agent.lock.txt)"

# What the configured open pipeline needs from local servers, from the config itself.
eval "$(KEEL_PIPELINE=open "$AGENT_PY" - <<'PY'
import os, shlex
from urllib.parse import urlparse
from keel.config import load_config
cfg = load_config(None, os.environ["KEEL_CONFIG"])
lk = cfg.livekit
o = lk.open if lk else None
if o is None:
    raise SystemExit(f"{os.environ['KEEL_CONFIG']} has no [livekit.open] section")
ollama = [o.llm_model] if o.llm_provider == "ollama" else []
if lk.show_and_fix is not None:
    provider, model = lk.show_and_fix.vision_for("open")
    if provider == "ollama" and model not in ollama:
        ollama.append(model)
def port(name, default):
    p = cfg.providers.get(name)
    return urlparse(p.base_url).port or default if p else default
out = {
    "OLLAMA_MODELS_LIST": " ".join(ollama),
    "OLLAMA_PORT": port("ollama", 11434),
    "STT_MODEL": o.stt_model if o.stt_provider == "speaches" else "",
    "TTS_MODEL": o.tts_model if o.tts_provider == "speaches" else "",
    "SPEECH_PORT": port("speaches", 8000),
}
for k, v in out.items():
    print(f"{k}={shlex.quote(str(v))}")
PY
)"
NEED_OLLAMA=$([[ -n "$OLLAMA_MODELS_LIST" ]] && echo 1 || echo 0)
NEED_SPEECH=$([[ -n "$STT_MODEL$TTS_MODEL" ]] && echo 1 || echo 0)

# ---------------------------------------------------------------- install
speech_env() {
  if [[ ! -x "$SPEECH_PY" ]]; then
    say "Creating .venv-speech from requirements/speech.lock.txt"
    "$PY" -m venv "$ROOT/.venv-speech"
  fi
  "$SPEECH_PY" -c "import faster_whisper, kokoro_onnx, aiohttp, zstandard" 2>/dev/null && return
  # A venv made by uv has no pip; the standard library can add it.
  "$SPEECH_PY" -m pip --version >/dev/null 2>&1 || "$SPEECH_PY" -m ensurepip >/dev/null
  "$SPEECH_PY" -m pip install -q --upgrade pip
  "$SPEECH_PY" -m pip install -q -r "$ROOT/requirements/speech.lock.txt"
}

fetch() {  # fetch <url> <file>: download once, resumable, then check its pinned sha256
  local url="$1" file="$2" name
  name="$(basename "$file")"
  if [[ ! -f "$file" ]]; then
    say "Downloading $name"
    curl -fL --retry 5 -C - -o "$file.part" "$url"
    mv "$file.part" "$file"
  fi
  local want="${FILE_SHA256[$name]:-}"
  if [[ -n "$want" ]] && ! sha_ok "$file" "$want"; then
    rm -f "$file"
    die "$name does not match its pinned sha256; deleted, run again to re-download"
  fi
}

install_ollama() {
  [[ -x "$OLLAMA_DIR/bin/ollama" ]] && return
  mkdir -p "$OLLAMA_DIR"
  local arc="$OLLAMA_DIR/ollama-linux-amd64-$OLLAMA_VERSION.tar.zst"
  if [[ ! -f "$arc" ]]; then
    say "Downloading Ollama $OLLAMA_VERSION (about 1.4 GB)"
    curl -fL --retry 5 -C - -o "$arc.part" \
      "https://github.com/ollama/ollama/releases/download/v$OLLAMA_VERSION/ollama-linux-amd64.tar.zst"
    mv "$arc.part" "$arc"
  fi
  sha_ok "$arc" "$OLLAMA_SHA256" || { rm -f "$arc"; die "Ollama archive does not match its pinned sha256; deleted, run again"; }
  say "Unpacking Ollama into third_party/ollama"
  "$SPEECH_PY" -c "import sys, zstandard; zstandard.ZstdDecompressor().copy_stream(open(sys.argv[1], 'rb'), sys.stdout.buffer)" \
    "$arc" | tar -x -C "$OLLAMA_DIR"
  [[ -x "$OLLAMA_DIR/bin/ollama" ]] || die "no bin/ollama in the Ollama archive"
}

install() {
  mkdir -p "$MODELS" "$RUN"
  speech_env
  if [[ "$NEED_SPEECH" == 1 && -n "$TTS_MODEL" ]]; then
    fetch "$KOKORO_URL/$TTS_MODEL.onnx" "$MODELS/$TTS_MODEL.onnx"
    fetch "$KOKORO_URL/voices-v1.0.bin" "$MODELS/voices-v1.0.bin"
  fi
  if [[ "$NEED_SPEECH" == 1 && -n "$STT_MODEL" ]]; then
    local get="import sys; from huggingface_hub import snapshot_download; snapshot_download(sys.argv[1], revision=sys.argv[2] or None, cache_dir=sys.argv[3])"
    local rev="${WHISPER_REVISION[$STT_MODEL]:-}"
    # From the local cache when it is there (works offline), else from Hugging Face.
    if ! HF_HUB_OFFLINE=1 "$SPEECH_PY" -c "$get" "$STT_MODEL" "$rev" "$MODELS/whisper" >/dev/null 2>&1; then
      say "Fetching $STT_MODEL"
      "$SPEECH_PY" -c "$get" "$STT_MODEL" "$rev" "$MODELS/whisper" >/dev/null
    fi
  fi
  if [[ "$NEED_OLLAMA" == 1 ]]; then
    install_ollama
    local was_running=1
    ollama_up || { was_running=0; start_ollama; }
    for m in $OLLAMA_MODELS_LIST; do
      # Already here: no registry request, so a start works offline.
      OLLAMA_HOST="127.0.0.1:$OLLAMA_PORT" "$OLLAMA_DIR/bin/ollama" show "$m" >/dev/null 2>&1 && continue
      say "Pulling $m"
      OLLAMA_HOST="127.0.0.1:$OLLAMA_PORT" "$OLLAMA_DIR/bin/ollama" pull "$m" >>"$RUN/ollama_pull.log" 2>&1 \
        || die "could not pull $m; see $RUN/ollama_pull.log"
    done
    [[ $was_running == 1 ]] || stop_one ollama
  fi
}

# ---------------------------------------------------------------- run
ollama_up() { curl -sf "http://127.0.0.1:$OLLAMA_PORT/api/version" >/dev/null 2>&1; }
speech_up() { curl -sf "http://127.0.0.1:$SPEECH_PORT/health" >/dev/null 2>&1; }

wait_for() {  # wait_for <name> <check function> <seconds>
  for _ in $(seq 1 "$3"); do "$2" && return 0; sleep 1; done
  die "$1 did not answer within $3 s; see $RUN/$1.log"
}

start_ollama() {
  mkdir -p "$RUN" "$OLLAMA_DIR/models"
  # 8192 tokens holds FDB-v3's instructions and 12 tool schemas (about 1,850 tokens)
  # and a whole conversation; a quantised KV cache (needs flash attention) keeps it
  # small; keep_alive -1: the model stays loaded between scenarios. On a 4 GB laptop
  # GPU that also drives the desktop, Ollama then keeps 11 of the 4B model's 37 layers
  # on the CPU (measured); KEEL_OLLAMA_CONTEXT=4096 lets all of it fit.
  OLLAMA_HOST="127.0.0.1:$OLLAMA_PORT" OLLAMA_MODELS="$OLLAMA_DIR/models" OLLAMA_CONTEXT_LENGTH="${KEEL_OLLAMA_CONTEXT:-8192}" \
  OLLAMA_FLASH_ATTENTION=1 OLLAMA_KV_CACHE_TYPE=q8_0 OLLAMA_KEEP_ALIVE=-1 \
    nohup "$OLLAMA_DIR/bin/ollama" serve >"$RUN/ollama.log" 2>&1 &
  echo $! >"$RUN/ollama.pid"
  wait_for ollama ollama_up 60
}

start_speech() {
  mkdir -p "$RUN"
  local args=(--port "$SPEECH_PORT")
  if [[ -n "$STT_MODEL" ]]; then
    args+=(--whisper "$STT_MODEL" --models-dir "$MODELS/whisper")
    [[ -n "${WHISPER_REVISION[$STT_MODEL]:-}" ]] && args+=(--whisper-revision "${WHISPER_REVISION[$STT_MODEL]}")
  fi
  [[ -n "$TTS_MODEL" ]] && args+=(--kokoro-model "$MODELS/$TTS_MODEL.onnx" --kokoro-voices "$MODELS/voices-v1.0.bin")
  PYTHONPATH="$ROOT" HF_HUB_OFFLINE=1 nohup "$SPEECH_PY" -m keel.speech "${args[@]}" >"$RUN/speech.log" 2>&1 &
  echo $! >"$RUN/speech.pid"
  wait_for speech speech_up 180
}

stop_one() {
  local pidfile="$RUN/$1.pid"
  [[ -f "$pidfile" ]] || return 0
  kill "$(cat "$pidfile")" 2>/dev/null || true
  rm -f "$pidfile"
}

start() {
  install
  if [[ "$NEED_OLLAMA" == 1 ]]; then
    ollama_up || start_ollama
    for m in $OLLAMA_MODELS_LIST; do
      # Load it and run one real chat request now: loading alone left the first
      # chat request 65 s slower (measured on the 4 GB laptop GPU), long enough
      # to lose a benchmark scenario.
      say "Warming up $m"
      curl -sf --max-time 600 "http://127.0.0.1:$OLLAMA_PORT/api/chat" \
        -d "{\"model\": \"$m\", \"messages\": [{\"role\": \"user\", \"content\": \"Reply with OK.\"}], \"stream\": false, \"keep_alive\": -1, \"options\": {\"num_predict\": 1}}" >/dev/null \
        || die "Ollama could not run $m; see $RUN/ollama.log"
    done
    say "Ollama on 127.0.0.1:$OLLAMA_PORT ($OLLAMA_MODELS_LIST)"
  fi
  if [[ "$NEED_SPEECH" == 1 ]]; then
    speech_up || start_speech
    say "Speech server on 127.0.0.1:$SPEECH_PORT (stt: ${STT_MODEL:-none}, tts: ${TTS_MODEL:-none})"
  fi
  [[ "$NEED_OLLAMA$NEED_SPEECH" != "00" ]] || say "[livekit.open] uses no local server; nothing to start"
}

status() {
  if [[ "$NEED_OLLAMA" == 1 ]]; then
    if ollama_up; then echo "ollama: up on $OLLAMA_PORT"; else echo "ollama: down"; fi
  fi
  if [[ "$NEED_SPEECH" == 1 ]]; then
    if speech_up; then echo "speech: up on $SPEECH_PORT"; else echo "speech: down"; fi
  fi
}

case "${1:-}" in
  install) install ;;
  start) start ;;
  stop) stop_one speech; stop_one ollama ;;
  status) status ;;
  *) sed -n '2,20p' "$0"; exit 2 ;;
esac
