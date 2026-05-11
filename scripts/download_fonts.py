"""Download all 20 curated Google Fonts to fonts/<family>/<weight>.woff2.

Google Fonts serves different font formats based on the requesting User-Agent.
We send a Chrome UA so we get WOFF2 (best compression, modern browser
support) — without it the API returns TTF urls.

Idempotent: skips files already on disk.

Usage:
    python scripts/download_fonts.py
    python scripts/download_fonts.py --force      # re-download everything
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.parse
from pathlib import Path

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = REPO_ROOT / "configs" / "font_catalog.json"
FONTS_DIR = REPO_ROOT / "fonts"

# Chrome-on-Windows UA — necessary to receive WOFF2 from the Google Fonts API.
CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# Google Fonts CSS layout:
#   /* latin */               <-- comment names the subset
#   @font-face { font-family: '...'; font-weight: NNN; src: url(...woff2); }
#   /* latin-ext */
#   @font-face { ... }
#
# We need the `latin` subset because that's the one covering U+0020-U+007F
# (ASCII letters/digits/punctuation). The `latin-ext` block only has
# accented chars and a tiny smattering of punctuation — Canvas2D rendering
# of plain English text against latin-ext silently falls back to Arial,
# which is exactly the bug pilot 3 hit.
SUBSET_BLOCK_RE = re.compile(
    r"/\*\s*([\w-]+)\s*\*/\s*"
    r"@font-face\s*\{[^}]*?font-family:\s*'([^']+)'[^}]*?"
    r"font-weight:\s*(\d+)[^}]*?"
    r"src:\s*url\((https://[^)]+\.woff2)\)[^}]*?\}",
    re.DOTALL,
)


def fetch_css_for_family(family: str, weights: list[int]) -> str:
    spec = f"{family.replace(' ', '+')}:wght@" + ";".join(str(w) for w in weights)
    url = f"https://fonts.googleapis.com/css2?family={spec}&display=swap"
    r = httpx.get(url, headers={"User-Agent": CHROME_UA}, timeout=30.0)
    r.raise_for_status()
    return r.text


def parse_face_blocks(css_text: str) -> list[tuple[str, str, int, str]]:
    """Return (subset, family, weight, woff2_url) per @font-face block."""
    out = []
    for m in SUBSET_BLOCK_RE.finditer(css_text):
        out.append((m.group(1), m.group(2), int(m.group(3)), m.group(4)))
    return out


def safe_family_dir(family: str) -> Path:
    return FONTS_DIR / family.replace(" ", "_")


def download_one(url: str, dest: Path, force: bool = False) -> bool:
    """Download a single WOFF2; returns True if downloaded, False if skipped."""
    if dest.exists() and not force:
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    r = httpx.get(url, headers={"User-Agent": CHROME_UA}, timeout=60.0)
    r.raise_for_status()
    dest.write_bytes(r.content)
    return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true",
                    help="re-download even if file exists")
    args = ap.parse_args()

    catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    fonts = catalog["fonts"]
    FONTS_DIR.mkdir(parents=True, exist_ok=True)

    summary = []
    t0 = time.time()
    for family, info in fonts.items():
        weights = info["weights"]
        print(f"\n=== {family} ({info['category']}) — weights {weights}")
        try:
            css = fetch_css_for_family(family, weights)
        except Exception as e:
            print(f"  ERROR fetching css for {family}: {e}", file=sys.stderr)
            summary.append((family, 0, len(weights), "css_failed"))
            continue
        blocks = parse_face_blocks(css)
        # Only keep the `latin` subset — that's the one with ASCII coverage.
        # If a family doesn't have a `latin` block (rare), fall back to
        # whichever subset includes U+0020.
        latin_blocks = [b for b in blocks if b[0] == "latin"]
        if not latin_blocks:
            print(f"  WARNING: no /* latin */ block in CSS — falling back to first")
            latin_blocks = blocks
        seen: dict[int, str] = {}
        for subset, fam, w, url in latin_blocks:
            if w in weights and w not in seen:
                seen[w] = url

        downloaded = 0
        for w, url in seen.items():
            dest = safe_family_dir(family) / f"{w}.woff2"
            try:
                if download_one(url, dest, force=args.force):
                    print(f"  + weight {w}  ({dest.stat().st_size:,} bytes)")
                    downloaded += 1
                else:
                    print(f"  = weight {w}  (already on disk)")
            except Exception as e:
                print(f"  ERROR downloading {family} {w}: {e}", file=sys.stderr)
        missing = [w for w in weights if w not in seen]
        if missing:
            print(f"  ! missing weights from css: {missing}")
        summary.append((family, len(seen), len(weights), None))

    dt = time.time() - t0
    print(f"\n=== done in {dt:.1f}s ===")
    for fam, got, want, err in summary:
        flag = " " if got == want else "*"
        suffix = f"  ({err})" if err else ""
        print(f"  {flag} {fam:<22} {got}/{want} weights{suffix}")


if __name__ == "__main__":
    main()
