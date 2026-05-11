"""Phase-3.5: repair rejected samples by feeding the validator's feedback
back to Gemini 3.1 Pro and asking for a corrected HTML.

Reads samples from data/rejected/ that have a sibling .validation.json with
either mechanical violations or visual-judge issues. For each:

  1. Build a repair prompt with the brief, the previous HTML, ALL Stage-1
     violations, AND any Stage-2 visual-judge issues + lowest score axes.
  2. Call Gemini 3.1 Pro to get a corrected HTML.
  3. Update the sample record in place:
       - sample.html = new_html
       - sample.repair_history.append({attempt, previous_html, ...})
     The original html is preserved inside repair_history[0].previous_html,
     so we keep before/after pairs for free (useful for DPO later).
  4. Delete the stale .validation.json sibling so the pipeline re-validates
     this sample in the next step.

Samples whose `repair_history` already has `max_attempts` entries are skipped.

Usage (typically called by scripts\\run_pipeline.ps1, not by hand):
    python generators\\repair.py --input data\\rejected --max-attempts 2 \\
                                 --concurrency 4
"""
from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from generators.generate import (  # noqa: E402
    MODEL_ID,
    strip_fences,
    is_well_formed_html,
    GEN_TIMEOUT_S,
    GEN_TIMEOUT_S_RETRY,
)
from generators.system_prompts import BASE_SYSTEM, render_repair_prompt  # noqa: E402
from generators.api_log import log_api_call, extract_usage_from_genai_response  # noqa: E402

CONFIG = json.loads((REPO_ROOT / "configs" / "design_types.json").read_text(encoding="utf-8"))
LOG_PATH = REPO_ROOT / "logs" / "repair_log.jsonl"


def _summarize_violations(violations: list[dict]) -> list[dict]:
    """Trim each violation for storage in repair_history (drops huge rect dumps)."""
    out = []
    for v in violations[:50]:
        keep = {"kind": v.get("kind")}
        for k in ("path", "expected", "actual", "contrast_ratio", "font_size_px", "overlap_px"):
            if k in v:
                keep[k] = v[k]
        out.append(keep)
    return out


