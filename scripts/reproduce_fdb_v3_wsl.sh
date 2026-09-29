#!/usr/bin/env bash
# Run scripts/reproduce_fdb_v3.sh from a Windows checkout, inside WSL.
#
#   PS C:\...\samsung> wsl bash scripts/reproduce_fdb_v3_wsl.sh --example travel_10
#   PS C:\...\samsung> wsl bash scripts/reproduce_fdb_v3_wsl.sh
#
# Same options as reproduce_fdb_v3.sh. The benchmark needs Linux (LiveKit starts
# its job processes with forkserver there, FDB-v3's scripts write to /tmp), and a
# run from /mnt/c is slow, so this copies the checkout (with .env) to the WSL home
# (KEEL_WSL_DIR, default ~/keel), runs it there, and copies the run's folder back
# to results/fdb_v3/ in the Windows checkout. Environments and the FDB-v3 checkout
# stay in the WSL copy between runs.

set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DST="${KEEL_WSL_DIR:-$HOME/keel}"
case "$SRC" in /mnt/*) ;; *) echo "run this from the Windows checkout, through wsl (see the top of this file)" >&2; exit 2 ;; esac
command -v rsync >/dev/null 2>&1 || { echo "rsync not found: sudo apt install rsync" >&2; exit 1; }
# `wsl bash` is not a login shell, so ~/.profile has not added the per-user bin
# directories (where uv puts python3.10, for example). Add them as it would.
for d in "$HOME/.local/bin" "$HOME/bin"; do
  if [[ -d "$d" ]]; then PATH="$d:$PATH"; fi
done
export PATH

echo "==> Copying $SRC to $DST"
mkdir -p "$DST"
# Excluded paths are neither copied nor deleted: the WSL copy keeps its own
# environments, FDB-v3 checkout, results and traces.
rsync -a --delete \
  --exclude=/.venv/ --exclude=/.venv-agent/ --exclude=/.venv-bench/ --exclude=/.venv-speech/ --exclude=/third_party/ \
  --exclude=/results/ --exclude=/traces/ --exclude=/reports/ --exclude='*.egg-info/' \
  --exclude=__pycache__/ --exclude=.pytest_cache/ --exclude=.hypothesis/ \
  --exclude=/txt.txt --exclude='/*.docx' --exclude='/*.pptx' \
  "$SRC/" "$DST/"
mkdir -p "$DST/results/fdb_v3"
before="$(ls -1 "$DST/results/fdb_v3")"

status=0
cd "$DST"
bash "$DST/scripts/reproduce_fdb_v3.sh" "$@" || status=$?

for run in $(ls -1 "$DST/results/fdb_v3"); do
  if ! grep -qxF "$run" <<<"$before"; then
    mkdir -p "$SRC/results/fdb_v3"
    rsync -a "$DST/results/fdb_v3/$run" "$SRC/results/fdb_v3/"
    echo "==> Copied results/fdb_v3/$run to the Windows checkout"
  fi
done
exit "$status"
