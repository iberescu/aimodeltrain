"""Measure typographic metrics for every font in fonts/, save to configs/font_metrics.json.

We use the FontFace JS API + base64 data URIs to avoid file:// cross-origin
issues — Chromium blocks file:// font fetches from about:blank pages, which
silently falls back to a system font and gives you identical metrics for
every "font". Inlining as data URIs sidesteps that entirely.

For each (family, weight) we render the font in headless Chromium and pull:
  - cap-height (from Canvas2D actualBoundingBoxAscent of 'H')
  - x-height (from actualBoundingBoxAscent of 'x')
  - ascent / descent / line-height (from fontBoundingBox*)
  - per-character advance widths for printable ASCII
  - weighted-average advance width (English frequency table)

All measurements are em-relative (font-size 1000px → divide by 1000).
Save to configs/font_metrics.json. The per-call user prompt surfaces these
so Gemini can lay out text against the actual font metrics it will render
under, not the system-fallback metrics it implicitly designs for.

Usage:
    python scripts/measure_fonts.py
"""
from __future__ import annotations

import asyncio
import base64
import json
import sys
from pathlib import Path

from playwright.async_api import async_playwright

REPO_ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = REPO_ROOT / "configs" / "font_catalog.json"
FONTS_DIR = REPO_ROOT / "fonts"
OUTPUT_PATH = REPO_ROOT / "configs" / "font_metrics.json"

SAMPLE_CHARS = (
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "abcdefghijklmnopqrstuvwxyz"
    "0123456789"
    " .,;:!?'\"()-—/&@#%$"
)

ENG_FREQ = {
    "e": 12.7, "t": 9.1, "a": 8.2, "o": 7.5, "i": 7.0, "n": 6.7, "s": 6.3, "h": 6.1,
    "r": 6.0, "d": 4.3, "l": 4.0, "c": 2.8, "u": 2.8, "m": 2.4, "w": 2.4, "f": 2.2,
    "g": 2.0, "y": 2.0, "p": 1.9, "b": 1.5, "v": 1.0, "k": 0.8, "j": 0.15, "x": 0.15,
    "q": 0.10, "z": 0.07, " ": 18.0,
}

MEASURE_JS = r"""
(async ({family, weight, dataUri, chars, engFreq}) => {
  // Register the font via the JS API — robust across page origins.
  const ff = new FontFace(family, `url(${dataUri}) format("woff2")`,
                           { weight: String(weight) });
  const loaded = await ff.load();
  document.fonts.add(loaded);
  await document.fonts.ready;

  const fontSpec = `${weight} 1000px "${family}"`;
  if (!document.fonts.check(fontSpec)) {
    return { error: "font_not_loaded_after_check" };
  }

  const fontSizePx = 1000;
  const canvas = document.createElement('canvas');
  canvas.width = 4000;
  canvas.height = 2000;
  const ctx = canvas.getContext('2d');
  ctx.font = fontSpec;

  function asEm(v) { return v / fontSizePx; }

  // Per-character advance widths
  const widths = {};
  for (const ch of chars) {
    widths[ch] = asEm(ctx.measureText(ch).width);
  }

  // English-frequency-weighted average advance
  let avgNum = 0, avgDen = 0;
  for (const [ch, w] of Object.entries(engFreq)) {
    avgNum += ctx.measureText(ch).width * w;
    avgDen += w;
  }
  const avgAdvanceEm = asEm(avgNum / avgDen);

  // Cap-height = actual ink-box ascent of 'H'.
  const Hm = ctx.measureText('H');
  const xm = ctx.measureText('x');
  const Mm = ctx.measureText('M');
  const capHeightEm = asEm(Hm.actualBoundingBoxAscent || 0);
  const xHeightEm   = asEm(xm.actualBoundingBoxAscent || 0);
  const ascentEm    = asEm(Mm.fontBoundingBoxAscent  || 0);
  const descentEm   = asEm(Mm.fontBoundingBoxDescent || 0);

  // line-height (normal) measured from a DOM node.
  const probe = document.createElement('span');
  probe.textContent = 'Hg';
  probe.style.font = fontSpec;
  probe.style.lineHeight = 'normal';
  probe.style.position = 'absolute';
  probe.style.left = '-9999px';
  document.body.appendChild(probe);
  const rect = probe.getBoundingClientRect();
  document.body.removeChild(probe);
  const renderedLineHeightEm = asEm(rect.height);

  return {
    cap_height_em: capHeightEm,
    x_height_em: xHeightEm,
    ascent_em: ascentEm,
    descent_em: descentEm,
    rendered_line_height_em: renderedLineHeightEm,
    avg_advance_em: avgAdvanceEm,
    char_widths_em: widths,
  };
})
"""


