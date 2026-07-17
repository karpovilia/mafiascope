#!/bin/bash
#SBATCH --job-name=mafia_ww_smoke
#SBATCH --account=proj_1782
#SBATCH --output=logs/mafia-ww-smoke-%j.log
#SBATCH --error=logs/mafia-ww-smoke-%j.err
#SBATCH --time=00:25:00
#SBATCH --cpus-per-task=4
#SBATCH --gpus=1
#SBATCH --nodes=1
#SBATCH --partition=test
# Fast env+config smoke on the test partition. Loads Qwen3-8B from the hf_mirror
# and plays ONE short game per setup with probes OFF and rounds capped, purely to
# verify the cluster env, weights path, and config wiring BEFORE the A100 array.
# Purpose is validation only — these games are NOT part of any corpus.
#
# Submit one config (arg 1 = config basename, default ww_skin):
#   sbatch src/run_hpc_test_smoke.sbatch config_ww_skin_qwen.yaml
#   sbatch src/run_hpc_test_smoke.sbatch config_ww_seer_qwen.yaml
#   sbatch src/run_hpc_test_smoke.sbatch config_resistance_qwen.yaml

set -euo pipefail
set -x

CONFIG="${1:-configs/config_ww_skin_qwen.yaml}"
[[ "${CONFIG}" == */* ]] || CONFIG="configs/${CONFIG}"

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

if [[ "${CONFIG}" = /* ]]; then CONFIG_PATH="${CONFIG}"; else CONFIG_PATH="${PROJECT_DIR}/${CONFIG}"; fi

echo "SMOKE  config=${CONFIG_PATH}  node=$(hostname)"
# 1 game, probes OFF, rounds capped to 2 -> finishes within the 25-min test cap.
exec "${ENV_PY}" -u src/main.py \
    -c "${CONFIG_PATH}" \
    -n 1 \
    --no-introspection \
    --max-rounds 2 \
    --seed-base 9000
