#!/bin/bash
#SBATCH --job-name=mafia_v100_q8b
#SBATCH --account=proj_1782
#SBATCH --output=logs/mafia-v100-%j.log
#SBATCH --error=logs/mafia-v100-%j.err
#SBATCH --time=16:00:00
#SBATCH --cpus-per-task=4
#SBATCH --gpus=1
#SBATCH --nodes=1
#SBATCH --partition=rocky
#SBATCH --constraint="type_a|type_b"
# NOTE: V100-32GB nodes (type_a/type_b) currently live in partition "rocky"
# (verified via sinfo 2026-07-11; type_c V100s in "normal" are mostly allocated).
# rocky forbids GPU-less jobs — --gpus=1 satisfies that. Do NOT add --mem on rocky.

# ──────────────────────────────────────────────
# SLURM job: N parallel Mafia games, Qwen3-8B fp16 on 1x V100-32GB
#
# Usage:
#   sbatch src/run_v100_qwen7b.sbatch                                    # 10 games
#   sbatch src/run_v100_qwen7b.sbatch configs/config_v100_qwen7b.yaml 1  # smoke: 1 game
# ──────────────────────────────────────────────

set -euo pipefail
set -x

# ==== ARGS ====
CONFIG="${1:-configs/config_v100_qwen7b.yaml}"
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
# export PYTHONNOUSERSITE=1   # kept identical to run_mafia.sbatch (known-working env)

# ==== OFFLINE MODE (compute nodes have no internet) ====
export HF_HUB_OFFLINE=1
export TRANSFORMERS_OFFLINE=1
export HF_HUB_DISABLE_TELEMETRY=1
export HF_HOME="${PROJECT_DIR}/.hf_cache"
mkdir -p "${HF_HOME}"
export TRANSFORMERS_CACHE="${MODELS_DIR}"

# ==== DIAGNOSTICS ====
cd "${PROJECT_DIR}"
# cwd = repo root; config uses log_dir: logs -> traces in ${PROJECT_DIR}/logs/<uuid>/
mkdir -p logs

echo "========================================"
echo "Node: $(hostname)"
echo "GPU:  ${CUDA_VISIBLE_DEVICES:-unset}"
echo "Python: ${ENV_PY}"
echo "Config: ${CONFIG}"
echo "Games: ${NUM_GAMES}"
echo "========================================"

"${ENV_PY}" -c "
import torch
print(f'torch {torch.__version__}')
print(f'CUDA available: {torch.cuda.is_available()}')
if torch.cuda.is_available():
    print(f'GPU: {torch.cuda.get_device_name(0)}')
    print(f'VRAM: {torch.cuda.get_device_properties(0).total_memory / 1e9:.1f} GB')
    print(f'bf16 supported: {torch.cuda.is_bf16_supported()}')  # expect False on V100
import transformers
print(f'transformers {transformers.__version__}')
"

# ==== RESOLVE CONFIG PATH ====
if [[ "${CONFIG}" = /* ]]; then
    CONFIG_PATH="${CONFIG}"
else
    CONFIG_PATH="${PROJECT_DIR}/${CONFIG}"
fi

# ==== RUN ====
echo "Starting ${NUM_GAMES} parallel games..."
exec "${ENV_PY}" -u src/main.py \
    -c "${CONFIG_PATH}" \
    -n "${NUM_GAMES}" \
    --parallel \
    ${EXTRA_ARGS}
