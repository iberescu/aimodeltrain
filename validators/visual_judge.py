"""Stage-2 visual judge.

Stage-1 (`validators/validate.py`) checks mechanical rules: DOM-bbox text
collisions, off-canvas text, font sizes, contrast, canvas size. It's
deterministic and cheap.

Stage-2 catches what rules cannot:
  - "is this design ACTUALLY readable / aesthetically coherent?"
  - "does it match the brief (industry, tone, layout archetype)?"
  - "are there visible issues a human would call out — bad font pairings,
    color clashes, weird empty regions, awkward CTAs, garbled copy?"

For each sample that passed Stage-1, we feed the rendered PNG + the brief to
a multimodal LLM and get a structured JSON judgment. We then enforce a
threshold: samples below it get demoted to data/rejected/.

Provider: Gemini 3.1 Pro by default (user-chosen). NOTE: same model
generated AND judged means correlated blind spots. If reliability matters
later, swap the judge to a different family (claude / gpt-vision).

Usage:
    python validators/visual_judge.py --input data/validated --threshold 7
    python validators/visual_judge.py --input data/validated --threshold 7 \
                                      --provider gemini --concurrency 4
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
RENDERS_DIR = REPO_ROOT / "renders"
REJECTED_DIR = REPO_ROOT / "data" / "rejected"

# Add the project root to path so we can import the api_log helper from
# generators/. (visual_judge lives in validators/, which isn't a package
# relative to generators/ from the runner's CWD.)
sys.path.insert(0, str(REPO_ROOT))
from generators.api_log import log_api_call, extract_usage_from_genai_response  # noqa: E402

MODEL_GEMINI = "gemini-3.1-pro-preview"
MODEL_ANTHROPIC = "claude-opus-4-7"

JUDGE_SYSTEM = """You are a meticulous senior B2B brand designer auditing one
generated design at a time. The design is a B2B marketing/brand asset (flyer,
business card, sticker, poster, social post, ad) for a fictitious B2B
company. You will see ONE rendered design (PNG) and the original brief that
requested it. Return ONE JSON object — no prose around it, no markdown
fences — with the schema:

{
  "scores": {
    "brief_adherence":  int 1-10,   // matches vertical/audience/tone/content?
    "brand_presence":   int 1-10,   // is logo clearly visible AND is company name prominent and matching?
    "typography":       int 1-10,   // hierarchy, font choice, readability
    "layout_balance":   int 1-10,   // composition, whitespace, visual weight
    "color_harmony":    int 1-10,   // palette use, contrast that READS well
    "b2b_register":     int 1-10,   // does it FEEL B2B (professional, trust-signaling) not B2C/consumer?
    "overall":          int 1-10    // would a B2B brand studio ship this?
  },
  "issues": [
    {
      "kind": "text_collision" | "text_clipping" | "missing_logo" |
              "tiny_or_hidden_logo" | "wrong_company_name" |
              "ugly_typography" | "color_clash" | "awkward_empty_space" |
              "off_brief" | "low_legibility" | "garbled_text" |
              "broken_layout" | "b2c_register" | "other",
      "severity": "low" | "med" | "high",
      "note": "one sentence explanation"
    }
  ],
  "ship": true | false
}

Be strict.
- A 7 means "competent". An 8 means "good". 9 means "great". 10 is
  portfolio-grade.
- If you see ANY text overlapping other text, set ship=false and add a
  text_collision issue with severity "high".
- If the logo is missing, hidden, or absurdly tiny, set ship=false and
  add a tiny_or_hidden_logo (or missing_logo) issue with severity "high".
- If the rendered company name does not match the brief's company_name,
  set ship=false and add wrong_company_name with severity "high".
