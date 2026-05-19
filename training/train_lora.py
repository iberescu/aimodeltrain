"""Phase 5 — LoRA SFT on Qwen3.6-27B (or compatible).

Designed to run inside an HF Jobs container on H100 (or a local GPU box with
≥48GB VRAM in 4-bit). The dataset is pulled from the HF Hub by id (the
dataset that scripts/build_dataset.py produced and pushed).

Key choices (from the README):
  - 4-bit NF4 quantization via bitsandbytes (27B in 4-bit fits an H100 80GB)
  - LoRA r=64, alpha=128, dropout=0.05
  - Target all linear projections (q/k/v/o + gate/up/down)
  - 2 epochs by default (override via --epochs)
  - bf16, gradient checkpointing, paged_adamw_8bit
  - SFT loss masks the prompt; only the assistant tokens count.
    `trl`'s `SFTTrainer` with the `messages` column + the model's chat template
    does this when `assistant_only_loss=True` (trl>=0.12).

Launch on HF Jobs (example — fire this from your laptop once the dataset is
pushed):

  hf auth login                                # one-time
  hf jobs run --hardware h100x1 \
    --secret HUGGINGFACE_HUB_TOKEN \
    -v $PWD/training:/workspace \
    -w /workspace \
    "pip install -r requirements.txt && python train_lora.py \
       --dataset-id  myuser/aimodeltrain-1k-v1 \
       --base-model  Qwen/Qwen3.6-27B \
       --output-repo myuser/aimodeltrain-qwen3.6-27b-lora-v1"

Locally (small smoke test, single sample):
  python training/train_lora.py --dataset-jsonl data/dataset/train.jsonl \
    --base-model Qwen/Qwen2.5-Coder-0.5B-Instruct --epochs 1 --max-steps 5 --dry-run
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def get_env(name: str, default=None):
    return os.environ.get(name, default)


def parse_args():
    ap = argparse.ArgumentParser()
    # Data
    ap.add_argument("--dataset-id", default=None, help="HF dataset id, e.g. myuser/aimodeltrain-1k-v1")
    ap.add_argument("--dataset-jsonl", default=None, help="Alt to --dataset-id: local train.jsonl + (optional) val.jsonl alongside")
    # Model
    ap.add_argument("--base-model", default="Qwen/Qwen3.6-27B", help="HF model id of the base.")
    # LoRA
    ap.add_argument("--lora-r", type=int, default=64)
    ap.add_argument("--lora-alpha", type=int, default=128)
    ap.add_argument("--lora-dropout", type=float, default=0.05)
    # Training
    ap.add_argument("--epochs", type=float, default=2.0)
    ap.add_argument("--lr", type=float, default=2e-4)
    ap.add_argument("--warmup-ratio", type=float, default=0.03)
    ap.add_argument("--max-seq-len", type=int, default=8192)
    ap.add_argument("--batch-size", type=int, default=1, help="per-device train batch")
    ap.add_argument("--grad-accum", type=int, default=16)
    ap.add_argument("--max-steps", type=int, default=-1, help="-1 = use --epochs")
    # Output
    ap.add_argument("--output-dir", default="./outputs/lora")
    ap.add_argument("--output-repo", default=None, help="If set, push the trained LoRA adapter to this HF repo id.")
    ap.add_argument("--merge-repo", default=None,
                    help="If set, after training, load base in bf16, merge adapter into it, "
                         "and push as a complete model to this repo id. Enables deployment "
                         "via HF Inference Endpoints' default toolkit (no LoRA mounting needed).")
    ap.add_argument("--private", action="store_true", default=True)
    ap.add_argument("--public", action="store_true")
    # Behavior
    ap.add_argument("--dry-run", action="store_true", help="Build pipeline & log shapes, but don't actually train.")
    ap.add_argument("--no-4bit", action="store_true", help="Disable 4-bit quantization (requires more VRAM).")
    ap.add_argument("--seed", type=int, default=42)
    return ap.parse_args()


def load_dataset_local_or_hub(args):
    from datasets import load_dataset, DatasetDict  # type: ignore

    if args.dataset_id:
        print(f"==> loading dataset from HF Hub: {args.dataset_id}")
        return load_dataset(args.dataset_id)
    if not args.dataset_jsonl:
        print("ERROR: provide --dataset-id or --dataset-jsonl", file=sys.stderr)
        sys.exit(2)

    p = Path(args.dataset_jsonl)
    files = {"train": str(p)}
    val = p.parent / "val.jsonl"
    if val.exists():
        files["val"] = str(val)
    print(f"==> loading dataset from local JSONL: {files}")
    splits = {k: load_dataset("json", data_files=v, split="train") for k, v in files.items()}
    return DatasetDict(splits)


def main():
    args = parse_args()

    # Lazy imports so --help works without the heavy stack installed
    import torch  # type: ignore
    from transformers import (  # type: ignore
        AutoModelForCausalLM,
        AutoTokenizer,
        BitsAndBytesConfig,
    )
    from peft import LoraConfig, get_peft_model, prepare_model_for_kbit_training  # type: ignore
    from trl import SFTConfig, SFTTrainer  # type: ignore

    private = False if args.public else args.private

    ds = load_dataset_local_or_hub(args)
    train = ds["train"]
    eval_ds = ds.get("val") or ds.get("validation") or ds.get("test")
    print(f"  train rows: {len(train):,}")
    if eval_ds is not None:
        print(f"  eval rows:  {len(eval_ds):,}")

    print(f"==> tokenizer: {args.base_model}")
    tok = AutoTokenizer.from_pretrained(args.base_model, use_fast=True, trust_remote_code=True)
    if tok.pad_token is None:
        tok.pad_token = tok.eos_token

    bnb_cfg = None
    if not args.no_4bit:
        bnb_cfg = BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_compute_dtype=torch.bfloat16,
            bnb_4bit_use_double_quant=True,
        )

    print(f"==> model: {args.base_model} (4bit={not args.no_4bit})")
    model = AutoModelForCausalLM.from_pretrained(
        args.base_model,
        quantization_config=bnb_cfg,
        torch_dtype=torch.bfloat16,
        device_map="auto",
        trust_remote_code=True,
        attn_implementation="sdpa",
    )
    model.config.use_cache = False  # incompatible with gradient checkpointing
    if bnb_cfg is not None:
        model = prepare_model_for_kbit_training(model, use_gradient_checkpointing=True)

    lora_cfg = LoraConfig(
        r=args.lora_r,
        lora_alpha=args.lora_alpha,
        lora_dropout=args.lora_dropout,
        bias="none",
        task_type="CAUSAL_LM",
        target_modules=[
            "q_proj", "k_proj", "v_proj", "o_proj",
            "gate_proj", "up_proj", "down_proj",
        ],
    )
    model = get_peft_model(model, lora_cfg)
    print("==> trainable params:")
    model.print_trainable_parameters()

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    sft_cfg = SFTConfig(
        output_dir=str(out_dir),
        num_train_epochs=args.epochs,
        max_steps=args.max_steps,
        per_device_train_batch_size=args.batch_size,
        per_device_eval_batch_size=max(1, args.batch_size),
        gradient_accumulation_steps=args.grad_accum,
        gradient_checkpointing=True,
        gradient_checkpointing_kwargs={"use_reentrant": False},
        learning_rate=args.lr,
        lr_scheduler_type="cosine",
        warmup_ratio=args.warmup_ratio,
        bf16=True,
        optim="paged_adamw_8bit",
        logging_steps=10,
        save_strategy="epoch",
        eval_strategy="epoch" if eval_ds is not None else "no",
        save_total_limit=2,
        report_to=["wandb"] if get_env("WANDB_API_KEY") else ["none"],
        seed=args.seed,
        # trl >=0.13 renamed max_seq_length to max_length.
        max_length=args.max_seq_len,
        packing=False,
        # trl's SFTTrainer reads the `messages` column and applies the tokenizer's
        # chat template. assistant_only_loss masks the system+user prompt.
        assistant_only_loss=True,
    )

    trainer = SFTTrainer(
        model=model,
        args=sft_cfg,
        train_dataset=train,
        eval_dataset=eval_ds,
        processing_class=tok,
    )

    if args.dry_run:
        print("==> dry-run: pipeline assembled OK; not training.")
        sample = next(iter(trainer.get_train_dataloader()))
        print(f"  first batch keys: {list(sample.keys())}")
        for k, v in sample.items():
            shape = getattr(v, "shape", None)
            print(f"    {k:<24} shape={shape}")
        return

    print("==> starting training")
    trainer.train()

    print(f"==> saving LoRA adapter to {out_dir}")
    trainer.save_model(str(out_dir))
    tok.save_pretrained(str(out_dir))
    (out_dir / "training_args.json").write_text(json.dumps(vars(args), indent=2), encoding="utf-8")

    if args.output_repo:
        print(f"==> push adapter to HF: {args.output_repo} (private={private})")
        from huggingface_hub import HfApi  # type: ignore
        api = HfApi()
        api.create_repo(args.output_repo, repo_type="model", private=private, exist_ok=True)
        api.upload_folder(
            repo_id=args.output_repo,
            repo_type="model",
            folder_path=str(out_dir),
            commit_message=f"LoRA adapter — base={args.base_model}, r={args.lora_r}, epochs={args.epochs}",
        )
        print(f"    done — https://huggingface.co/{args.output_repo}")

    if args.merge_repo:
        # Free the trainer + quantized model before reloading in bf16. Merging
        # on top of a 4-bit base would produce a 4-bit merged model which is
        # awkward to deploy; instead we reload the base in bf16, attach the
        # adapter from disk, and merge cleanly.
        print(f"==> merging adapter into base (bf16) and pushing to {args.merge_repo}")
        import gc
        del trainer, model
        gc.collect()
        torch.cuda.empty_cache()

        base = AutoModelForCausalLM.from_pretrained(
            args.base_model,
            torch_dtype=torch.bfloat16,
            device_map="auto",
            trust_remote_code=True,
        )
        from peft import PeftModel  # type: ignore
        merged = PeftModel.from_pretrained(base, str(out_dir))
        merged = merged.merge_and_unload()

        merge_dir = out_dir.parent / (out_dir.name + "_merged")
        merge_dir.mkdir(parents=True, exist_ok=True)
        print(f"    saving merged model to {merge_dir}")
        merged.save_pretrained(str(merge_dir), safe_serialization=True, max_shard_size="4GB")
        tok.save_pretrained(str(merge_dir))

        from huggingface_hub import HfApi  # type: ignore
        api = HfApi()
        api.create_repo(args.merge_repo, repo_type="model", private=private, exist_ok=True)
        print(f"    uploading to {args.merge_repo}")
        api.upload_folder(
            repo_id=args.merge_repo,
            repo_type="model",
            folder_path=str(merge_dir),
            commit_message=f"Merged model — base={args.base_model} + LoRA r={args.lora_r} (epochs={args.epochs})",
        )
        print(f"    done — https://huggingface.co/{args.merge_repo}")


if __name__ == "__main__":
    main()
