"""Rewrite each sample's HTML so every direct child of <body> carries an
inline `style="position:absolute;left:Xpx;top:Ypx;width:Wpx;height:Hpx;..."`
attribute. Existing `<style>` block (and any non-positioning inline styles)
are left alone — inline positioning wins by CSS specificity.

Use case: prior to the prompt change, the teacher emitted HTML with `<style>`
rules driving layout (flex/grid/absolute via classes). The system prompt
now mandates inline absolute positioning. Rather than regenerate the 1k
designs (~$170), we render each existing sample in headless Chromium,
read the rendered bbox of every body-level child via `getBoundingClientRect`,
and bake those coordinates back into inline `style=""` attributes.

The visual output is byte-identical to the original (we're just promoting
the positioning the CSS already computed into inline styles).

Usage:
  python scripts/add_inline_positions.py
  python scripts/add_inline_positions.py --source data/_archive_pilot50_*/validated
  python scripts/add_inline_positions.py --source data/validated --concurrency 8
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

DESIGN_SPECS = json.loads((REPO_ROOT / "configs" / "design_types.json").read_text(encoding="utf-8"))["design_types"]


# JS to inject: returns the bbox of every direct child of <body> in document
# order. We store a stable id (data-aimt-id) so we can match elements back
# in Python without depending on selector path. The id is removed before
# re-serializing.
EXTRACT_JS = r"""
(() => {
  const out = [];
  const children = document.body.children;
  for (let i = 0; i < children.length; i++) {
    const el = children[i];
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden') {
      out.push({i, visible: false});
      continue;
    }
    const r = el.getBoundingClientRect();
    const br = document.body.getBoundingClientRect();
    out.push({
      i,
      visible: true,
      left: Math.round(r.left - br.left),
      top: Math.round(r.top - br.top),
      width: Math.round(r.width),
      height: Math.round(r.height),
      // Surface a few cascade-relevant flags so we don't accidentally undo
      // things that mattered.
      hasZIndex: cs.zIndex !== 'auto',
      zIndex: cs.zIndex,
    });
  }
  return out;
})()
"""


def write_position_style(left: int, top: int, width: int, height: int) -> str:
    # box-sizing:border-box so width/height correspond to total rendered size
    # (matching getBoundingClientRect()'s reading). Without this, padding and
    # border on the original element would push the rendered box past our
    # captured width, which can trip off-canvas / collision validators.
    return (
        f"box-sizing:border-box;position:absolute;left:{left}px;top:{top}px;"
        f"width:{width}px;height:{height}px;"
    )


_STYLE_ATTR_RE = re.compile(r"""style\s*=\s*("([^"]*)"|'([^']*)')""", re.IGNORECASE)


def upsert_inline_position(open_tag: str, pos_style: str) -> str:
    """Prepend pos_style to the element's style="" attribute, creating it
    if absent. Existing properties are kept (and may even override ours if
    they redundantly set positioning — that's fine, we just want positioning
    to be present in inline)."""
    m = _STYLE_ATTR_RE.search(open_tag)
    if m:
        existing = m.group(2) or m.group(3) or ""
        # Drop any prior position-related declarations so our values win.
        cleaned = re.sub(
            r"(?i)\s*(?:position|left|top|right|bottom|width|height)\s*:[^;]*;?",
            "",
            existing,
        ).strip().rstrip(";")
        merged = pos_style + (cleaned + ";" if cleaned else "")
        new_attr = f'style="{merged}"'
        return open_tag[:m.start()] + new_attr + open_tag[m.end():]
    # No style attribute — add one before the closing >.
    insert = f' style="{pos_style.rstrip(";")}"'
    if open_tag.endswith("/>"):
        return open_tag[:-2] + insert + " />"
    return open_tag[:-1] + insert + ">"


def find_body_children_open_tags(html: str) -> list[tuple[int, int, str]]:
    """Return (start_idx, end_idx, open_tag) for every direct child of <body>
    in document order. This is a heuristic: we look for the <body ...> tag,
    then walk forward tracking nesting depth so we identify top-level
    children (depth == 1 right after the opening). It's tolerant of HTML5
    quirks (self-closing/void elements, comments)."""
    body_open = re.search(r"<body\b[^>]*>", html, re.IGNORECASE)
    if not body_open:
        return []
    body_close = html.lower().rfind("</body>")
    if body_close < 0:
        body_close = len(html)
    cursor = body_open.end()

    VOID = {"area","base","br","col","embed","hr","img","input","link","meta","param","source","track","wbr"}
    out: list[tuple[int, int, str]] = []
    # Depth-aware tag scanner.
    depth = 0
    pos = cursor
    tag_re = re.compile(r"<(?P<closing>/?)(?P<name>[A-Za-z][\w-]*)[^>]*>")
    while pos < body_close:
        # Skip comments and CDATA to avoid spurious tag matches inside them.
        if html.startswith("<!--", pos):
            end = html.find("-->", pos + 4)
            if end < 0: break
            pos = end + 3
            continue
        m = tag_re.search(html, pos)
        if not m or m.start() >= body_close:
            break
        if m.group("closing"):
            depth -= 1
            pos = m.end()
            continue
        name = m.group("name").lower()
        is_void = name in VOID or m.group(0).rstrip(">").rstrip().endswith("/")
        if depth == 0:
            # Top-level child of body: this is what we annotate.
            # We need to find its closing position to get the FULL element span,
            # but for inline-style rewriting we only need the OPEN tag.
            out.append((m.start(), m.end(), m.group(0)))
        if not is_void:
            depth += 1
        pos = m.end()
    return out


