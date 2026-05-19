#!/bin/sh
set -eu

ADAPTER_REPO="iberescu2201/aimodeltrain-qwen-lora-dryrun"
ADAPTER_DIR="/tmp/adapter"
BASE_MODEL="Qwen/Qwen3.6-27B"

# HF Spaces only give us /tmp as writable; the default HF cache and xet
# downloader write to $HOME/.cache (read-only on Spaces). Force everything
# under /tmp. Disable hf_transfer + xet — they require their own writable
# scratch paths and we don't need the throughput here.
export HF_HOME=/tmp/hf
export HF_HUB_CACHE=/tmp/hf/hub
export HF_ASSETS_CACHE=/tmp/hf/assets
export XDG_CACHE_HOME=/tmp/cache
export HF_HUB_ENABLE_HF_TRANSFER=0
export HF_HUB_DISABLE_XET=1
# torch._inductor calls getpass.getuser() which falls back to
# pwd.getpwuid(os.getuid()) — HF Space runs as UID 1000 with no /etc/passwd
# entry, which crashes. Setting USER short-circuits the lookup.
export USER=spaceuser
# Force HOME to /tmp — HF Space defaults it to "/" which is read-only and
# breaks flashinfer (writes $HOME/.cache/flashinfer) and other libs.
export HOME=/tmp
export TORCHINDUCTOR_CACHE_DIR=/tmp/torchinductor
export TRITON_CACHE_DIR=/tmp/triton
export NUMBA_CACHE_DIR=/tmp/numba
export FLASHINFER_WORKSPACE_DIR=/tmp/flashinfer
mkdir -p /tmp/hf /tmp/hf/hub /tmp/hf/assets /tmp/cache /tmp/torchinductor /tmp/triton /tmp/numba /tmp/flashinfer /tmp/.cache

echo "==> nvidia-smi"
nvidia-smi || echo "(no nvidia-smi — likely CPU-only build; vLLM will exit)"

echo "==> downloading LoRA adapter from $ADAPTER_REPO"
mkdir -p "$ADAPTER_DIR"
python3 - <<PY
import os
from huggingface_hub import snapshot_download
path = snapshot_download(
    repo_id="$ADAPTER_REPO",
    local_dir="$ADAPTER_DIR",
    token=os.environ.get("HF_TOKEN"),
)
print(f"adapter at {path}")
PY

echo "==> starting vLLM (OpenAI-compatible API on port 7860)"
exec python3 -m vllm.entrypoints.openai.api_server \
  --model "$BASE_MODEL" \
  --served-model-name "aimt-dryrun" \
  --enable-lora \
  --lora-modules "dryrun=$ADAPTER_DIR" \
  --max-lora-rank 64 \
  --port 7860 \
  --host 0.0.0.0 \
  --trust-remote-code \
  --dtype bfloat16 \
  --max-model-len 8192 \
  --gpu-memory-utilization 0.92 \
  --allowed-origins '["*"]'
