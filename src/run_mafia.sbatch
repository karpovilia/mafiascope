#!/bin/bash
#SBATCH --job-name=mafia_llm
#SBATCH --output=logs/mafia-%j.log
#SBATCH --error=logs/mafia-%j.err
#SBATCH --time=12:00:00
#SBATCH --cpus-per-task=4
#SBATCH --gpus=1
#SBATCH --nodes=1
#SBATCH --partition=normal
#SBATCH --constraint="type_e"

# ──────────────────────────────────────────────
# SLURM job: run N parallel Mafia games with batched GPU inference
#
# Usage:
#   sbatch run_mafia.sbatch                              # 10 games, default config
#   sbatch run_mafia.sbatch config_local.yaml 20         # 20 games, custom config
#   sbatch run_mafia.sbatch config_local.yaml 10 --no-introspection
# ──────────────────────────────────────────────

set -euo pipefail
set -x

# ==== ARGS ====
CONFIG="${1:-../configs/config_local.yaml}"
NUM_GAMES="${2:-10}"
EXTRA_ARGS="${3:-}"

PROJECT_DIR="${SLURM_SUBMIT_DIR:-$(pwd)}"
ENV_NAME="mafia_llm"
MODELS_DIR="${PROJECT_DIR}/models"

# ==== MODULES ====
module purge
module load Python/Anaconda_v02.2024

# ==== CONDA ====
eval "$(conda shell.bash hook)"
conda activate "${ENV_NAME}"

ENV_PY="${CONDA_PREFIX}/bin/python"
if [[ ! -x "${ENV_PY}" ]]; then
    echo "ERROR: python not found in ${CONDA_PREFIX}" >&2
    exit 3
fi
export PATH="${CONDA_PREFIX}/bin:${PATH}"
# export PYTHONNOUSERSITE=1

# ==== OFFLINE MODE ====
export HF_HUB_OFFLINE=1
export TRANSFORMERS_OFFLINE=1
export HF_HUB_DISABLE_TELEMETRY=1
export HF_HOME="${PROJECT_DIR}/.hf_cache"
mkdir -p "${HF_HOME}"

# Point transformers to local model copies
export TRANSFORMERS_CACHE="${MODELS_DIR}"

# ==== DIAGNOSTICS ====
cd "${PROJECT_DIR}"
mkdir -p logs

echo "========================================"
echo "Node: $(hostname)"
echo "GPU:  ${CUDA_VISIBLE_DEVICES:-unset}"
echo "Python: ${ENV_PY}"
echo "Config: ${CONFIG}"
echo "Games: ${NUM_GAMES}"
echo "Models dir: ${MODELS_DIR}"
echo "========================================"

"${ENV_PY}" -c "
import torch
print(f'torch {torch.__version__}')
print(f'CUDA available: {torch.cuda.is_available()}')
if torch.cuda.is_available():
    print(f'GPU: {torch.cuda.get_device_name(0)}')
    print(f'VRAM: {torch.cuda.get_device_properties(0).total_memory / 1e9:.1f} GB')
import transformers
print(f'transformers {transformers.__version__}')
"

# ==== RESOLVE CONFIG PATH ====
if [[ "${CONFIG}" = /* ]]; then
    CONFIG_PATH="${CONFIG}"
else
    CONFIG_PATH="${PROJECT_DIR}/${CONFIG}"
fi

# ==== PATCH CONFIG: point model to local path ====
# If models were downloaded to ./models/, the model path in config needs
# to point there. We do this via env var that llm_backend.py respects,
# or just ensure the model path in config matches the HF cache.

# ==== RUN ====
echo "Starting ${NUM_GAMES} parallel games..."
exec "${ENV_PY}" -u src/main.py \
    -c "${CONFIG_PATH}" \
    -n "${NUM_GAMES}" \
    --parallel \
    ${EXTRA_ARGS}