def collect_repair_candidates(rejected_dir: Path, max_attempts: int) -> list[tuple[Path, Path]]:
    pairs: list[tuple[Path, Path]] = []
    for sample_path in rejected_dir.rglob("*.json"):
        if sample_path.name.endswith(".validation.json"):
            continue
        report_path = sample_path.with_suffix(".validation.json")
        if not report_path.exists():
            continue
        try:
            sample = json.loads(sample_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        attempts_so_far = len(sample.get("repair_history", []))
        if attempts_so_far >= max_attempts:
            continue
        # Must have actionable feedback
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        has_mech = bool(report.get("violations"))
        vj = report.get("visual_judge") or {}
        has_visual = bool(vj.get("issues")) or (vj.get("scores") and any(s < 7 for s in vj["scores"].values()))
        if not (has_mech or has_visual):
            continue
        pairs.append((sample_path, report_path))
    return pairs


async def _call_once(client, user_prompt: str, attempt: int, sample_id: str | None = None) -> tuple[str | None, dict]:
    from google.genai import types  # type: ignore

    # Slightly lower temperature than initial generation — we want fix, not invention.
    config = types.GenerateContentConfig(
        system_instruction=BASE_SYSTEM,
        temperature=0.5 if attempt == 0 else 0.3,
        top_p=0.9,
        # See generate.py for why 16384 (thinking + output share this budget).
        max_output_tokens=16384,
        thinking_config=types.ThinkingConfig(thinking_level="MEDIUM"),
    )
    deadline = GEN_TIMEOUT_S_RETRY if attempt > 0 else GEN_TIMEOUT_S
    t0 = time.time()
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
        err = f"timeout_{deadline:.0f}s"
        log_api_call(phase="repair", model=MODEL_ID, sample_id=sample_id,
                     tokens_in=None, tokens_out=None,
                     elapsed_s=time.time() - t0, error=err,
                     extra={"attempt": attempt})
        return None, {"error": err, "elapsed": time.time() - t0}
    except Exception as e:
        err = str(e)[:300]
        log_api_call(phase="repair", model=MODEL_ID, sample_id=sample_id,
                     tokens_in=None, tokens_out=None,
                     elapsed_s=time.time() - t0, error=err,
                     extra={"attempt": attempt})
        return None, {"error": err, "elapsed": time.time() - t0}

    elapsed = time.time() - t0
    text = (resp.text or "").strip()
    html = strip_fences(text)
    usage = extract_usage_from_genai_response(resp)
    meta = {"elapsed": elapsed, **usage}
    well_formed = is_well_formed_html(html)
    log_api_call(
        phase="repair", model=MODEL_ID, sample_id=sample_id,
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


async def call_gemini_repair(client, sample: dict, report: dict, spec: dict) -> tuple[str | None, dict]:
    brief = sample["brief"]
    mech = report.get("violations") or []
    vj = report.get("visual_judge") or {}
    visual_issues = vj.get("issues") or []
    visual_scores = vj.get("scores") or {}

    user_prompt = render_repair_prompt(
        brief, spec, sample["html"],
        mechanical_violations=mech,
        visual_issues=visual_issues,
        visual_scores=visual_scores,
    )
    # One retry on timeout / malformed response — keeps the worker pool flowing
    # if a single call hangs on the API side.
    sid = sample.get("id")
    html, meta = await _call_once(client, user_prompt, attempt=0, sample_id=sid)
    if html is None:
        html, meta2 = await _call_once(client, user_prompt, attempt=1, sample_id=sid)
        if html is None:
            return None, {"error": "retry_exhausted", "first": meta, "second": meta2}
        meta2["retried_from"] = meta.get("error")
        return html, meta2
    return html, meta


async def repair_one(client, sample_path: Path, report_path: Path) -> dict:
    try:
        sample = json.loads(sample_path.read_text(encoding="utf-8"))
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except Exception as e:
        return {"error": f"read_failed: {e}"}

    design_type = sample.get("design_type")
    spec = CONFIG["design_types"].get(design_type)
    if spec is None:
        return {"error": f"unknown_design_type: {design_type}"}

    new_html, meta = await call_gemini_repair(client, sample, report, spec)
    if new_html is None:
        return {"error": "gemini_call_failed", "meta": meta}

    history = sample.get("repair_history") or []
    history.append({
        "attempt": len(history) + 1,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "previous_html": sample["html"],
        "violations_fed": _summarize_violations(report.get("violations") or []),
        "visual_issues_fed": (report.get("visual_judge") or {}).get("issues") or [],
        "visual_scores_at_attempt": (report.get("visual_judge") or {}).get("scores"),
        "model": MODEL_ID,
        "meta": meta,
    })
    sample["repair_history"] = history
    sample["html"] = new_html
    sample["repaired_at"] = datetime.now(timezone.utc).isoformat()

    sample_path.write_text(json.dumps(sample, indent=2), encoding="utf-8")
    # Critical: invalidate the old report so the pipeline re-validates the new html
    try:
        report_path.unlink()
    except FileNotFoundError:
        pass
    return {"ok": True, "attempt": history[-1]["attempt"]}


async def worker(name: int, queue: asyncio.Queue, client, stats: dict):
    while True:
        item = await queue.get()
        if item is None:
            queue.task_done()
            break
        sample_path, report_path = item
        try:
            res = await repair_one(client, sample_path, report_path)
        except Exception as e:
            res = {"error": f"worker_exception: {str(e)[:200]}"}
        if "ok" in res:
            stats["ok"] += 1
        else:
            stats["failed"] += 1
            log_event = {
                "ts": datetime.now(timezone.utc).isoformat(),
                "event": "repair_failed",
                "sample": str(sample_path.relative_to(REPO_ROOT)),
                "result": res,
            }
            LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            with LOG_PATH.open("a", encoding="utf-8") as f:
                f.write(json.dumps(log_event) + "\n")
        stats["seen"] += 1
        if stats["seen"] % 25 == 0:
            print(f"  [{stats['seen']}] repaired={stats['ok']} failed={stats['failed']}")
        queue.task_done()


async def main_async(args):
    rejected_dir = Path(args.input)
    pairs = collect_repair_candidates(rejected_dir, args.max_attempts)
    print(f"to repair: {len(pairs)} samples (max_attempts={args.max_attempts})")
    if not pairs:
        return 0

    api_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("ERROR: set GOOGLE_API_KEY first", file=sys.stderr)
        return 1
    from google import genai  # type: ignore
    from google.genai import types  # type: ignore
    # Same httpx-layer timeout as generate.py — see comment there.
    client = genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(timeout=int(GEN_TIMEOUT_S_RETRY * 1000)),
    )

    queue: asyncio.Queue = asyncio.Queue(maxsize=args.concurrency * 4)
    stats = {"seen": 0, "ok": 0, "failed": 0}
    t0 = time.time()

    async def producer():
        for p in pairs:
            await queue.put(p)
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

    dt = time.time() - t0
    print(f"\n=== repair pass done in {dt:.1f}s ===")
    print(f"  repaired: {stats['ok']}")
    print(f"  failed:   {stats['failed']}")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="data/rejected")
    ap.add_argument("--max-attempts", type=int, default=2,
                    help="skip samples whose repair_history already has this many entries")
    ap.add_argument("--concurrency", type=int, default=4)
    args = ap.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())
