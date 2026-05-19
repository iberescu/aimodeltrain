#!/usr/bin/env bash
# Bootstrap script run inside the HF Job. The /code mount holds train_lora.py
# + requirements.txt (uploaded as iberescu2201/aimodeltrain-code).
set -euo pipefail

# Make HF auth visible under every name the libs check for. `--secrets HF_TOKEN`
# on the CLI side injects HF_TOKEN into the container; mirror it to the older
# name so datasets/huggingface_hub/transformers all pick it up.
if [ -n "${HF_TOKEN:-}" ] && [ -z "${HUGGINGFACE_HUB_TOKEN:-}" ]; then
  export HUGGINGFACE_HUB_TOKEN="$HF_TOKEN"
fi
if [ -n "${HUGGINGFACE_HUB_TOKEN:-}" ] && [ -z "${HF_TOKEN:-}" ]; then
  export HF_TOKEN="$HUGGINGFACE_HUB_TOKEN"
fi
echo "==> auth: HF_TOKEN set=$([ -n "${HF_TOKEN:-}" ] && echo yes || echo NO)"

# Reduce CUDA-allocator fragmentation; helps when single allocations are large
# (Qwen2.5-Coder's 152K vocab makes the cross-entropy logits ~2-5GB by itself).
export PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True

echo "==> copying code into a writable workspace"
mkdir -p /workspace
cp -r /code/. /workspace/
cd /workspace

echo "==> nvidia-smi"
nvidia-smi || true

echo "==> python + cuda"
python -V
python -c "import torch; print('torch', torch.__version__, 'cuda', torch.cuda.is_available(), 'devices', torch.cuda.device_count())"

echo "==> pip install training requirements"
pip install --no-cache-dir -r requirements.txt

echo "==> launching train_lora.py"
python train_lora.py \
  --dataset-id iberescu2201/aimodeltrain-1k-v1 \
  --base-model Qwen/Qwen2.5-Coder-7B-Instruct \
  --output-repo iberescu2201/aimodeltrain-qwen2.5coder-7b-lora-1k-v1 \
  --merge-repo  iberescu2201/aimodeltrain-qwen2.5coder-7b-merged-1k-v1 \
  --epochs 2 \
  --lora-r 64 \
  --lora-alpha 128 \
  --max-seq-len 4096 \
  --batch-size 1 \
  --grad-accum 16 \
  --lr 2e-4 \
  --private

echo "==> done"
