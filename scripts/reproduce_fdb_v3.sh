#!/usr/bin/env bash
# One-command reproduction: run Full-Duplex-Bench v3 against Keel, end to end.
#
#   scripts/reproduce_fdb_v3.sh                       # full run: 100 recordings, 3 evaluations
#   scripts/reproduce_fdb_v3.sh --example travel_10   # smoke test on one recorded self-correction scenario
#
# What it does (Theme 05 guide §4: "install, configure, evaluate"):
#   1. checks prerequisites and the API keys in .env (never prints them)
#   2. creates two virtualenvs from lock files: the Keel agent and FDB-v3's scripts
#   3. clones FDB-v3 at a pinned commit and downloads its audio (link from its v3/README.md)
#   4. downloads the agent's model weights (VAD, end-of-utterance model)
#   5. starts Keel's LiveKit agent, runs FDB-v3's own inference script, stops the agent
#   6. runs FDB-v3's three evaluations with the gpt-4o judge (--use-llm)
#   7. collects reports, per-scenario results, logs, traces, versions and seeds into
#      results/fdb_v3/<timestamp>_<provider>/
#
# Model provider (declared, guide §4): OpenAI, through LiveKit Cloud.
#   cascaded (default): Silero VAD + LiveKit end-of-utterance model (local) +
#                       OpenAI whisper-1 STT + gpt-4o + tts-1, with Keel under every tool call
#   gpt_realtime:       OpenAI gpt-realtime-1.5, with Keel under every tool call
# Keys (in .env at the repo root; see .env.example):
#   LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET, OPENAI_API_KEY
#
# Needs: Linux or macOS, bash, git, ffmpeg, Python 3.10 (FDB-v3's README uses 3.10; set PYTHON=...).
# FDB-v3's ASR model (NeMo parakeet) uses the GPU when one is available.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FDB_REPO="https://github.com/DanielLin94144/Full-Duplex-Bench.git"
FDB_COMMIT="3e799c45a045256f47d5f1c9cda90157e2d2ec9e"      # main, 2026-05-20
FDB_DATA_ID="1SO_4MTazWQ_jvCx0dtmpQ-t40bdd07yz"            # Google Drive id in FDB v3/README.md "Data"
FDB_DIR="$ROOT/third_party/Full-Duplex-Bench"
V3="$FDB_DIR/v3"
PY="${PYTHON:-python3.10}"

PIPELINE="cascaded"
LATENCY="instant"
EXAMPLE=""
SKIP_INSTALL=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --pipeline) PIPELINE="$2"; shift 2 ;;
    --latency) LATENCY="$2"; shift 2 ;;
    --example) EXAMPLE="$2"; shift 2 ;;
    --skip-install) SKIP_INSTALL=1; shift ;;
    -h|--help) sed -n '2,28p' "$0"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done
case "$PIPELINE" in cascaded|gpt_realtime) ;; *) echo "--pipeline must be cascaded or gpt_realtime" >&2; exit 2 ;; esac
PROVIDER="keel_${PIPELINE}"

