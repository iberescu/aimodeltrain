"""Build playground.html — a self-contained page for testing the deployed
Qwen3.6-27B + LoRA adapter on the HF Inference Endpoint.

Embeds (inline) the same source data the training pipeline uses:
  - BASE_SYSTEM (from generators/system_prompts.py)
  - 12 brand-locked COMPANIES (from generators/briefs.py)
  - AUDIENCE_PERSONAS, TONES, LAYOUT_ARCHETYPES, CONTENT_TEMPLATES, TYPE_LAYOUT_PREFS
  - Design type specs (configs/design_types.json)
  - Font metrics summary (subset of configs/font_metrics.json)

The page does prompt-rendering in JavaScript that mirrors render_user_prompt(),
so the user message it sends to the endpoint matches what the teacher saw at
training time.

Re-run any time the source data changes.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from generators.system_prompts import BASE_SYSTEM  # noqa: E402
from generators import briefs as _briefs  # noqa: E402

DESIGN_SPECS = json.loads((REPO_ROOT / "configs" / "design_types.json").read_text(encoding="utf-8"))["design_types"]
FONT_METRICS_RAW = json.loads((REPO_ROOT / "configs" / "font_metrics.json").read_text(encoding="utf-8"))


def load_dotenv() -> dict:
    """Tiny .env reader. Avoids depending on python-dotenv being installed."""
    out: dict = {}
    p = REPO_ROOT / ".env"
    if not p.exists():
        return out
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        k, _, v = line.partition("=")
        out[k.strip()] = v.strip().strip("'").strip('"')
    return out


def resolve_secret(key: str, env: dict) -> str:
    """Look in process env, then .env, then Windows user-scope registry."""
    v = os.environ.get(key) or env.get(key)
    if v:
        return v
    try:
        import winreg  # type: ignore
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment") as k:
            return winreg.QueryValueEx(k, key)[0]
    except Exception:
        return ""


def query_endpoint_url() -> str:
    """If .env didn't have ENDPOINT_URL, query HF for the live endpoint URL."""
    env = load_dotenv()
    if env.get("ENDPOINT_URL"):
        return env["ENDPOINT_URL"]
    tok = resolve_secret("HUGGINGFACE_HUB_TOKEN", env)
    if not tok:
        return ""
    try:
        from huggingface_hub import HfApi  # type: ignore
        api = HfApi(token=tok)
        ep = api.get_inference_endpoint("aimt-dryrun", namespace="iberescu2201")
        url = getattr(ep, "url", "") or ""
        if url and not url.endswith("/v1/chat/completions"):
            url = url.rstrip("/") + "/v1/chat/completions"
        return url
    except Exception:
        return ""


def font_metrics_summary() -> dict:
    """Trim font_metrics.json to just the fields render_user_prompt surfaces."""
    out = {}
    for family, weights in FONT_METRICS_RAW.items():
        out[family] = {}
        for wkey, m in weights.items():
            cw = m.get("char_widths_em") or {}
            out[family][wkey] = {
                "cap_height_em": m.get("cap_height_em"),
                "x_height_em": m.get("x_height_em"),
                "ascent_em": m.get("ascent_em"),
                "descent_em": m.get("descent_em"),
                "rendered_line_height_em": m.get("rendered_line_height_em"),
                "avg_advance_em": m.get("avg_advance_em"),
                "char_widths_em": {k: cw[k] for k in ["M", "i", "W", "l", "n", "o", " "] if k in cw},
            }
    return out


def main():
    env = load_dotenv()
    hf_token = resolve_secret("HUGGINGFACE_HUB_TOKEN", env)
    endpoint_url = env.get("ENDPOINT_URL") or query_endpoint_url()

    data = {
        "system_prompt": BASE_SYSTEM.strip(),
        "companies": _briefs.COMPANIES,
        "audience_personas": _briefs.AUDIENCE_PERSONAS,
        "tones": _briefs.TONES,
        "layout_archetypes": _briefs.LAYOUT_ARCHETYPES,
        "type_layout_prefs": _briefs.TYPE_LAYOUT_PREFS,
        "content_templates": _briefs.CONTENT_TEMPLATES,
        "design_specs": DESIGN_SPECS,
        "font_metrics": font_metrics_summary(),
        # Baked-in defaults. Page falls back to localStorage if it sees nothing here.
        # NOTE: the resulting playground.html is gitignored — never commit it.
        "config": {
            "endpoint_url": endpoint_url or "",
            "hf_token": hf_token or "",
        },
    }
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")

    out = REPO_ROOT / "playground.html"
    template = (REPO_ROOT / "scripts" / "_playground_template.html").read_text(encoding="utf-8")
    html = template.replace("__DATA__", payload)
    out.write_text(html, encoding="utf-8")
    print(f"wrote {out} ({len(html):,} bytes)")
    print(f"  endpoint_url: {endpoint_url or '(not yet provisioned)'}")
    print(f"  hf_token:     {('set (' + str(len(hf_token)) + ' chars)') if hf_token else 'MISSING'}")


if __name__ == "__main__":
    main()
