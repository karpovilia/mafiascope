#!/bin/bash
#SBATCH --job-name=mafia_ww_a100
#SBATCH --account=proj_1782
#SBATCH --output=logs/mafia-ww-a%A_%a.log
#SBATCH --error=logs/mafia-ww-a%A_%a.err
#SBATCH --time=16:00:00
#SBATCH --cpus-per-task=4
#SBATCH --gpus=1
#SBATCH --nodes=1
#SBATCH --partition=rocky
#SBATCH --constraint="type_e"
# A100 array for the Werewolf multigame study (idea 02). Three setups x 50 games
# each, Qwen3-8B fp16, byte-identical model+sampling+probe battery to
# config_v100_qwen7b.yaml. Deliberately NO --mem line (proj policy on rocky).
#
# Task map (15 tasks): setup = task/5, local = task%5.
#   setup 0 -> config_ww_skin_qwen.yaml   (game_type=werewolf_skin)
#   setup 1 -> config_ww_seer_qwen.yaml   (game_type=werewolf_seer)
#   setup 2 -> config_resistance_qwen.yaml(game_type=resistance)
# Each task runs 10 parallel games with per-game seed grid 9000 + 10*local + i,
# so every setup covers seeds 9000..9049 exactly once (shared grid across the
# multigame + multilingual corpora for later paired comparisons).
#
# Submit (example, throttle to 5 concurrent GPUs):
#   sbatch --array=0-14%5 src/run_hpc_a100_array.sbatch
# Fewer games / single setup for a partial run, e.g. ww_skin only:
#   sbatch --array=0-4%5 src/run_hpc_a100_array.sbatch

set -euo pipefail
set -x

NUM_GAMES="${1:-10}"
TASK_ID="${SLURM_ARRAY_TASK_ID:-0}"
SETUP=$(( TASK_ID / 5 ))
LOCAL=$(( TASK_ID % 5 ))
SEED_BASE=$(( 9000 + 10 * LOCAL ))

case "${SETUP}" in
  0) CONFIG="configs/config_ww_skin_qwen.yaml" ;;
  1) CONFIG="configs/config_ww_seer_qwen.yaml" ;;
  2) CONFIG="configs/config_resistance_qwen.yaml" ;;
  *) echo "bad setup ${SETUP} (task ${TASK_ID})" >&2; exit 4 ;;
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
echo "Node: $(hostname)  Task: ${TASK_ID}  Setup: ${SETUP}  Local: ${LOCAL}"
echo "Config: ${CONFIG}  Games: ${NUM_GAMES}  SeedBase: ${SEED_BASE}"
echo "========================================"

if [[ "${CONFIG}" = /* ]]; then CONFIG_PATH="${CONFIG}"; else CONFIG_PATH="${PROJECT_DIR}/${CONFIG}"; fi

exec "${ENV_PY}" -u src/main.py \
    -c "${CONFIG_PATH}" \
    -n "${NUM_GAMES}" \
    --parallel \
    --seed-base "${SEED_BASE}"