- If the design feels consumer / B2C ("treat yourself", "cozy weekends")
  rather than B2B, add a b2c_register issue and lower b2b_register."""


def encode_png_b64(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode("ascii")


def find_screenshot(validation_report: dict) -> Path | None:
    rel = validation_report.get("screenshot")
    if not rel:
        return None
    p = Path(rel)
    if not p.is_absolute():
        p = REPO_ROOT / p
    return p if p.exists() else None


def build_brief_summary(sample: dict) -> str:
    b = sample.get("brief", {})
    canvas = sample.get("canvas") or {}
    return json.dumps(
        {
            "design_type": sample.get("design_type"),
            "canvas": canvas,
            "company_name": b.get("company_name"),
            "logo_concept": b.get("logo_concept"),
            "vertical": b.get("vertical"),
            "audience": b.get("audience"),
            "value_prop": b.get("value_prop"),
            "tone": b.get("tone"),
            "palette_name": b.get("palette_name"),
            "palette_hex": b.get("palette_hex"),
            "layout": b.get("layout"),
            "content_template": b.get("content_template"),
        },
        indent=2,
    )


# ---------- providers ----------

async def judge_gemini(client, png_bytes: bytes, brief_summary: str, sample_id: str | None = None) -> dict:
    from google.genai import types  # type: ignore
    config = types.GenerateContentConfig(
        system_instruction=JUDGE_SYSTEM,
        temperature=0.2,
        max_output_tokens=2048,
        response_mime_type="application/json",
    )
    contents = [
        types.Part.from_bytes(data=png_bytes, mime_type="image/png"),
        f"Brief that produced this design:\n{brief_summary}\n\nReturn the JSON now.",
    ]
    t0 = time.time()
    try:
        resp = await client.aio.models.generate_content(
            model=MODEL_GEMINI, contents=contents, config=config
        )
    except Exception as e:
        log_api_call(phase="judge_gemini", model=MODEL_GEMINI, sample_id=sample_id,
                     tokens_in=None, tokens_out=None,
                     elapsed_s=time.time() - t0, error=str(e)[:300])
        raise
    elapsed = time.time() - t0
    usage = extract_usage_from_genai_response(resp)
    log_api_call(
        phase="judge_gemini", model=MODEL_GEMINI, sample_id=sample_id,
        tokens_in=usage.get("tokens_in"),
        tokens_out=usage.get("tokens_out"),
        tokens_thinking=usage.get("tokens_thinking"),
        tokens_cache=usage.get("tokens_cache"),
        elapsed_s=elapsed,
        error=None,
    )
    return json.loads((resp.text or "").strip())


async def judge_anthropic(client, png_bytes: bytes, brief_summary: str, sample_id: str | None = None) -> dict:
    b64 = base64.standard_b64encode(png_bytes).decode("ascii")
    t0 = time.time()
    try:
        resp = await client.messages.create(
            model=MODEL_ANTHROPIC,
            max_tokens=2048,
            system=JUDGE_SYSTEM,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "image",
                         "source": {"type": "base64", "media_type": "image/png", "data": b64}},
                        {"type": "text",
                         "text": f"Brief that produced this design:\n{brief_summary}\n\nReturn the JSON now."},
                    ],
                }
            ],
        )
    except Exception as e:
        log_api_call(phase="judge_anthropic", model=MODEL_ANTHROPIC, sample_id=sample_id,
                     tokens_in=None, tokens_out=None,
                     elapsed_s=time.time() - t0, error=str(e)[:300])
        raise
    elapsed = time.time() - t0
    log_api_call(
        phase="judge_anthropic", model=MODEL_ANTHROPIC, sample_id=sample_id,
        tokens_in=getattr(resp.usage, "input_tokens", 0) if hasattr(resp, "usage") else 0,
        tokens_out=getattr(resp.usage, "output_tokens", 0) if hasattr(resp, "usage") else 0,
        tokens_cache=getattr(resp.usage, "cache_read_input_tokens", 0) if hasattr(resp, "usage") else 0,
        elapsed_s=elapsed,
        error=None,
    )
    text = "".join(block.text for block in resp.content if hasattr(block, "text")).strip()
    # Tolerate any markdown fences
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip("` \n")
    return json.loads(text)


# ---------- driver ----------

async def judge_one(provider: str, client, sample_path: Path, report_path: Path, threshold: int) -> dict:
    try:
        sample = json.loads(sample_path.read_text(encoding="utf-8"))
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except Exception as e:
        return {"error": f"read_failed: {e}"}

    png_path = find_screenshot(report)
    if png_path is None:
        return {"error": "no_screenshot"}

    png_bytes = png_path.read_bytes()
    brief_summary = build_brief_summary(sample)

    sid = sample.get("id")
    try:
        if provider == "gemini":
            judgment = await judge_gemini(client, png_bytes, brief_summary, sample_id=sid)
        elif provider == "anthropic":
            judgment = await judge_anthropic(client, png_bytes, brief_summary, sample_id=sid)
        else:
            return {"error": f"unknown_provider: {provider}"}
    except Exception as e:
        return {"error": f"api_failed: {str(e)[:200]}"}

    report["visual_judge"] = {
        "provider": provider,
        "judged_at": datetime.now(timezone.utc).isoformat(),
        **judgment,
    }
    overall = judgment.get("scores", {}).get("overall", 0)
    ship = bool(judgment.get("ship", False))
    passed = overall >= threshold and ship

    report["visual_judge"]["passed"] = passed
    report["overall_pass"] = bool(report.get("valid", False)) and passed
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    if not passed:
        # Demote: move sample + report to data/rejected/<design_type>/
        dest_dir = REJECTED_DIR / sample.get("design_type", "unknown")
        dest_dir.mkdir(parents=True, exist_ok=True)
        shutil.move(str(sample_path), str(dest_dir / sample_path.name))
        shutil.move(str(report_path), str(dest_dir / report_path.name))

    return {"passed": passed, "overall": overall, "ship": ship}


async def worker(name: int, queue: asyncio.Queue, provider: str, client, threshold: int, stats: dict):
    while True:
        item = await queue.get()
        if item is None:
            queue.task_done()
            break
        sample_path, report_path = item
        try:
            res = await judge_one(provider, client, sample_path, report_path, threshold)
        except Exception as e:
            res = {"error": str(e)[:200]}
        if "error" in res:
            stats["errored"] += 1
        elif res.get("passed"):
            stats["passed"] += 1
        else:
            stats["demoted"] += 1
        stats["seen"] += 1
        if stats["seen"] % 25 == 0:
            print(f"  [{stats['seen']}] pass={stats['passed']} demote={stats['demoted']} err={stats['errored']}")
        queue.task_done()


def collect_inputs(validated_dir: Path) -> list[tuple[Path, Path]]:
    pairs = []
    for sample_path in validated_dir.rglob("*.json"):
        if sample_path.name.endswith(".validation.json"):
            continue
        report_path = sample_path.with_suffix(".validation.json")
        if not report_path.exists():
            continue
        # Skip already-judged
        try:
            existing = json.loads(report_path.read_text(encoding="utf-8"))
            if "visual_judge" in existing:
                continue
        except Exception:
            pass
        pairs.append((sample_path, report_path))
    return pairs


async def main_async(args):
    validated_dir = Path(args.input)
    pairs = collect_inputs(validated_dir)
    print(f"to judge: {len(pairs)} samples")
    if not pairs:
        return 0

    provider = args.provider
    if provider == "gemini":
        api_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
        if not api_key:
            print("ERROR: set GOOGLE_API_KEY for gemini judge", file=sys.stderr)
            return 1
        from google import genai  # type: ignore
        client = genai.Client(api_key=api_key)
    elif provider == "anthropic":
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            print("ERROR: set ANTHROPIC_API_KEY for anthropic judge", file=sys.stderr)
            return 1
        import anthropic  # type: ignore
        client = anthropic.AsyncAnthropic(api_key=api_key)
    else:
        print(f"unknown provider: {provider}", file=sys.stderr)
        return 1

    queue: asyncio.Queue = asyncio.Queue(maxsize=args.concurrency * 4)
    stats = {"seen": 0, "passed": 0, "demoted": 0, "errored": 0}
    t0 = time.time()

    async def producer():
        for item in pairs:
            await queue.put(item)
        for _ in range(args.concurrency):
            await queue.put(None)

    prod = asyncio.create_task(producer())
    workers = [
        asyncio.create_task(worker(i, queue, provider, client, args.threshold, stats))
        for i in range(args.concurrency)
    ]
    await prod
    await queue.join()
    for w in workers:
        await w

    dt = time.time() - t0
    print(f"\n=== visual judge done in {dt:.1f}s ===")
    print(f"  passed:  {stats['passed']}")
    print(f"  demoted: {stats['demoted']}")
    print(f"  errored: {stats['errored']}")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="data/validated")
    ap.add_argument("--provider", choices=["gemini", "anthropic"], default="gemini")
    ap.add_argument("--threshold", type=int, default=7, help="overall score floor (1-10)")
    ap.add_argument("--concurrency", type=int, default=4)
    args = ap.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())
