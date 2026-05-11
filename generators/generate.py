"""Phase-2 driver: turn a brief plan into raw HTML samples using Gemini 3.1 Pro.

Concurrency: async with bounded semaphore. Persistent: every completed brief
writes a record to data/raw/<design_type>/<id>.json and a row to
logs/generation_log.jsonl. Re-running the script skips ids already on disk —
safe to interrupt and resume.

Usage:
    python generators/generate.py --plan-size 2000 --concurrency 6
    python generators/generate.py --plan-size 2000 --resume         # idempotent
"""
from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import os
import random
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# google-genai SDK is imported lazily so `--dry-run` works without it installed.

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from generators.briefs import build_plan, Brief  # noqa: E402
from generators.system_prompts import BASE_SYSTEM, render_user_prompt  # noqa: E402
from generators.api_log import log_api_call, extract_usage_from_genai_response  # noqa: E402

CONFIG = json.loads((REPO_ROOT / "configs" / "design_types.json").read_text(encoding="utf-8"))
RAW_DIR = REPO_ROOT / "data" / "raw"
LOG_PATH = REPO_ROOT / "logs" / "generation_log.jsonl"

MODEL_ID = "gemini-3.1-pro-preview"

# Per-call deadline for a single Gemini generation. 120s is comfortable for
# thinking-mode calls but tight enough that a hung connection releases the
# async worker promptly instead of wedging the pool (which is what stalled
# the pilot). A second attempt has a slightly higher cap.
GEN_TIMEOUT_S = 120.0
GEN_TIMEOUT_S_RETRY = 180.0

HTML_FENCE_RE = re.compile(r"```(?:html)?\s*(.+?)```", re.DOTALL | re.IGNORECASE)


def strip_fences(text: str) -> str:
    text = text.strip()
    m = HTML_FENCE_RE.search(text)
    if m:
        return m.group(1).strip()
    return text


def is_well_formed_html(text: str) -> bool:
    t = text.lower()
    return t.lstrip().startswith("<!doctype html") and "</html>" in t and "<body" in t


def proportional_plan(plan_size: int) -> dict[str, int]:
    """Scale generation_targets down to plan_size proportionally."""
    raw = CONFIG["generation_targets"]
    total = sum(raw.values())
    scaled = {k: max(1, round(v * plan_size / total)) for k, v in raw.items()}
    # Adjust last key to make the sum exact
    diff = plan_size - sum(scaled.values())
    last = list(scaled.keys())[-1]
    scaled[last] += diff
    return scaled


def existing_ids(design_type: str) -> set[str]:
    out: set[str] = set()
    d = RAW_DIR / design_type
    if not d.exists():
        return out
    for p in d.glob("*.json"):
        out.add(p.stem)
    return out


def write_sample(record: dict) -> Path:
    d = RAW_DIR / record["design_type"]
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{record['id']}.json"
    p.write_text(json.dumps(record, indent=2), encoding="utf-8")
    return p


def log_event(event: dict) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event) + "\n")


async def call_gemini(client, brief_dict: dict, spec: dict, attempt: int, sample_id: str | None = None) -> tuple[str | None, dict]:
    """Returns (html_or_none, metadata).

    On retriable failure returns (None, {"error": ...}).
    """
    from google.genai import types  # type: ignore

    user_prompt = render_user_prompt(brief_dict, spec)
    # thinking_level helps for layout planning; output is what we save.
    config = types.GenerateContentConfig(
        system_instruction=BASE_SYSTEM,
        temperature=1.0 if attempt == 0 else 0.7,
        top_p=0.95,
        # max_output_tokens counts THINKING + actual output for Gemini 3.1
        # Pro. Pilot 4 v1 hit a ~63% failure rate at 8192 because MEDIUM
        # thinking consumes 5-8K, leaving < 2K for the HTML — which then
        # got truncated mid-document and rejected as malformed_html_response.
        # 16384 gives comfortable headroom for the actual HTML to emit.
        max_output_tokens=16384,
        thinking_config=types.ThinkingConfig(thinking_level="MEDIUM"),
    )
    t0 = time.time()
    deadline = GEN_TIMEOUT_S_RETRY if attempt > 0 else GEN_TIMEOUT_S
    err_log: str | None = None
    try:
        resp = await asyncio.wait_for(
            client.aio.models.generate_content(
                model=MODEL_ID,
                contents=user_prompt,
                config=config,
            ),
            timeout=deadline,
        )
    except asyncio.TimeoutError:
        err_log = f"timeout_{deadline:.0f}s"
        log_api_call(phase="generate", model=MODEL_ID, sample_id=sample_id,
                     tokens_in=None, tokens_out=None,
                     elapsed_s=time.time() - t0, error=err_log,
                     extra={"attempt": attempt})
        return None, {"error": err_log, "elapsed": time.time() - t0}
    except Exception as e:
        err_log = str(e)[:300]
        log_api_call(phase="generate", model=MODEL_ID, sample_id=sample_id,
                     tokens_in=None, tokens_out=None,
                     elapsed_s=time.time() - t0, error=err_log,
                     extra={"attempt": attempt})
        return None, {"error": err_log, "elapsed": time.time() - t0}

    elapsed = time.time() - t0
    text = (resp.text or "").strip()
    html = strip_fences(text)
    usage = extract_usage_from_genai_response(resp)
    meta = {"elapsed": elapsed, **usage}
    well_formed = is_well_formed_html(html)
    log_api_call(
        phase="generate", model=MODEL_ID, sample_id=sample_id,
        tokens_in=usage.get("tokens_in"),
        tokens_out=usage.get("tokens_out"),
        tokens_thinking=usage.get("tokens_thinking"),
        tokens_cache=usage.get("tokens_cache"),
        elapsed_s=elapsed,
        error=None if well_formed else "malformed_html_response",
        extra={"attempt": attempt},
    )
    if not well_formed:
        return None, {**meta, "error": "malformed_html_response", "head": html[:200]}
    return html, meta