say() { printf '\n==> %s\n' "$*"; }
die() { printf '\nERROR: %s\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------- 1. prerequisites
say "Checking prerequisites"
for bin in git ffmpeg "$PY"; do
  command -v "$bin" >/dev/null 2>&1 || die "'$bin' not found (set PYTHON=... for a different Python 3.10)"
done
"$PY" -c 'import sys; assert sys.version_info[:2] >= (3, 10) and sys.version_info[:2] < (3, 13), sys.version' \
  || die "Python 3.10-3.12 required"
[[ -f "$ROOT/.env" ]] || die "no .env at the repo root; copy .env.example to .env and fill it in"
set -a; # shellcheck disable=SC1091
source "$ROOT/.env"; set +a
for key in LIVEKIT_URL LIVEKIT_API_KEY LIVEKIT_API_SECRET OPENAI_API_KEY; do
  [[ -n "${!key:-}" ]] || die "$key is empty in .env"
done
echo "keys present: LIVEKIT_URL LIVEKIT_API_KEY LIVEKIT_API_SECRET OPENAI_API_KEY (values not shown)"

# ---------------------------------------------------------------- 2. environments
# Large wheels (torch, triton: hundreds of MB) must survive a flaky connection.
export PIP_RETRIES="${PIP_RETRIES:-10}" PIP_RESUME_RETRIES="${PIP_RESUME_RETRIES:-10}" PIP_TIMEOUT="${PIP_TIMEOUT:-60}"
AGENT_PY="$ROOT/.venv-agent/bin/python"
BENCH_PY="$ROOT/.venv-bench/bin/python"
if [[ $SKIP_INSTALL -eq 0 ]]; then
  say "Creating the agent environment (.venv-agent) from requirements/agent.lock.txt"
  [[ -x "$AGENT_PY" ]] || "$PY" -m venv "$ROOT/.venv-agent"
  "$AGENT_PY" -m pip install -q --upgrade pip
  "$AGENT_PY" -m pip install -q -r "$ROOT/requirements/agent.lock.txt"
  "$AGENT_PY" -m pip install -q --no-deps -e "$ROOT"
  say "Creating the benchmark environment (.venv-bench) from requirements/bench.lock.txt"
  [[ -x "$BENCH_PY" ]] || "$PY" -m venv "$ROOT/.venv-bench"
  "$BENCH_PY" -m pip install -q --upgrade pip
  "$BENCH_PY" -m pip install -q -r "$ROOT/requirements/bench.lock.txt"
fi

# Fail fast: every import the agent and FDB-v3's scripts make, before the long steps.
say "Checking both environments"
agent_imports="import livekit.agents, livekit.plugins.openai, livekit.plugins.silero, livekit.plugins.turn_detector, dotenv, keel.livekit.session"
bench_imports="from livekit import api, rtc; import numpy, dotenv, pydub, openai, gdown, nemo.collections.asr"
"$AGENT_PY" -c "$agent_imports" || die "agent environment is incomplete; rerun without --skip-install"
"$BENCH_PY" -c "$bench_imports" || die "benchmark environment is incomplete; rerun without --skip-install"

# ---------------------------------------------------------------- 3. benchmark code + data
say "FDB-v3 at $FDB_COMMIT"
if [[ ! -d "$FDB_DIR/.git" ]]; then
  git clone -q "$FDB_REPO" "$FDB_DIR"
fi
git -C "$FDB_DIR" fetch -q origin "$FDB_COMMIT" 2>/dev/null || true
git -C "$FDB_DIR" checkout -q "$FDB_COMMIT"
[[ "$(git -C "$FDB_DIR" rev-parse HEAD)" == "$FDB_COMMIT" ]] || die "FDB-v3 checkout is not at $FDB_COMMIT"

if [[ ! -d "$V3/fdb_v3_data_released" ]]; then
  say "Downloading the FDB-v3 audio (about 740 MB)"
  "$BENCH_PY" -m gdown "$FDB_DATA_ID" -O "$FDB_DIR/fdb_v3_data.zip"
  "$BENCH_PY" -m zipfile -e "$FDB_DIR/fdb_v3_data.zip" "$V3"
  rm -rf "$V3/__MACOSX"
fi
n_inputs=$(find "$V3/fdb_v3_data_released" -name input.wav | wc -l | tr -d ' ')
[[ "$n_inputs" -eq 100 ]] || die "expected 100 input.wav files, found $n_inputs"
echo "audio: $n_inputs recordings"

if [[ -n "$EXAMPLE" ]]; then
  # 79 of the 100 scenarios in benchmark_data_v2.json have recordings; the runner
  # silently processes nothing for one that has none.
  compgen -G "$V3/fdb_v3_data_released/${EXAMPLE}_*" >/dev/null     || die "no recording for scenario '$EXAMPLE' in fdb_v3_data_released/ (folders are <scenario>_<speaker>)"
fi

# FDB-v3's scripts read their keys from v3/.env.local (load_dotenv(".env.local")).
cp "$ROOT/.env" "$V3/.env.local"

# FDB-v3's runner loads its ASR model from Hugging Face on first use; a failed
# download there ends the run. Fetch it first, with retries (later attempts
# skip the Xet transfer path and use plain HTTP). The model name is the runner's own.
ASR_MODEL="$(sed -n 's/^ASR_MODEL_NAME = "\(.*\)".*/\1/p' "$V3/run_tool_benchmark.py")"
[[ -n "$ASR_MODEL" ]] || die "could not read ASR_MODEL_NAME from run_tool_benchmark.py"
say "Downloading FDB-v3's ASR model ($ASR_MODEL)"
for attempt in 1 2 3 4; do
  if [[ $attempt -gt 1 ]]; then export HF_HUB_DISABLE_XET=1; echo "retry $attempt (plain HTTP)"; fi
  "$BENCH_PY" -c "import sys; from huggingface_hub import snapshot_download; snapshot_download(sys.argv[1])" "$ASR_MODEL"     && break
  [[ $attempt -eq 4 ]] && die "could not download $ASR_MODEL from Hugging Face"
  sleep 5
done

# ---------------------------------------------------------------- 4-5. agent + inference
RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)_${PROVIDER}${EXAMPLE:+_$EXAMPLE}"
OUT="$ROOT/results/fdb_v3/$RUN_ID"
mkdir -p "$OUT"
START_EPOCH="$(date +%s)"

