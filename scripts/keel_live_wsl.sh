#!/usr/bin/env bash
# Start Keel's web app, and an agent to talk to, from a Windows checkout inside WSL.
#
#   PS C:\...\samsung> wsl bash scripts/keel_live_wsl.sh                   # benchmark agent + web app
#   PS C:\...\samsung> wsl bash scripts/keel_live_wsl.sh --show-and-fix    # Show & Fix agent + web app
#   PS C:\...\samsung> wsl bash scripts/keel_live_wsl.sh --no-agent        # web app only (sessions, results)
#   PS C:\...\samsung> wsl bash scripts/keel_live_wsl.sh --pipeline gemini_realtime   # free: a Gemini API key only
#   PS C:\...\samsung> wsl bash scripts/keel_live_wsl.sh --pipeline open   # open-weight models on this machine
#
# --pipeline sets KEEL_PIPELINE for the agent (default: the profile's own, cascaded
# on OpenAI). With open, scripts/open_models.sh starts the local model servers first.
#
# Then open http://localhost:8765. Ctrl+C stops both.
# Uses the WSL copy and environment that scripts/reproduce_fdb_v3_wsl.sh made
# (KEEL_WSL_DIR, default ~/keel), so its traces and results are what the app shows.

set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DST="${KEEL_WSL_DIR:-$HOME/keel}"
AGENT="keel.livekit.agent"
PORT=8765
PIPELINE=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --show-and-fix) AGENT="extension.show_and_fix.agent"; shift ;;
    --no-agent) AGENT=""; shift ;;
    --port) PORT="$2"; shift 2 ;;
    --pipeline) PIPELINE="$2"; shift 2 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done
for d in "$HOME/.local/bin" "$HOME/bin"; do
  if [[ -d "$d" ]]; then PATH="$d:$PATH"; fi
done
PY="$DST/.venv-agent/bin/python"
[[ -x "$PY" ]] || { echo "no environment in $DST: run 'wsl bash scripts/reproduce_fdb_v3_wsl.sh --example travel_10' once first" >&2; exit 1; }

# A benchmark run in progress uses the same LiveKit project; a second agent
# would be dispatched to its rooms, and new code must not reach its job processes.
if pgrep -f "keel.livekit.agent start" >/dev/null; then
  if [[ -n "$AGENT" ]]; then
    echo "A benchmark run is in progress; not starting an agent (it would join the benchmark's rooms)." >&2
    echo "Rerun with --no-agent to browse sessions and results meanwhile." >&2
    exit 1
  fi
  echo "==> A benchmark run is in progress: showing $DST as it is (no code copied)."
else
  echo "==> Copying $SRC to $DST"
  rsync -a --delete \
    --exclude=/.venv/ --exclude=/.venv-agent/ --exclude=/.venv-bench/ --exclude=/.venv-speech/ --exclude=/third_party/ \
    --exclude=/results/ --exclude=/traces/ --exclude=/reports/ --exclude='*.egg-info/' \
    --exclude=__pycache__/ --exclude=.pytest_cache/ --exclude=.hypothesis/ \
    --exclude=/txt.txt --exclude='/*.docx' \
    "$SRC/" "$DST/"
fi
cd "$DST"
export KEEL_FDB_DIR="${KEEL_FDB_DIR:-$DST/third_party/Full-Duplex-Bench/v3}"

pids=()
OPEN_STARTED=0
cleanup() {
  for p in "${pids[@]}"; do kill "$p" 2>/dev/null || true; done
  wait 2>/dev/null || true
  if [[ $OPEN_STARTED -eq 1 ]]; then bash "$DST/scripts/open_models.sh" stop || true; fi
}
trap cleanup EXIT INT TERM

if [[ -n "$PIPELINE" ]]; then export KEEL_PIPELINE="$PIPELINE"; fi
if [[ -n "$AGENT" && "$PIPELINE" == "open" ]]; then
  profile="$DST/config/fdb_v3.toml"
  [[ "$AGENT" == extension.show_and_fix.agent ]] && profile="$DST/config/show_and_fix.toml"
  KEEL_CONFIG="$profile" bash "$DST/scripts/open_models.sh" start
  OPEN_STARTED=1
fi

if [[ -n "$AGENT" ]]; then
  echo "==> Starting $AGENT (dev mode${PIPELINE:+, pipeline $PIPELINE}); its log is in $DST/traces/live_agent.log"
  mkdir -p traces
  "$PY" -m "$AGENT" dev >traces/live_agent.log 2>&1 &
  pids+=($!)
fi
echo "==> Web app: http://localhost:$PORT"
"$PY" -m keel.web --port "$PORT" --root "$DST" &
pids+=($!)
wait -n "${pids[@]}"
