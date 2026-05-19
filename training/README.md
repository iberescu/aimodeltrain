# Training (Phase 4 + 5)

This dir holds the LoRA SFT entry point that runs **inside** an HF Jobs
container. Local dev box is intentionally NOT set up to import this — the
heavy deps (peft, trl, accelerate, bitsandbytes) live in this dir's
`requirements.txt` and only install on the GPU side.

## End-to-end (after a pipeline run completes)

1. **Package the dataset** (local — fast, free)

   ```powershell
   .\.venv\Scripts\python.exe scripts\build_dataset.py `
     --name pilot1k-v1 `
     --repo  <hf-user-or-org>/aimodeltrain-1k-v1 `
     --private
   ```

   This walks `data/validated/` plus every `data/_archive_pilot*/validated/`,
   converts each sample to the `{messages: [system,user,assistant]}` SFT
   format with the Qwen chat template applied at train time, splits 90/5/5
   train/val/test deterministically, writes `data/dataset/{train,val,test}.jsonl`,
   then pushes the splits to the HF Hub as a private dataset.

2. **Launch training on HF Jobs** (paid — H100 ~$4–5/hr × ~2–3 h for the
   1k LoRA = ~$10–15)

   ```bash
   hf auth login                              # one-time
   hf jobs run --hardware h100x1 \
     --secret HUGGINGFACE_HUB_TOKEN \
     --secret WANDB_API_KEY \
     -v $PWD/training:/workspace \
     -w /workspace \
     "pip install -r requirements.txt && python train_lora.py \
        --dataset-id   <hf-user>/aimodeltrain-1k-v1 \
        --base-model   Qwen/Qwen3.6-27B \
        --output-repo  <hf-user>/aimodeltrain-qwen3.6-27b-lora-v1 \
        --epochs 2 --lora-r 64"
   ```

   The adapter pushes back to your HF account when training finishes.

## Dry-run / sanity (local)

If you have a small GPU (≥8GB) and want to verify the pipeline assembles:

```powershell
.\.venv\Scripts\python.exe -m pip install -r training\requirements.txt
.\.venv\Scripts\python.exe training\train_lora.py `
  --dataset-jsonl data\dataset\train.jsonl `
  --base-model Qwen/Qwen2.5-Coder-0.5B-Instruct `
  --epochs 1 --max-steps 5 --dry-run
```

## Hyperparameters (defaults match the README's Phase 5 plan)

| Knob | Default | Why |
|---|---|---|
| `--lora-r` | 64 | README target. Higher r ↔ more capacity ↔ slower train. |
| `--lora-alpha` | 128 | 2× r is the common rule of thumb. |
| `--lora-dropout` | 0.05 | Gentle regularization. |
| `--epochs` | 2 | 1k samples × 2 ep × ~1 sec/step ≈ 30 min/epoch on H100. |
| `--lr` | 2e-4 | Standard LoRA learning rate. |
| `--max-seq-len` | 8192 | Most HTML samples fit under 4k tokens; a few outliers need 8k. |
| `--batch-size` | 1 | Per-device; combined with `--grad-accum 16` → effective batch 16. |
| `--no-4bit` | off | 27B in bf16 needs ≥120GB VRAM; keep 4bit on for a single H100 80GB. |

## What loss the model actually trains on

`SFTTrainer` with `assistant_only_loss=True` and the Qwen chat template
masks out system + user tokens — gradients only flow through assistant
tokens. So the model learns to predict the HTML conditioned on the brief,
not to memorize the brief itself.

## DPO later (Phase 6 — not built yet)

Every repaired sample carries a `repair_history` with the pre-repair HTML.
Each `(previous_html, final_html)` pair is a natural (rejected, chosen)
preference pair. A future `train_dpo.py` will consume these to refine the
SFT'd adapter once we have enough repair pairs (~3000 from a 10k run).
