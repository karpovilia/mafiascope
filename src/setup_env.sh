#!/bin/bash
# ──────────────────────────────────────────────
# Setup conda environment for LLM Mafia on HPC
#
# Usage:
#   bash setup_env.sh              # create env + download models
#   bash setup_env.sh --no-models  # skip model download
# ──────────────────────────────────────────────
set -euo pipefail

ENV_NAME="mafia_llm"
PYTHON_VER="3.12"
PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
MODELS_DIR="${PROJECT_DIR}/models"

echo "=== LLM Mafia Environment Setup ==="
echo "Project: ${PROJECT_DIR}"
echo "Env: ${ENV_NAME}"

# ── Load modules (HPC-specific, skip if not available) ──
if command -v module &>/dev/null; then
    module purge 2>/dev/null || true
    module load Python/Anaconda_v02.2024 2>/dev/null || true
fi

# ── Conda init ──
if command -v conda &>/dev/null; then
    eval "$(conda shell.bash hook)"
else
    echo "[ERROR] conda not found. Install Anaconda/Miniconda first."
    exit 1
fi

# ── Create environment ──
if conda env list | grep -q "^${ENV_NAME} "; then
    echo "[INFO] Env ${ENV_NAME} exists, activating..."
else
    echo "[INFO] Creating conda env ${ENV_NAME} with Python ${PYTHON_VER}..."
    conda create -n "${ENV_NAME}" python="${PYTHON_VER}" -y
fi

conda activate "${ENV_NAME}"
echo "[INFO] Python: $(python --version) at $(which python)"

# ── Install dependencies ──
echo "[INFO] Installing Python packages..."
pip install --upgrade pip
pip install -r "${PROJECT_DIR}/requirements.txt"

# Extra packages for HPC
pip install huggingface_hub sentencepiece

# ── Verify ──
python -c "
import torch, transformers, yaml
print(f'torch {torch.__version__}  CUDA: {torch.cuda.is_available()}')
if torch.cuda.is_available():
    print(f'  GPU: {torch.cuda.get_device_name(0)}')
    print(f'  VRAM: {torch.cuda.get_device_properties(0).total_mem / 1e9:.1f} GB')
print(f'transformers {transformers.__version__}')
print('All OK')
"

# ── Download models ──
if [[ "${1:-}" != "--no-models" ]]; then
    echo ""
    echo "[INFO] Downloading models for offline use..."
    python "${PROJECT_DIR}/download_models.py" \
        --from-config "${PROJECT_DIR}/../configs/config_local.yaml" \
        --dest "${MODELS_DIR}"
    echo "[INFO] Models saved to: ${MODELS_DIR}"
else
    echo "[INFO] Skipping model download (--no-models)"
fi

echo ""
echo "=== Setup complete ==="
echo "To activate:  conda activate ${ENV_NAME}"
echo "To run:       python main.py -c config_local.yaml -n 10 --parallel"