def family_dir_name(family: str) -> str:
    return family.replace(" ", "_")


def font_data_uri(path: Path) -> str:
    return "data:font/woff2;base64," + base64.b64encode(path.read_bytes()).decode("ascii")


async def measure_all() -> dict:
    catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    fonts = catalog["fonts"]

    faces: list[tuple[str, int, Path]] = []
    missing = []
    for family, info in fonts.items():
        for weight in info["weights"]:
            path = FONTS_DIR / family_dir_name(family) / f"{weight}.woff2"
            if not path.exists():
                missing.append(f"{family} w={weight}")
                continue
            faces.append((family, weight, path))
    if missing:
        print(f"WARNING: missing on disk: {missing}", file=sys.stderr)
    if not faces:
        return {}

    out: dict = {}
    bad: list[tuple[str, int, str]] = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        page = await browser.new_page()
        # Plain page; the FontFace JS API works on about:blank too.
        await page.set_content("<!doctype html><html><body></body></html>", wait_until="load")

        for fam, weight, path in faces:
            data_uri = font_data_uri(path)
            try:
                metrics = await page.evaluate(
                    MEASURE_JS,
                    {
                        "family": fam,
                        "weight": weight,
                        "dataUri": data_uri,
                        "chars": SAMPLE_CHARS,
                        "engFreq": ENG_FREQ,
                    },
                )
            except Exception as e:
                print(f"  ERROR measuring {fam} w={weight}: {e}", file=sys.stderr)
                bad.append((fam, weight, str(e)[:200]))
                continue
            if "error" in metrics:
                print(f"  ERROR loading {fam} w={weight}: {metrics['error']}", file=sys.stderr)
                bad.append((fam, weight, metrics["error"]))
                continue

            out.setdefault(fam, {})[f"weight_{weight}"] = {
                "cap_height_em":     round(metrics["cap_height_em"], 4),
                "x_height_em":       round(metrics["x_height_em"], 4),
                "ascent_em":         round(metrics["ascent_em"], 4),
                "descent_em":        round(metrics["descent_em"], 4),
                "rendered_line_height_em": round(metrics["rendered_line_height_em"], 4),
                "avg_advance_em":    round(metrics["avg_advance_em"], 4),
                "char_widths_em":    {k: round(v, 4) for k, v in metrics["char_widths_em"].items()},
            }
            print(f"  measured {fam:<20} w={weight}: "
                  f"cap={metrics['cap_height_em']:.3f}  "
                  f"x={metrics['x_height_em']:.3f}  "
                  f"asc={metrics['ascent_em']:.3f}  "
                  f"avg_adv={metrics['avg_advance_em']:.3f}")

        await browser.close()
    if bad:
        print(f"\nfailed: {len(bad)}", file=sys.stderr)
        for fam, w, msg in bad:
            print(f"  {fam} w={w}: {msg}", file=sys.stderr)
    return out


def main():
    out = asyncio.run(measure_all())
    if not out:
        print("no metrics produced", file=sys.stderr)
        sys.exit(1)
    OUTPUT_PATH.write_text(json.dumps(out, indent=2), encoding="utf-8")
    families = len(out)
    weights = sum(len(v) for v in out.values())
    sz = OUTPUT_PATH.stat().st_size
    print(f"\nwrote {OUTPUT_PATH} ({families} families, {weights} weights, {sz:,} bytes)")


if __name__ == "__main__":
    main()