say "Downloading agent model weights"
KEEL_FDB_DIR="$V3" KEEL_PIPELINE="$PIPELINE" "$AGENT_PY" -m keel.livekit.agent download-files >"$OUT/download_files.log" 2>&1

say "Starting Keel's agent (pipeline=$PIPELINE, latency=$LATENCY)"
KEEL_FDB_DIR="$V3" KEEL_PIPELINE="$PIPELINE" "$AGENT_PY" -m keel.livekit.agent start --latency "$LATENCY" \
  >"$OUT/agent.log" 2>&1 &
AGENT_PID=$!
cleanup() { kill "$AGENT_PID" 2>/dev/null || true; wait "$AGENT_PID" 2>/dev/null || true; }
trap cleanup EXIT
for _ in $(seq 1 120); do
  grep -q "registered worker" "$OUT/agent.log" && break
  kill -0 "$AGENT_PID" 2>/dev/null || die "agent exited during start-up; see $OUT/agent.log"
  sleep 1
done
grep -q "registered worker" "$OUT/agent.log" || die "agent did not register with LiveKit within 120 s; see $OUT/agent.log"
echo "agent registered with LiveKit"

say "Running FDB-v3 inference (provider label: $PROVIDER)"
cd "$V3"
if [[ -n "$EXAMPLE" ]]; then
  "$BENCH_PY" run_tool_benchmark.py --provider "$PROVIDER" --example "$EXAMPLE" --force 2>&1 | tee "$OUT/inference.log"
else
  "$BENCH_PY" run_tool_benchmark_all_released.py --provider "$PROVIDER" --force 2>&1 | tee "$OUT/inference.log"
fi
cleanup
trap - EXIT

# ---------------------------------------------------------------- 6. evaluation
# Each evaluation is non-fatal, as in FDB-v3's own run_all_evaluations_released.sh,
# so one failing step still leaves the others and the collected logs.
say "Evaluating (gpt-4o judge)"
"$BENCH_PY" evaluate_tool_calls.py --benchmark benchmark_data_v2.json --results-dir fdb_v3_data_released \
  --provider "$PROVIDER" --output "$OUT/${PROVIDER}_evaluation_report.json" --use-llm 2>&1 | tee "$OUT/eval_tool_calls.log" || echo "WARNING: this evaluation failed; see $OUT/eval_tool_calls.log"
"$BENCH_PY" evaluate_pass_rate.py --benchmark benchmark_data_v2.json --results-dir fdb_v3_data_released \
  --provider "$PROVIDER" --output "$OUT/${PROVIDER}_pass_rate_report.json" --use-llm 2>&1 | tee "$OUT/eval_pass_rate.log" || echo "WARNING: this evaluation failed; see $OUT/eval_pass_rate.log"
