"""Phase 4 — Dataset packaging.

Walks validated samples from one or more pilot runs (live data/validated
and/or archived data/_archive_pilot*/), converts each to a Qwen chat-template
SFT record (messages = system + user + assistant), splits deterministically
into train/val/test, writes JSONL locally, and optionally pushes to the
HuggingFace Hub as a private dataset.

The user message is rendered with generators.system_prompts.render_user_prompt
so the training prompt matches the exact format the teacher saw at generation
time — that's important: at inference the student will receive the same
prompt shape, so SFT should align with it.

Examples (PowerShell):
  python scripts/build_dataset.py
  python scripts/build_dataset.py --source data/_archive_pilot50_20260512_1202
  python scripts/build_dataset.py --repo myuser/aimodeltrain-1k-v1 --private

The --repo argument requires HUGGINGFACE_HUB_TOKEN to be set (process env or
Windows user-scope env).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import sys
from pathlib import Path

# certifi's bundle sometimes lacks the intermediate CA HF's CDN serves;
# inject the OS cert store before any SSL connection is opened.
try:
    import truststore  # type: ignore
    truststore.inject_into_ssl()
except Exception:
    pass

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from generators.system_prompts import BASE_SYSTEM, render_user_prompt  # noqa: E402

DESIGN_SPECS = json.loads((REPO_ROOT / "configs" / "design_types.json").read_text(encoding="utf-8"))["design_types"]


def discover_sources(explicit: list[str] | None) -> list[Path]:
    """If --source paths were given, use them. Otherwise scan the live run +
    every data/_archive_pilot* dir for a 'validated' subdir."""
    if explicit:
        out = []
        for s in explicit:
            p = Path(s)
            if not p.is_absolute():
                p = REPO_ROOT / s
            if not p.exists():
                print(f"warning: --source {s} does not exist; skipping", file=sys.stderr)
                continue
            out.append(p)
        return out

    found: list[Path] = []
    live = REPO_ROOT / "data" / "validated"
    if live.exists() and any(live.rglob("*.json")):
        found.append(live)
    for arch in sorted((REPO_ROOT / "data").glob("_archive_pilot*")):
        for cand in [arch / "validated", arch / "data" / "validated"]:
            if cand.exists():
                found.append(cand)
                break
    return found


def split_bucket(sample_id: str, train: float, val: float) -> str:
    """Deterministic split via sha1(sample_id) → uniform float in [0,1)."""
    h = hashlib.sha1(sample_id.encode("utf-8")).hexdigest()
    u = int(h[:8], 16) / 0xFFFFFFFF
    if u < train:
        return "train"
    if u < train + val:
        return "val"
    return "test"


def load_validated_samples(sources: list[Path]) -> list[dict]:
    out: list[dict] = []
    seen: set[str] = set()
    for src in sources:
        for sp in sorted(src.rglob("*.json")):
            if sp.name.endswith(".validation.json"):
                continue
            try:
                sample = json.loads(sp.read_text(encoding="utf-8"))
            except Exception as e:
                print(f"  skip (bad json): {sp.name} — {e}", file=sys.stderr)
                continue
            sid = sample.get("id") or sp.stem
            if sid in seen:
                continue
            seen.add(sid)
            if not sample.get("html") or not sample.get("brief"):
                print(f"  skip (no html/brief): {sid}", file=sys.stderr)
                continue
            out.append(sample)
    return out


def to_sft_record(sample: dict) -> dict | None:
    brief = sample["brief"]
    dt = sample.get("design_type") or brief.get("design_type")
    spec = DESIGN_SPECS.get(dt)
    if spec is None:
        return None
    user_msg = render_user_prompt(brief, spec)
    record = {
        "id": sample["id"],
        "design_type": dt,
        "company_name": brief.get("company_name"),
        "messages": [
            {"role": "system", "content": BASE_SYSTEM.strip()},
            {"role": "user", "content": user_msg.strip()},
            {"role": "assistant", "content": sample["html"].strip()},
        ],
    }
    return record


def write_jsonl(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def maybe_push_to_hub(out_dir: Path, repo_id: str, private: bool, name: str) -> None:
    try:
        from datasets import load_dataset, DatasetDict  # type: ignore
    except ImportError:
        print("ERROR: --repo set but `datasets` package not importable", file=sys.stderr)
        sys.exit(2)

    token = os.environ.get("HUGGINGFACE_HUB_TOKEN") or os.environ.get("HF_TOKEN")
    if not token:
        try:
            import ctypes  # noqa: F401  (winreg fallback below)
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment") as key:
                token = winreg.QueryValueEx(key, "HUGGINGFACE_HUB_TOKEN")[0]
        except Exception:
            token = None
    if not token:
        print("ERROR: --repo set but HUGGINGFACE_HUB_TOKEN is empty", file=sys.stderr)
        sys.exit(2)

    splits = {}
    for sp in ("train", "val", "test"):
        f = out_dir / f"{sp}.jsonl"
        if f.exists() and f.stat().st_size > 0:
            splits[sp] = load_dataset("json", data_files=str(f), split="train")
    if not splits:
        print("ERROR: no non-empty split files to push", file=sys.stderr)
        sys.exit(2)
    ds = DatasetDict(splits)
    print(f"==> push_to_hub: {repo_id} (private={private}, tag={name})")
    ds.push_to_hub(repo_id, private=private, token=token, commit_message=f"{name}: {sum(len(v) for v in splits.values())} samples")
    print(f"    done — https://huggingface.co/datasets/{repo_id}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", action="append", help="Directory of validated samples. Repeatable. Default: auto-discover live + archives.")
    ap.add_argument("--out-dir", default="data/dataset", help="Where to write {train,val,test}.jsonl. Default: data/dataset")
    ap.add_argument("--train", type=float, default=0.90)
    ap.add_argument("--val", type=float, default=0.05)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--repo", default=None, help="If set, push the splits as a HuggingFace dataset to this repo id (e.g. myuser/aimodeltrain-1k-v1).")
    ap.add_argument("--private", action="store_true", default=True, help="Push as a private dataset (default).")
    ap.add_argument("--public", action="store_true", help="Override --private.")
    ap.add_argument("--name", default="pilot", help="Tag used in the HF commit message.")
    args = ap.parse_args()

    private = False if args.public else args.private
    test = 1.0 - args.train - args.val
    if min(args.train, args.val, test) < 0 or abs(args.train + args.val + test - 1.0) > 1e-6:
        print(f"ERROR: train+val+test must sum to 1.0; got train={args.train} val={args.val} test={test}", file=sys.stderr)
        sys.exit(2)

    sources = discover_sources(args.source)
    if not sources:
        print("ERROR: no validated samples found. Run the pipeline first.", file=sys.stderr)
        sys.exit(1)

    print(f"==> sources ({len(sources)}):")
    for s in sources:
        n = sum(1 for p in s.rglob("*.json") if not p.name.endswith(".validation.json"))
        print(f"    {s.relative_to(REPO_ROOT)}  ({n} files)")

    samples = load_validated_samples(sources)
    print(f"==> loaded {len(samples)} unique validated samples")
    if not samples:
        sys.exit(1)

    by_split: dict[str, list[dict]] = {"train": [], "val": [], "test": []}
    skipped = 0
    for s in samples:
        rec = to_sft_record(s)
        if rec is None:
            skipped += 1
            continue
        bucket = split_bucket(rec["id"], args.train, args.val)
        by_split[bucket].append(rec)

    rng = random.Random(args.seed)
    for k in by_split:
        rng.shuffle(by_split[k])

    out_dir = REPO_ROOT / args.out_dir if not Path(args.out_dir).is_absolute() else Path(args.out_dir)
    for sp, recs in by_split.items():
        write_jsonl(out_dir / f"{sp}.jsonl", recs)
        print(f"  wrote {sp}.jsonl: {len(recs)} records")

    if skipped:
        print(f"  skipped {skipped} samples (missing spec / fields)")

    # Stats
    print()
    print(f"  total kept:   {sum(len(v) for v in by_split.values())}")
    print(f"  total bytes:  {sum((out_dir / f'{k}.jsonl').stat().st_size for k in by_split):,}")

    # Stats by design_type
    by_type: dict[str, int] = {}
    for k in by_split:
        for r in by_split[k]:
            by_type[r["design_type"]] = by_type.get(r["design_type"], 0) + 1
    print()
    print("  by design_type:")
    for dt, n in sorted(by_type.items(), key=lambda x: -x[1]):
        print(f"    {dt:<22} {n}")

    # Manifest
    manifest = {
        "dataset_name": args.name,
        "sources": [str(s.relative_to(REPO_ROOT)) for s in sources],
        "n_samples": sum(len(v) for v in by_split.values()),
        "split_counts": {k: len(v) for k, v in by_split.items()},
        "split_ratios": {"train": args.train, "val": args.val, "test": round(test, 6)},
        "by_design_type": dict(sorted(by_type.items(), key=lambda x: -x[1])),
        "format": "openai-messages (system/user/assistant) — Qwen chat template applied at training time",
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"\n  manifest -> {out_dir / 'manifest.json'}")

    if args.repo:
        maybe_push_to_hub(out_dir, args.repo, private, args.name)


if __name__ == "__main__":
    main()
