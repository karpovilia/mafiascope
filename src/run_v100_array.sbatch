#!/bin/bash
#SBATCH --job-name=mafia_v100_arr
#SBATCH --account=proj_1782
#SBATCH --output=logs/mafia-v100-a%A_%a.log
#SBATCH --error=logs/mafia-v100-a%A_%a.err
#SBATCH --time=16:00:00
#SBATCH --cpus-per-task=4
#SBATCH --gpus=1
#SBATCH --nodes=1
#SBATCH --partition=rocky
#SBATCH --constraint="type_a|type_b"
# Array wrapper over run_v100_qwen7b.sbatch's body: each task = 10 parallel
# Qwen3-8B games on one V100, deterministic per-task seed.
# Usage: sbatch --array=0-29%8 src/run_v100_array.sbatch
#   -> 30 tasks x 10 games = 300 games in 5 arms (A/B/C x pure/mixed), max 8 GPUs.
# Seeds: 5000 + 10*task_id .. 5009 + 10*task_id (engine uses seed+i per game).

set -euo pipefail
set -x

NUM_GAMES="${1:-10}"
TASK_ID="${SLURM_ARRAY_TASK_ID:-0}"
SEED=$((5000 + 10 * TASK_ID))
# Arm map: 0-5 A (no graph), 6-11 B (1st-order graph), 12-17 C (2nd-order),
#          18-23 B-mixed (slots 0,2,4,6), 24-29 C-mixed. 6 tasks x 10 games each.
case $((TASK_ID / 6)) in
  0) CONFIG="configs/config_v100_qwen7b.yaml" ;;
  1) CONFIG="configs/config_v100_qwen_fb1.yaml" ;;
  2) CONFIG="configs/config_v100_qwen_fb2.yaml" ;;
  3) CONFIG="configs/config_v100_qwen_fb1_mixed.yaml" ;;
  4) CONFIG="configs/config_v100_qwen_fb2_mixed.yaml" ;;
  *) echo "bad task id ${TASK_ID}" >&2; exit 4 ;;
esac

PROJECT_DIR="${SLURM_SUBMIT_DIR:-$(pwd)}"
ENV_NAME="mafia_llm"
MODELS_DIR="${PROJECT_DIR}/models"

module purge
module load Python/Anaconda_v02.2024
eval "$(conda shell.bash hook)"
conda activate "${ENV_NAME}"
ENV_PY="${CONDA_PREFIX}/bin/python"
[[ -x "${ENV_PY}" ]] || { echo "ERROR: python not found in ${CONDA_PREFIX}" >&2; exit 3; }
export PATH="${CONDA_PREFIX}/bin:${PATH}"

export HF_HUB_OFFLINE=1
export TRANSFORMERS_OFFLINE=1
export HF_HUB_DISABLE_TELEMETRY=1
export HF_HOME="${PROJECT_DIR}/.hf_cache"
mkdir -p "${HF_HOME}"
export TRANSFORMERS_CACHE="${MODELS_DIR}"

cd "${PROJECT_DIR}"
mkdir -p logs

echo "========================================"
echo "Node: $(hostname)  Task: ${TASK_ID}  Seed: ${SEED}"
echo "Config: ${CONFIG}  Games: ${NUM_GAMES}"
echo "========================================"

if [[ "${CONFIG}" = /* ]]; then CONFIG_PATH="${CONFIG}"; else CONFIG_PATH="${PROJECT_DIR}/${CONFIG}"; fi

exec "${ENV_PY}" -u src/main.py \
    -c "${CONFIG_PATH}" \
    -n "${NUM_GAMES}" \
    --parallel \
    --seed "${SEED}"
