#!/usr/bin/env bash
# Multilingual Qwen3-8B outcome experiment — tesla (192.168.2.43) run-kit.
#
# Prereqs verified 2026-07-15:
#   - Qwen3-8B fp16 cached at ~user/.cache/huggingface/hub/models--Qwen--Qwen3-8B
#   - Ready env: /home/user/repos/ml_venv (torch 2.7.0+cu126, transformers 4.57.3,
#     accelerate 1.13.0) loads Qwen3-8B config+tokenizer from cache.
#   - BLOCKER at authoring time: both A6000s saturated by other users
#     (44 GB llama-server on GPU1, 33 GB python on GPU0). Qwen3-8B fp16 needs ~16 GB.
#     Run this ONLY once `nvidia-smi` shows a card with >=18 GB free. Never kill
#     foreign processes (shared box).
#
# Usage (from a machine that can reach tesla; run steps 1 then 2):
#   ./run_lang_batch.sh sync                 # rsync repo -> tesla ~user/mafia_lang
#   ./run_lang_batch.sh run  <GPU_IDX> <N> <LANGS...>
#     e.g. ./run_lang_batch.sh run 0 50 en ru zh es de
#   ./run_lang_batch.sh pull                 # rsync traces back to local logs/
set -euo pipefail

HOST=user@192.168.2.43
REMOTE=/home/user/mafia_lang
PY=/home/user/repos/ml_venv/bin/python
LOCAL_REPO="$(cd "$(dirname "$0")" && pwd)"

case "${1:-}" in
  sync)
    ssh "$HOST" "mkdir -p $REMOTE"
    rsync -az --delete \
      --exclude '.git' --exclude 'logs/' --exclude '__pycache__' \
      --exclude '*.pyc' --exclude 'analysis/' \
      "$LOCAL_REPO/" "$HOST:$REMOTE/"
    ssh "$HOST" "mkdir -p $REMOTE/logs"
    echo "synced -> $HOST:$REMOTE"
    ;;
  run)
    GPU="${2:?gpu idx}"; N="${3:?num games}"; shift 3
    LANGS="$*"; [ -z "$LANGS" ] && LANGS="en ru zh es de"
    for lang in $LANGS; do
      echo "=== lang=$lang N=$N on GPU$GPU ==="
      ssh "$HOST" "cd $REMOTE/src && CUDA_VISIBLE_DEVICES=$GPU HF_HUB_OFFLINE=1 \
        $PY run_lang_games.py -c ../configs/config_lang_${lang}_qwen.yaml -n $N --seed-base 9000"
    done
    ;;
  pull)
    rsync -az "$HOST:$REMOTE/logs/" "$LOCAL_REPO/logs/"
    echo "pulled traces -> $LOCAL_REPO/logs"
    ;;
  *)
    echo "usage: $0 {sync|run <gpu> <N> [langs...]|pull}"; exit 1;;
esac