async def worker(name: int, queue: asyncio.Queue, client, stats: dict):
    while True:
        item = await queue.get()
        if item is None:
            queue.task_done()
            break
        brief: Brief = item
        spec = CONFIG["design_types"][brief.design_type]
        brief_dict = dataclasses.asdict(brief)

        sample_id_for_log = brief.id()
        html, meta = await call_gemini(client, brief_dict, spec, attempt=0, sample_id=sample_id_for_log)
        if html is None:
            html, meta2 = await call_gemini(client, brief_dict, spec, attempt=1, sample_id=sample_id_for_log)
            if html is None:
                stats["failed"] += 1
                log_event({
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "event": "generation_failed",
                    "brief_id": brief.id(),
                    "design_type": brief.design_type,
                    "errors": [meta, meta2],
                })
                queue.task_done()
                continue

        sample_id = brief.id()
        record = {
            "id": sample_id,
            "design_type": brief.design_type,
            "brief": brief_dict,
            "html": html,
            "teacher_model": MODEL_ID,
            "teacher_temperature": 1.0,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "teacher_meta": meta,
        }
        try:
            write_sample(record)
        except Exception as e:
            stats["failed"] += 1
            log_event({"event": "write_failed", "id": sample_id, "error": str(e)})
            queue.task_done()
            continue

        stats["ok"] += 1
        if stats["ok"] % 25 == 0:
            elapsed = time.time() - stats["t0"]
            rate = stats["ok"] / max(elapsed, 0.001)
            print(f"  [{stats['ok']} ok / {stats['failed']} fail] {rate:.2f}/s")
        queue.task_done()


async def main_async(args):
    targets = proportional_plan(args.plan_size)
    print(f"plan: {args.plan_size} samples")
    for k, v in targets.items():
        print(f"  {k}: {v}")

    plan = build_plan(targets, seed=args.seed)
    # Resume support: drop briefs whose id already has a file on disk.
    type_to_ids: dict[str, set[str]] = {t: existing_ids(t) for t in targets}
    plan = [b for b in plan if b.id() not in type_to_ids[b.design_type]]
    print(f"after resume filter: {len(plan)} briefs to generate")
    if args.dry_run:
        print("dry-run — exiting before any API calls.")
        return 0

    api_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("ERROR: set GOOGLE_API_KEY (or GEMINI_API_KEY) in env first.", file=sys.stderr)
        return 1

    from google import genai  # type: ignore
    from google.genai import types  # type: ignore
    # HTTP-LAYER TIMEOUT (the one that actually works).
    # asyncio.wait_for raises CancelledError into the awaiter but the SDK
    # keeps holding the httpx connection — pilot 1 and pilot 2 both stalled
    # for this exact reason. Setting timeout on HttpOptions forces httpx to
    # cut the socket on the deadline, which propagates as a real exception.
    client = genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(timeout=int(GEN_TIMEOUT_S_RETRY * 1000)),
    )

    queue: asyncio.Queue = asyncio.Queue(maxsize=args.concurrency * 4)
    stats = {"ok": 0, "failed": 0, "t0": time.time()}

    async def producer():
        for b in plan:
            await queue.put(b)
        for _ in range(args.concurrency):
            await queue.put(None)

    prod = asyncio.create_task(producer())
    workers = [
        asyncio.create_task(worker(i, queue, client, stats))
        for i in range(args.concurrency)
    ]
    await prod
    await queue.join()
    for w in workers:
        await w

    dt = time.time() - stats["t0"]
    print(f"\n=== generation done in {dt:.1f}s ===")
    print(f"  ok:     {stats['ok']}")
    print(f"  failed: {stats['failed']}")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan-size", type=int, default=10000)
    ap.add_argument("--concurrency", type=int, default=6)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--dry-run", action="store_true", help="Print plan only, no API calls.")
    ap.add_argument("--resume", action="store_true", help="Default behavior — kept for explicitness.")
    args = ap.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())