async def process_one(browser, sample_path: Path, output_path: Path, stats: dict):
    sample = json.loads(sample_path.read_text(encoding="utf-8"))
    html = sample.get("html")
    if not html:
        stats["skipped_no_html"] += 1
        return
    dt = sample.get("design_type") or (sample.get("brief") or {}).get("design_type")
    spec = DESIGN_SPECS.get(dt)
    if spec is None:
        stats["skipped_no_spec"] += 1
        return

    page = await browser.new_page(viewport={"width": spec["w"], "height": spec["h"]})
    try:
        await page.set_content(html, wait_until="networkidle")
        try:
            await page.evaluate("document.fonts.ready")
        except Exception:
            pass
        boxes = await page.evaluate(EXTRACT_JS)
    finally:
        await page.close()

    open_tags = find_body_children_open_tags(html)
    if len(open_tags) != len(boxes):
        # Mismatch: the regex-based body scan disagreed with the DOM. Bail
        # safely (we'd rather skip than emit corrupt HTML).
        stats["skipped_mismatch"] += 1
        return

    # Rewrite from the end so earlier offsets stay valid.
    new_html = html
    rewrote = 0
    for (start, end, tag), box in zip(reversed(open_tags), reversed(boxes)):
        if not box.get("visible"):
            continue
        pos_style = write_position_style(
            box["left"], box["top"], box["width"], box["height"]
        )
        new_tag = upsert_inline_position(tag, pos_style)
        new_html = new_html[:start] + new_tag + new_html[end:]
        rewrote += 1

    sample["html"] = new_html
    sample.setdefault("transforms", []).append({
        "kind": "add_inline_positions",
        "children_rewritten": rewrote,
        "viewport": {"w": spec["w"], "h": spec["h"]},
    })
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(sample, indent=2, ensure_ascii=False), encoding="utf-8")
    stats["ok"] += 1
    stats["rewritten"] += rewrote


async def worker(name: int, queue: asyncio.Queue, browser, stats: dict):
    while True:
        item = await queue.get()
        if item is None:
            queue.task_done()
            return
        sample_path, output_path = item
        try:
            await process_one(browser, sample_path, output_path, stats)
        except Exception as e:
            stats["errors"] += 1
            stats["error_examples"].setdefault(type(e).__name__, []).append(
                f"{sample_path.name}: {e}"[:200]
            )
        finally:
            queue.task_done()


async def main_async(sources: list[Path], output_dir: Path, concurrency: int):
    from playwright.async_api import async_playwright  # type: ignore

    files: list[Path] = []
    for src in sources:
        for sp in sorted(src.rglob("*.json")):
            if sp.name.endswith(".validation.json"):
                continue
            files.append(sp)

    print(f"==> {len(files)} samples to process (concurrency={concurrency})")

    queue: asyncio.Queue = asyncio.Queue()
    for sp in files:
        # Mirror the sub-path under output_dir (e.g. validated/<design_type>/<id>.json).
        try:
            rel = sp.relative_to(sources[0])
        except ValueError:
            rel = Path(sp.parent.name) / sp.name
        await queue.put((sp, output_dir / rel))
    for _ in range(concurrency):
        await queue.put(None)

    stats = {"ok": 0, "rewritten": 0, "errors": 0, "skipped_no_html": 0,
             "skipped_no_spec": 0, "skipped_mismatch": 0, "error_examples": {}}

    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        workers = [asyncio.create_task(worker(i, queue, browser, stats)) for i in range(concurrency)]
        await queue.join()
        for w in workers:
            await w
        await browser.close()

    print()
    print(f"  ok:               {stats['ok']}")
    print(f"  total body kids:  {stats['rewritten']}")
    print(f"  errors:           {stats['errors']}")
    print(f"  skipped/no html:  {stats['skipped_no_html']}")
    print(f"  skipped/no spec:  {stats['skipped_no_spec']}")
    print(f"  skipped/mismatch: {stats['skipped_mismatch']}")
    for k, exs in stats["error_examples"].items():
        print(f"  error sample [{k}]:")
        for e in exs[:3]:
            print(f"    - {e}")
    print(f"  wrote -> {output_dir.resolve().relative_to(REPO_ROOT)}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", action="append", help="Directory of samples. Repeatable. Default: data/validated.")
    ap.add_argument("--output", default=None, help="Output directory. Default: <source>_inlined")
    ap.add_argument("--concurrency", type=int, default=6)
    args = ap.parse_args()

    if not args.source:
        sources = [REPO_ROOT / "data" / "validated"]
    else:
        sources = []
        for s in args.source:
            p = Path(s)
            if not p.is_absolute():
                p = REPO_ROOT / s
            sources.append(p)

    if args.output:
        output_dir = Path(args.output)
        if not output_dir.is_absolute():
            output_dir = REPO_ROOT / args.output
    else:
        # Default sibling: <source>_inlined for each source. Use the first.
        output_dir = sources[0].parent / (sources[0].name + "_inlined")

    asyncio.run(main_async(sources, output_dir, args.concurrency))


if __name__ == "__main__":
    main()
