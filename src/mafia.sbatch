#!/bin/bash
#SBATCH --job-name=mafia_run
#SBATCH --output=logs/mafia_run-%j.log
#SBATCH --error=logs/mafia_run-%j.err
#SBATCH --time=01:00:00
#SBATCH --cpus-per-task=1
#SBATCH --gpus=1
#SBATCH --nodes=1
#SBATCH --partition=normal
#SBATCH --constraint="type_e"

set -euo pipefail
set -x

# ==== ARGS / PATHS ====
RAW_CONFIG_PATH="${1:-configs/config_qwen.yml}"
PROJECT_DIR="/home/iakarpov/wsd_service-main"
ENV_NAME="wsd_py311"

# ==== МОДУЛИ ====
module purge
module load Python/Anaconda_v02.2024

# ==== CONDA ACTIVATE ====
eval "$(conda shell.bash hook)"
conda activate "${ENV_NAME}"

# Жёстко берём python из активированного окружения
ENV_PY="${CONDA_PREFIX}/bin/python"
if [[ -z "${CONDA_PREFIX:-}" || ! -x "${ENV_PY}" ]]; then
  echo "Bad CONDA_PREFIX or python not found: ${CONDA_PREFIX}" >&2
  exit 3
fi
# (опц.) почини PATH, чтобы env был первым
export PATH="${CONDA_PREFIX}/bin:${PATH}"
export PYTHONNOUSERSITE=1

echo "Using ENV_PY: ${ENV_PY}"
echo "CONDA_PREFIX: ${CONDA_PREFIX:-unset}"


# ==== ОФФЛАЙН/КЭШ ====
export HF_HUB_OFFLINE=1
export TRANSFORMERS_OFFLINE=1
export HF_HUB_DISABLE_TELEMETRY=1
export HF_HOME="${PROJECT_DIR}/.hf_cache"
mkdir -p "${HF_HOME}"

# pandas — не трогать проблемные C-расширения
export PANDAS_NO_NUMEXPR=1
export PANDAS_NO_BOTTLENECK=1

# ==== STUBS для редких импортов ====
STUBS_DIR="${PROJECT_DIR}/stubs"
mkdir -p "${STUBS_DIR}/bottleneck" "${STUBS_DIR}/pyarrow"
cat > "${STUBS_DIR}/numexpr.py" <<'PY'
def __getattr__(name): raise ImportError("numexpr is disabled in this environment")
PY
cat > "${STUBS_DIR}/bottleneck/__init__.py" <<'PY'
def __getattr__(name): raise ImportError("bottleneck is disabled in this environment")
PY
cat > "${STUBS_DIR}/pyarrow/__init__.py" <<'PY'
def __getattr__(name): raise ImportError("pyarrow is disabled in this environment")
PY
export PYTHONPATH="${STUBS_DIR}${PYTHONPATH:+:${PYTHONPATH}}"

# ==== ДИАГНОСТИКА и авто-установка недостающих пакетов ====
"${ENV_PY}" - <<'PY'
import sys, subprocess

def ensure(pkg, spec=None):
    try:
        __import__(pkg)
        print(f"{pkg}: OK")
    except Exception:
        inst = spec or pkg
        print(f"{pkg} missing -> pip install {inst}")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "--upgrade", inst])

# Убедимся, что есть langchain_core и langchain_huggingface
ensure("langchain_core", "langchain-core>=0.3,<0.4")
ensure("langchain", "langchain>=0.3,<0.4")
ensure("langchain_huggingface", "langchain-huggingface>=0.1.0")

# Короткая сводка по основным либам
try:
    import torch, transformers, tokenizers, sentencepiece, huggingface_hub
    print("torch", torch.__version__, "cuda avail:", torch.cuda.is_available(), "cuda:", getattr(torch.version,'cuda',None))
    import transformers as tf
    print("transformers", tf.__version__)
    import tokenizers as tk
    print("tokenizers", tk.__version__)
    import sentencepiece as spm
    print("sentencepiece", getattr(spm, "__version__", "unknown"))
    import huggingface_hub as hh
    print("huggingface_hub", hh.__version__)
except Exception as e:
    print("DIAG IMPORT ERROR:", e)
    raise
PY

# ==== РАБОЧАЯ ДИРЕКТОРИЯ ====
cd "${PROJECT_DIR}"
mkdir -p logs
echo "Working on node $(hostname)"
echo "Assigned GPUs: ${CUDA_VISIBLE_DEVICES:-unset}"
echo "Raw config arg: ${RAW_CONFIG_PATH}"
echo "CONDA_PREFIX: ${CONDA_PREFIX:-unset}"

# Абсолютный путь к конфигу
if [[ "${RAW_CONFIG_PATH}" = /* ]]; then
  CONFIG_PATH="${RAW_CONFIG_PATH}"
else
  CONFIG_PATH="${PROJECT_DIR}/${RAW_CONFIG_PATH}"
fi
echo "Resolved config: ${CONFIG_PATH}"
echo "config_path: ${CONFIG_PATH}" > "${PROJECT_DIR}/_resolved_config.yml"

# ==== ЗАПУСК ====
exec "${ENV_PY}" -u src/main.py "${CONFIG_PATH}"
