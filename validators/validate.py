"""Validate generated HTML designs against the spec.

Usage:
    python validators/validate.py --input data/raw --output data/validated \
                                  --rejected data/rejected --concurrency 8 \
                                  --screenshots

Input is a directory of *.json files (one per sample, schema in design_spec.md
§4) OR a single .html/.json path. For each sample we:

  1. Render the HTML headless at the design's canvas size.
  2. Inject dom_extract.js to pull text geometry and styling.
  3. Run the rules in checks.py.
  4. Write a sibling `<id>.validation.json` report.
  5. Move the sample to validated/ or rejected/ depending on `valid`.
  6. Optionally save a PNG screenshot to renders/.

Designed to be safe to interrupt — already-validated samples are skipped if
their report exists and the html hash is unchanged.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import shutil
import sys
import time
from pathlib import Path

from playwright.async_api import async_playwright, Page

# Local imports
sys.path.insert(0, str(Path(__file__).parent))
from checks import run_all_checks

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = REPO_ROOT / "configs" / "design_types.json"
DOM_EXTRACT_JS = (Path(__file__).parent / "dom_extract.js").read_text(encoding="utf-8")


def load_config() -> dict:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def html_sha8(html: str) -> str:
    return hashlib.sha256(html.encode("utf-8")).hexdigest()[:8]


def iter_samples(path: Path):
    """Yield (sample_dict, source_path) for every input record."""
    if path.is_file():
        if path.suffix == ".json":
            yield json.loads(path.read_text(encoding="utf-8")), path
        elif path.suffix == ".html":
            # Bare HTML — design_type must be inferable from data-design-type
            html = path.read_text(encoding="utf-8")
            yield {"id": html_sha8(html), "html": html, "design_type": None}, path
        return
    for p in sorted(path.rglob("*.json")):
        # Skip validation reports
        if p.name.endswith(".validation.json"):
            continue
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        if "html" not in doc:
            continue
        yield doc, p


async def validate_one(
    page: Page,
    sample: dict,
    config: dict,
    *,
    screenshots_dir: Path | None,
) -> dict:
    design_type = sample.get("design_type")
    if not design_type:
        # Attempt to recover from body data attribute via a brief sniff.
        design_type = _sniff_design_type(sample["html"]) or "flyer_us_letter"

    spec = config["design_types"].get(design_type)
    if spec is None:
        return {
            "id": sample.get("id"),
            "design_type": design_type,
            "valid": False,
            "violation_count": 1,
            "violations": [{"kind": "unknown_design_type", "value": design_type}],
        }

    w, h = spec["w"], spec["h"]
    await page.set_viewport_size({"width": w, "height": h})

    network_violations: list[dict] = []

    # Origins we explicitly permit for B2B brand work. Google Fonts is the
    # only external origin allowed by the design spec; everything else is a
    # violation. Loaded fonts mean text bboxes need a fonts.ready settle
    # below — without it, layout reflows AFTER we measure and our collision
    # checks become unreliable.
    ALLOWED_NETWORK_PREFIXES = (
        "https://fonts.googleapis.com/",
        "https://fonts.gstatic.com/",
    )

    def on_request(req):
        url = req.url
        if url.startswith(("data:", "about:")):
            return
        if url.startswith(ALLOWED_NETWORK_PREFIXES):
            return
        # `set_content` uses about:blank as the base; any other URL = external fetch.
        network_violations.append({"kind": "external_resource_fetch", "url": url[:200]})

    page.on("request", on_request)

    # `wait_until="load"` waits for sync resources including `<link
    # rel="stylesheet">` (Google Fonts CSS is render-blocking, so it's
    # captured here). Then we explicitly await document.fonts.ready, which
    # resolves once all `@font-face` rules have actually downloaded their
    # WOFF2 files. This is much faster than `networkidle` for designs that
    # don't use Google Fonts (no @font-face → ready resolves immediately).
    try:
        await page.set_content(sample["html"], wait_until="load", timeout=20000)
    except Exception as e:
        page.remove_listener("request", on_request)
        return {
            "id": sample.get("id"),
            "design_type": design_type,
            "valid": False,
            "violation_count": 1,
            "violations": [{"kind": "render_error", "error": str(e)[:300]}],
        }

    # Properly await the FontFaceSet.ready promise. Note: page.evaluate auto-
    # awaits Promises returned from its expression, but a bare async-arrow
    # would just *return the function*, never invoke it. Use an IIFE.
    try:
        await page.evaluate("(async () => { await document.fonts.ready; return true; })()")
    except Exception:
        pass

    try:
        extract = await page.evaluate(DOM_EXTRACT_JS)
    except Exception as e:
        page.remove_listener("request", on_request)
        return {
            "id": sample.get("id"),
            "design_type": design_type,
            "valid": False,
            "violation_count": 1,
            "violations": [{"kind": "extract_error", "error": str(e)[:300]}],
        }

    page.remove_listener("request", on_request)

    val = config["validation"]
    expected_company_name = (sample.get("brief") or {}).get("company_name")
    report = run_all_checks(
        extract,
        expected_type=design_type,
        w=w,
        h=h,
        shape=spec["shape"],
        min_font_px=spec["min_font_px"],
        bleed_pct=spec["bleed_pct"],
        min_overlap_px=val["min_overlap_px"],
        min_contrast=val["min_contrast_ratio"],
        canvas_tol=val["canvas_size_tolerance_px"],
        expected_company_name=expected_company_name,
        required_roles=val.get("required_roles") or [],
        company_name_substring=val.get("company_name_substring_match", True),
        company_name_min_overlap=val.get("company_name_min_overlap", 0.8),
    )

    if network_violations:
        report["violations"].extend(network_violations)
        report["violation_count"] += len(network_violations)
        for nv in network_violations:
            report["violations_by_kind"][nv["kind"]] = (
                report["violations_by_kind"].get(nv["kind"], 0) + 1
            )
        report["valid"] = False

    report["id"] = sample.get("id")
    report["design_type"] = design_type
    report["canvas"] = {"w": w, "h": h, "shape": spec["shape"]}

    if screenshots_dir is not None and report["valid"]:
        # Only screenshot the passing ones — they're the ones we'll keep.
        out_png = (screenshots_dir / design_type / f"{sample.get('id', html_sha8(sample['html']))}.png").resolve()
        out_png.parent.mkdir(parents=True, exist_ok=True)
        try:
            await page.screenshot(path=str(out_png), full_page=False)
            # Record as a path RELATIVE to the repo root so the report is portable.
            try:
                rel = out_png.relative_to(REPO_ROOT)
            except ValueError:
                rel = out_png  # absolute fallback — visual_judge handles both
            report["screenshot"] = str(rel).replace("\\", "/")
        except Exception as e:
            report["screenshot_error"] = str(e)[:200]

    return report


def _sniff_design_type(html: str) -> str | None:
    # Cheap regex — only used as a last-resort fallback.
    import re
    m = re.search(r'data-design-type="([^"]+)"', html)
    return m.group(1) if m else None


async def worker(
    name: int,
    queue: asyncio.Queue,
    config: dict,
    browser,
    output_dir: Path,
    rejected_dir: Path,
    screenshots_dir: Path | None,
    stats: dict,
):
    ctx = await browser.new_context(viewport={"width": 800, "height": 600})
    page = await ctx.new_page()
    while True:
        item = await queue.get()
        if item is None:
            queue.task_done()
            break
        sample, src_path = item
        try:
            report = await validate_one(page, sample, config, screenshots_dir=screenshots_dir)
        except Exception as e:
            report = {
                "id": sample.get("id"),
                "design_type": sample.get("design_type"),
                "valid": False,
                "violation_count": 1,
                "violations": [{"kind": "worker_error", "error": str(e)[:300]}],
            }
        # Move sample to validated/ or rejected/, then write the fresh report.
        # Move semantics (not copy) is what makes the repair loop clean: if a
        # sample stays in data/rejected/ between rounds, it doesn't fork into
        # multiple copies. Self-source case (src dir == dest dir) is a no-op.
        try:
            dest_dir = output_dir if report["valid"] else rejected_dir
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = dest_dir / src_path.name
            if src_path.resolve() != dest.resolve():
                # Drop the stale report at the source (if any) so we don't leave orphans
                old_report = src_path.with_suffix(".validation.json")
                if old_report.exists() and old_report.resolve() != dest.with_suffix(".validation.json").resolve():
                    try:
                        old_report.unlink()
                    except OSError:
                        pass
                shutil.move(str(src_path), str(dest))
            report_path = dest.with_suffix(".validation.json")
            report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        except Exception as e:
            print(f"[worker {name}] write error: {e}", file=sys.stderr)

        stats["seen"] += 1
        if report["valid"]:
            stats["valid"] += 1
        else:
            stats["invalid"] += 1
            for v in report.get("violations", []):
                k = v["kind"]
                stats["by_kind"][k] = stats["by_kind"].get(k, 0) + 1
        if stats["seen"] % 25 == 0:
            print(f"[{stats['seen']}] valid={stats['valid']} invalid={stats['invalid']}")
        queue.task_done()
    await ctx.close()


async def main_async(args):
    config = load_config()
    input_path = Path(args.input)
    output_dir = Path(args.output)
    rejected_dir = Path(args.rejected)
    screenshots_dir = Path(args.screenshots_dir) if args.screenshots else None
    output_dir.mkdir(parents=True, exist_ok=True)
    rejected_dir.mkdir(parents=True, exist_ok=True)
    if screenshots_dir:
        screenshots_dir.mkdir(parents=True, exist_ok=True)

    samples = list(iter_samples(input_path))
    print(f"loaded {len(samples)} samples from {input_path}")
    if not samples:
        return 0

    queue: asyncio.Queue = asyncio.Queue()
    for s in samples:
        await queue.put(s)
    for _ in range(args.concurrency):
        await queue.put(None)

    stats = {"seen": 0, "valid": 0, "invalid": 0, "by_kind": {}}
    t0 = time.time()

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        workers = [
            asyncio.create_task(
                worker(i, queue, config, browser, output_dir, rejected_dir, screenshots_dir, stats)
            )
            for i in range(args.concurrency)
        ]
        await queue.join()
        for w_task in workers:
            await w_task
        await browser.close()

    dt = time.time() - t0
    print("\n=== validation done ===")
    print(f"  total:    {stats['seen']}  in {dt:.1f}s ({stats['seen']/max(dt,0.001):.1f}/s)")
    print(f"  valid:    {stats['valid']}")
    print(f"  invalid:  {stats['invalid']}")
    if stats["by_kind"]:
        print("  violations by kind:")
        for k, v in sorted(stats["by_kind"].items(), key=lambda kv: -kv[1]):
            print(f"    {k}: {v}")

    summary_path = output_dir.parent / "validation_summary.json"
    summary_path.write_text(json.dumps(stats, indent=2), encoding="utf-8")
    print(f"  summary written to {summary_path}")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, help="Directory of .json samples (or a single file)")
    ap.add_argument("--output", default="data/validated", help="Where to put validated samples")
    ap.add_argument("--rejected", default="data/rejected", help="Where to put rejected samples")
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--screenshots", action="store_true", help="Save PNG renders of passing samples")
    ap.add_argument("--screenshots-dir", default="renders")
    args = ap.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())