"$BENCH_PY" analyze_tool_latency.py --results-dir fdb_v3_data_released --provider "$PROVIDER" \
  --output "$OUT/${PROVIDER}_latency_report.json" 2>&1 | tee "$OUT/eval_latency.log" || echo "WARNING: this evaluation failed; see $OUT/eval_latency.log"

# ---------------------------------------------------------------- 7. collect
say "Collecting results into $OUT"
mkdir -p "$OUT/per_scenario" "$OUT/traces"
find fdb_v3_data_released -name "result_${PROVIDER}.json" -newermt "@$START_EPOCH" | while read -r f; do
  cp "$f" "$OUT/per_scenario/$(basename "$(dirname "$f")").json"
done
"$AGENT_PY" - "$OUT" <<'PY'
# This run's lines from the tool log FDB-v3 scores (other runs' rooms are left out).
import json, sys
from pathlib import Path
out = Path(sys.argv[1])
results = [json.loads(p.read_text()) for p in (out / "per_scenario").glob("*.json")]
rooms = {r["room_name"] for r in results if r.get("room_name")}
log = Path("/tmp/agent_tool_calls.log")
lines = [l for l in log.read_text().splitlines() if l.strip() and json.loads(l).get("room") in rooms] if log.exists() else []
(out / "agent_tool_calls.jsonl").write_text("".join(l + chr(10) for l in lines))
PY
# Keel's per-room traces written during this run (cp would reset their times, so filter first).
find "$ROOT/traces/livekit" -name 'eval-*.jsonl' -newermt "@$START_EPOCH" -exec cp {} "$OUT/traces/" \; 2>/dev/null || true
cp "$ROOT/config/keel.toml" "$ROOT/config/fdb_v3.toml" "$OUT/"
{
  echo "run_id: $RUN_ID"
  echo "command: $0 --pipeline $PIPELINE --latency $LATENCY${EXAMPLE:+ --example $EXAMPLE}"
  echo "provider_label: $PROVIDER"
  echo "keel_commit: $(git -C "$ROOT" rev-parse HEAD 2>/dev/null || echo unknown)$(git -C "$ROOT" diff --quiet 2>/dev/null || echo ' (uncommitted changes)')"
  echo "fdb_v3_commit: $FDB_COMMIT"
  echo "seeds: $(grep -E '^(seed|llm_seed) *=' "$ROOT/config/fdb_v3.toml" | tr -s ' ' | paste -sd ';' -)"
  echo "python: $("$AGENT_PY" -V 2>&1)"
  echo "os: $(uname -a)"
  command -v nvidia-smi >/dev/null && echo "gpu: $(nvidia-smi --query-gpu=name,driver_version --format=csv,noheader | paste -sd ';' -)"
} > "$OUT/run_info.txt"
"$AGENT_PY" -m pip freeze > "$OUT/pip_freeze_agent.txt"
"$BENCH_PY" -m pip freeze > "$OUT/pip_freeze_bench.txt"
"$AGENT_PY" -m keel.console "$OUT"/traces/*.jsonl -o "$OUT/console.html" >/dev/null 2>&1 || true

"$AGENT_PY" - "$OUT" "$PROVIDER" <<'PY'
import json, sys
from pathlib import Path
out, provider = Path(sys.argv[1]), sys.argv[2]
def load(name):
    p = out / f"{provider}_{name}_report.json"
    return json.loads(p.read_text()) if p.exists() else {}
pr, ev = load("pass_rate"), load("evaluation")
print("\n================ FDB-v3 results:", provider, "================")
if pr:
    print(f"  pass rate (strict) : {pr.get('overall_pass_rate')}  ({pr.get('passed')}/{pr.get('total_scenarios')})")
for k, v in (ev.get("by_metric") or {}).items():
    if k != "note":
        print(f"  {k:23}: {v}")
tt = ev.get("turn_taking") or {}
if tt:
    print(f"  turn_take_rate         : {tt.get('turn_take_rate')}")
print(f"  full reports       : {out}")
PY
