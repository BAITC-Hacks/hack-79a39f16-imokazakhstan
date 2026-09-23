#!/usr/bin/env bash
# Isolated Person 2 training environment; no credentials are copied or printed.
set -euo pipefail
TASK_DIR=/home/shadeform/wind-person2-20260923
mkdir -p "$TASK_DIR"
python3 -m pip install --quiet --target "$TASK_DIR/bootstrap" uv
"$TASK_DIR/bootstrap/bin/uv" venv --python 3.11 "$TASK_DIR/.venv"
"$TASK_DIR/bootstrap/bin/uv" pip install --python "$TASK_DIR/.venv/bin/python" \
  torch==2.8.0 numpy==1.26.4 pandas==2.3.2 scipy==1.16.2 scikit-learn==1.7.2 \
  joblib threadpoolctl catboost==1.2.10 statsforecast==2.1.1 \
  chronos-forecasting==2.3.2 transformers==4.56.1 peft==0.18.1 accelerate==1.15.0
"$TASK_DIR/.venv/bin/python" -c 'import torch; print("torch",torch.__version__,"CUDA",torch.cuda.is_available(),"GPUs",torch.cuda.device_count())'
