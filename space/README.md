---
title: aimt vllm
emoji: "\U0001F3A8"
colorFrom: indigo
colorTo: pink
sdk: docker
app_port: 7860
suggested_hardware: a100-large
hf_oauth: false
short_description: Qwen3.6-27B + aimodeltrain LoRA via vLLM
---

# aimt vllm

Serves [`Qwen/Qwen3.6-27B`](https://huggingface.co/Qwen/Qwen3.6-27B) with the
private LoRA adapter
[`iberescu2201/aimodeltrain-qwen-lora-dryrun`](https://huggingface.co/iberescu2201/aimodeltrain-qwen-lora-dryrun)
loaded on top, via vLLM's OpenAI-compatible API on port `7860`.

## Why a custom Space (not Inference Endpoints)

`Qwen/Qwen3.6-27B`'s config declares `model_type=qwen3_5`, which is too new
for HF's hosted serving stack (TGI, default toolkit) as of writing. HF's
custom-image API also doesn't accept Docker `command`/`args`, so we can't
pass vLLM CLI flags via Inference Endpoints. A Docker-SDK Space sidesteps
both: we ship our own Dockerfile + ENTRYPOINT with the args baked in.

## Required Space secret

- `HF_TOKEN` — fine-grained or write-scoped token that can read the private
  adapter repo. The container downloads the adapter at boot via
  `huggingface_hub.snapshot_download`.

## Required hardware

`a100-large` (1× A100 80GB). Qwen3.6-27B in bf16 is ~54 GB of weights plus
~16 GB of KV-cache headroom at `--max-model-len 8192`. On smaller GPUs the
container will OOM at load time.

## API

vLLM's OpenAI-compatible endpoints:

```
POST  https://<owner>-<space>.hf.space/v1/chat/completions
GET   https://<owner>-<space>.hf.space/v1/models
GET   https://<owner>-<space>.hf.space/health
```

Use `"model": "aimt-dryrun"` to call the base model, or `"model": "dryrun"`
to apply the LoRA adapter.
