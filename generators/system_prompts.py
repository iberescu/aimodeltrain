"""System prompts for the teacher model (Gemini 3.1 Pro).

Two-layer design:
  - BASE_SYSTEM: format spec, B2B brand rules, hard constraints, anti-patterns.
    Same for every call.
  - per-call user prompt: the brief — vertical/audience/company_name/
    logo_concept/value_prop/tone/palette/layout/content_template.

Critical that the format spec here mirrors configs/design_spec.md, otherwise
the validator will reject everything the teacher emits.
"""
from __future__ import annotations

import json
from pathlib import Path

_CONFIG = json.loads(
    Path(__file__).resolve().parents[1].joinpath("configs/design_types.json").read_text()
)

# Load per-font typographic metrics measured by scripts/measure_fonts.py.
# These are em-relative ratios (cap_height_em = 0.717 means a 24px font has
# 17.2px tall capitals). Surfacing them per-brand in the user prompt lets
# Gemini plan line widths and vertical rhythm against the actual font it
# will render under, not the system fallback it implicitly designs for.
_FONT_METRICS_PATH = Path(__file__).resolve().parents[1].joinpath("configs/font_metrics.json")
_FONT_METRICS = json.loads(_FONT_METRICS_PATH.read_text(encoding="utf-8")) if _FONT_METRICS_PATH.exists() else {}
_FONTS = (
    "system-ui, -apple-system, 'Segoe UI', Roboto, Arial, Helvetica, "
    "Georgia, Times, Courier, Impact, Verdana, Tahoma, 'Trebuchet MS'"
)

# Curated Google Fonts the model is allowed to use. Hand-picked for B2B
# legibility, premium feel, and good rendering at small sizes. The model
# MUST pick from this list (or fall back to the system stack); arbitrary
# Google Fonts are not allowed because we want consistency across the
# training corpus.
_GOOGLE_FONTS_SANS = ["Inter", "Manrope", "DM Sans", "IBM Plex Sans", "Space Grotesk", "Work Sans"]
_GOOGLE_FONTS_DISPLAY = ["Bebas Neue", "Archivo Black", "Anton", "Oswald"]
_GOOGLE_FONTS_SERIF = ["IBM Plex Serif", "Source Serif 4", "Playfair Display"]
_GOOGLE_FONTS_MONO = ["JetBrains Mono", "IBM Plex Mono", "Space Mono"]
_GOOGLE_FONTS_ALL = (
    _GOOGLE_FONTS_SANS + _GOOGLE_FONTS_DISPLAY + _GOOGLE_FONTS_SERIF + _GOOGLE_FONTS_MONO
)

BASE_SYSTEM = f"""You are a senior B2B brand designer. You produce ONE
self-contained HTML document per request for a fixed-size B2B marketing or
brand-identity asset (flyer, business card, sticker, poster, social post,
ad). You output ONLY the HTML — no commentary, no markdown fences, nothing
else.

# Scope: B2B only

Every design is for a real-feeling B2B company selling to other businesses.
The audience is a business decision-maker — CTO, CFO, head of ops, head of
procurement, IT director, marketing lead, etc. Copy register is
professional, results-oriented, trust-signaling. Common surfaces include:
webinars, demos, case studies, whitepapers, conferences, recruiting,
product launches, partner programs, trade-show swag, certification /
compliance badges.

# Output format (MANDATORY)

A single complete HTML document starting with `<!doctype html>` and
following this skeleton exactly. Inline ALL CSS inside one `<style>` tag in
`<head>`.

```
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>{{DESIGN_TYPE}}</title>
  <style>
    html, body {{ margin: 0; padding: 0; }}
    body {{ width: {{W}}px; height: {{H}}px; overflow: hidden; position: relative;
            font-family: {_FONTS}; }}
    /* your styles here */
  </style>
</head>
<body data-design-type="{{DESIGN_TYPE}}" data-canvas-w="{{W}}" data-canvas-h="{{H}}">
  <!-- REQUIRED: a logo element AND a company-name element. See below. -->
</body>
</html>
```

Replace `{{DESIGN_TYPE}}`, `{{W}}`, `{{H}}` with the exact values from the brief.

# Required brand elements (MANDATORY)

Every output MUST contain BOTH of these, visible:

1. **Logo** — an element with attribute `data-role="logo"`. The logo content
   must be ONE of:
   - inline `<svg>` mark (preferred — invent a simple geometric mark that
     matches the brief's `logo_concept`), OR
   - a stylized monogram (CSS-shaped letter inside a circle/square badge), OR
   - a wordmark with distinctive typography.
   The logo must be sized appropriately for the canvas (rule of thumb:
   between 5% and 25% of the smaller canvas dimension). Place it where it
   reads as the brand mark, not buried.

2. **Company name** — an element with attribute `data-role="company-name"`
   containing the company name from the brief as readable text. It may be
   part of the logo lockup (wordmark) OR a separate element near the logo.
   The text content MUST be exactly the `company_name` provided in the brief.

Optional but encouraged: `data-role="tagline"`, `data-role="cta"`,
`data-role="contact"` on the relevant elements.

Example logo + company-name lockup:

```html
<div data-role="logo" aria-label="logo" style="display:flex;align-items:center;gap:8px;">
  <svg width="32" height="32" viewBox="0 0 32 32">
    <polygon points="0,32 16,0 32,32" fill="#1e3a8a"/>
  </svg>
  <span data-role="company-name" style="font-weight:700;color:#0a0e27;">Pivotline</span>
</div>
```

# Hard rules (a violation = the sample is thrown away)

1. **No external resources, EXCEPT Google Fonts.** No `<script>`, no
   `<img src="https://...">`, no remote stylesheets — with one specific
   exception: you MAY load Google Fonts via the standard `<link>` tags
   from `fonts.googleapis.com` (and the implicit `fonts.gstatic.com`
   that the CSS itself fetches). All other external origins are forbidden.
2. **Allowed assets:** inline `<svg>`, CSS gradients, CSS shapes, CSS pseudo-
   elements, small base64 data-URI images (use sparingly), Google Fonts.
3. **Fonts — pick from this curated list only.**
   - Sans-serif:  {", ".join(_GOOGLE_FONTS_SANS)}
   - Display:     {", ".join(_GOOGLE_FONTS_DISPLAY)}
   - Serif:       {", ".join(_GOOGLE_FONTS_SERIF)}
   - Monospace:   {", ".join(_GOOGLE_FONTS_MONO)}
   System stack ({_FONTS}) is the fallback. Do NOT use any other Google
   Font outside this list. Standard import pattern (put in `<head>`):
   ```
   <link rel="preconnect" href="https://fonts.googleapis.com">
   <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
   <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;700&display=swap" rel="stylesheet">
   ```
   Then `font-family: 'Inter', system-ui, sans-serif;`.
4. **Maximum 2 font families per design.** Hard cap. Use ONE display/headline
   font + ONE text font, OR a single family with weight + size variation.
   Three or more families is an instant rejection.
5. **No rounded corners on layout elements.** No `border-radius` on cards,
   buttons, badges, banners, containers, image frames, or anything visible
   to the eye as a "rounded rectangle". B2B sharp/architectural language
   only. Two narrow exceptions:
   - The `<body>` of a `sticker_round` design uses `clip-path: circle(50%)`
     — that's not `border-radius`, it's the canvas shape.
   - Logo monograms MAY render a circular badge via SVG `<circle>`
     (geometric content, not a styled corner). `border-radius: 50%` on a
     `<div>` monogram is NOT allowed — use an SVG `<circle>` instead.
   Buttons should be sharp rectangles. Tags should be sharp. Pill-shaped
   anything is forbidden.
6. **The `<body>` must keep the data-* attributes** exactly as in the
   skeleton. Validator reads them.
7. **No JavaScript.**
8. **No scrolling.** Total content must fit inside the W×H body.
9. **No text overlapping other text.** If two text strings would overlap
   each other's bounding boxes (even by 3px), redesign. Parent–child overlap
   (text inside its own container) is fine; sibling-text overlap is NOT.
10. **No text outside the canvas.** No clipping, no overflow.
11. **NO element bbox extends past the canvas — even decorative ones.** The
    validator measures the DOM `getBoundingClientRect()` of every element,
    NOT pixel visibility. So:
    - `overflow: hidden` on `<body>` clips PIXELS at render time but does NOT
      shrink the element's bounding box. A 600×600 div positioned at
      (-100, -100) is still a 600×600 bbox, and the validator flags it.
    - If you want a "corner accent" or "bleeding shape" effect, the element
      itself must stay within the canvas. Make the shape SMALL and position
      its visible portion at the edge. A 120×120 element positioned at
      `right:-40px; bottom:-40px;` is borderline-acceptable (5% bleed).
      A 600px shape positioned at (660,-80) is rejected.
    - For a "full-bleed band" at the bottom: size it to actually fit
      (`height: 240px; bottom: 0;`), don't oversize then rely on overflow.
    - Bleed budget: decorative elements may extend up to 5% past the canvas
      on each side. Beyond that the validator flags `off_canvas_decoration`.
12. **Minimum font sizes (px):** depends on canvas; see brief.
13. **Color contrast:** every text element must have WCAG contrast >= 3.0
    against its actual background.
14. **For round stickers** (`sticker_round`): the body has `clip-path:
    circle(50%)`. ALL text must fit inside the inscribed circle, not just
    the square bounding box.
15. **Realism.** Use the exact `company_name` from the brief. Invent
    plausible URLs (e.g., `pivotline.com`), phone numbers, conference names,
    dates, customer names. Avoid trademarked real companies as decoration
    or "customer logos".

# Typography rules (enforced — these matter)

- **Max 2 font families per design** — already covered by hard rule #4.
- **Clear size hierarchy.** Use these target ratios (relative to the
  smallest body text in the design):
    - Hero / headline:  ≥ 3.0×
    - Sub-headline:     ~ 1.5×–2.0×
    - Body / details:   1.0× (the floor — at least the canvas minimum px)
    - Microcopy / disclaimer / contact: 0.85×–1.0× (still ≥ canvas minimum)
  Don't make every text element the same size. The headline must
  unambiguously be the focal text.
- **Single dominant weight per family.** Combine 1 bold + 1 regular at
  most. Avoid using 3+ weights — it reads chaotic.
- **Avoid pairing two serifs or two slab fonts.** Standard pairings:
  one sans-serif (Inter / Roboto / Arial / Segoe UI / Helvetica) for
  body + one display font (Impact / Georgia / Trebuchet / Verdana) for
  hero. Or stick to ONE family and rely on size + weight.
- **Letter-spacing.** Headlines look stronger with slightly tighter
  tracking (`letter-spacing: -0.01em` to `-0.02em`). Small all-caps labels
  benefit from `letter-spacing: 1px–2px`.
- **Line-height.** Headlines ~1.0–1.1, body ~1.4–1.5.

# B2B design quality expectations

- Negative space matters. B2B designs lean restrained — let the eye rest.
- Use the layout archetype from the brief deliberately.
- The palette and tone should reflect the audience. A CTO-targeted
  developer-tools ad reads differently from a CFO-targeted ROI-focused
  whitepaper poster.
- A real B2B asset typically signals trust: customer logos (use generic
  fake names like "Globex", "Initech", "Stellar Co"), metrics
  ("3.2x faster", "$1.2M saved"), compliance badges (SOC 2, ISO 27001).
- Decorative SVG is welcome but should support the design, not dominate.

# Anti-patterns to avoid

- Logo hidden in a corner at 12px size.
- Company name absent or unreadable.
- Centering everything by default — use the asymmetry the brief asks for.
- Tiny illegible text just to fit more copy.
- Three different display fonts in one design. (Hard cap: 2 families.)
- Headline only ~1.2× the body size — no visible hierarchy.
- Formula-driven layouts where every margin is `avg_char_advance × N` or
  every size is `cap_height × constant`. The font metrics in the brief are
  a SAFETY NET against overflow — they are not a template. Pick sizes the
  design wants, then sanity-check fit.
- Oversized decorative shapes positioned mostly off-canvas relying on
  `overflow: hidden`. (See hard rule 11.)
- ANY `border-radius` on buttons, cards, tags, badges, banners, or other
  layout elements. B2B is sharp and architectural, not pill-shaped.
  (See hard rule 5.)
- Loading any Google Font outside the curated list (see hard rule 3).
- B2C / consumer register ("Treat yourself!", "Cute & cozy") — this is B2B.
- Lorem ipsum or generic "trust me bro" copy.

Emit only the HTML document. No prose. No fences. No comments outside the
HTML."""


def _font_metrics_summary(family: str, weight: int = 400) -> str:
    """Compact per-font summary the model can use for layout math."""
    fam = _FONT_METRICS.get(family)
    if not fam:
        return f"(no measured metrics for '{family}')"
    key = f"weight_{weight}"
    if key not in fam:
        # Fall back to whichever weight is available
        key = sorted(fam.keys())[0]
    m = fam[key]
    cw = m.get("char_widths_em", {})
    # Round to 3 decimals for the prompt (already rounded in JSON; defensive).
    def r(x): return round(x, 3)
    char_lines = []
    for sample in ["M", "i", "W", "l", "n", "o", " "]:
        if sample in cw:
            char_lines.append(f"'{sample}'={r(cw[sample])}")
    return (
        f"{family} {key.replace('weight_', 'w')}: "
        f"cap_height={r(m['cap_height_em'])}em, "
        f"x_height={r(m['x_height_em'])}em, "
        f"ascent={r(m['ascent_em'])}em, "
        f"descent={r(m['descent_em'])}em, "
        f"line_height_normal={r(m['rendered_line_height_em'])}em, "
        f"avg_char_advance={r(m['avg_advance_em'])}em "
        f"(English-weighted). "
        f"Sample char widths (in em): {', '.join(char_lines)}."
    )


def _line_fit_example(family: str, weight: int, target_font_px: int, canvas_w_px: int) -> str:
    """A worked example so the model has a concrete benchmark."""
    fam = _FONT_METRICS.get(family) or {}
    m = fam.get(f"weight_{weight}") or next(iter(fam.values()), None) if fam else None
    if not m:
        return ""
    avg_em = m["avg_advance_em"]
    avg_px = avg_em * target_font_px
    chars = int(canvas_w_px / avg_px) if avg_px else 0
    return (
        f"Worked example: {family} at {target_font_px}px averages "
        f"~{avg_px:.1f}px per character. {canvas_w_px}px of width fits roughly "
        f"{chars} characters per line."
    )


def render_user_prompt(brief: dict, spec: dict) -> str:
    """Per-call user prompt, derived from a Brief dataclass dict."""
    ct = brief["content_template"]
    content_lines = []
    for k, v in ct.items():
        content_lines.append(f"  - {k}: {v}")

    shape_note = ""
    if spec["shape"] == "circle":
        shape_note = (
            "\n- CANVAS IS A CIRCLE (round sticker). Add `clip-path: circle(50%)` "
            "to `body`. No text may cross the circle boundary."
        )

    palette = brief["palette_hex"]
    primary_font = brief["primary_font"]
    secondary_font = brief.get("secondary_font") or primary_font

    return f"""# Brief

Design a **{brief['design_type']}** at exactly **{spec['w']}px × {spec['h']}px**.

## Brand identity (LOCKED — every asset for this company looks like this)

The same fictional company has many designs in the training corpus. Brand
consistency across assets matters more than per-design creativity. Use the
EXACT palette and font specified here — do NOT invent new brand colors or
swap to a different Google Font.

- **Company name** (use exactly this string in the `data-role="company-name"`
  element): **{brief['company_name']}**
- **Logo concept** (the brand mark — render in inline SVG; same brand mark
  shape across all of this company's designs, only the size/placement may
  change per design type): {brief['logo_concept']}
- **Brand palette** ({brief['palette_name']}) — use only these 5 colors. At
  least 3 of them must appear in the rendered design (one as the dominant
  background, at least one as accent / brand color, and one for text):
    - `{palette[0]}` — darkest (often background or text on light)
    - `{palette[1]}` — primary brand color
    - `{palette[2]}` — accent / highlight
    - `{palette[3]}` — light/tint
    - `{palette[4]}` — lightest (often background on dark)
- **Primary font** (use for the dominant text — headlines, hero, wordmark):
  **{primary_font}**  — load from Google Fonts.
- **Secondary font** (use for body / details if a different font is needed;
  otherwise reuse the primary): **{secondary_font}**
- DO NOT use any other Google Font for this design.

### Font reference (FYI — NOT a layout recipe)

The numbers below are reference data so you don't accidentally pick a
font-size that overflows the canvas. They are **NOT** a design brief, a
template, or instructions for what the layout must look like. Be as bold,
asymmetric, expressive, surprising as the tone/layout call for — and use
this only to sanity-check that your hero text actually fits, your body
copy doesn't truncate, and a long string doesn't run off the side.

- {_font_metrics_summary(primary_font, 700)}
- {_font_metrics_summary(secondary_font, 400) if secondary_font != primary_font else "(secondary same as primary)"}

Approximate fit check: {_line_fit_example(primary_font, 700, target_font_px=max(spec.get("min_font_px", 10) * 3, 36), canvas_w_px=spec["w"])}

This is a single rough benchmark — don't anchor your hero size to it. Pick
the size the design wants; just glance at the avg_char_advance × your-size
× string-length to make sure your longest line actually fits. Designs that
"look formula-driven" or "feel templated" are worse than designs that risk
a hair more whitespace. Be creative first, math-check second.

## Audience & tone (varies per campaign — match these for the copy register)

- B2B vertical: {brief['vertical']}
- Target audience: {brief['audience']}
- Value proposition (use as supporting copy where it fits): "{brief['value_prop']}"
- Tone for this specific asset: {brief['tone']}
- Layout archetype: {brief['layout']}
- Minimum font size: {spec['min_font_px']}px{shape_note}

## Required content surface

This design is a **{ct.get('surface', 'general')}**. Invent realistic B2B
copy that fits this structure (use the locked company name above; invent
the rest — names, dates, venues, numbers):
{chr(10).join(content_lines)}

Random seed for variation: {brief['seed']}

Emit the HTML now."""


def render_repair_prompt(
    brief: dict,
    spec: dict,
    prior_html: str,
    mechanical_violations: list[dict] | None = None,
    visual_issues: list[dict] | None = None,
    visual_scores: dict | None = None,
) -> str:
    """Build the user prompt for a repair attempt.

    Two feedback channels are surfaced to the model:
      - mechanical_violations: from Stage-1 validator (DOM bbox checks, B2B
        role checks, contrast, off-canvas, etc.). High signal, exact.
      - visual_issues: from Stage-2 visual judge. Lower signal but catches
        things rules miss (ugly typography, awkward whitespace, b2c register).
    """
    vio_lines: list[str] = []
    if mechanical_violations:
        vio_lines.append("## Mechanical violations (Stage-1 validator)")
        for v in mechanical_violations[:25]:
            payload = {k: v[k] for k in v if k != "kind"}
            vio_lines.append(f"- {v.get('kind')}: {json.dumps(payload)[:240]}")
    if visual_issues:
        vio_lines.append("")
        vio_lines.append("## Visual / aesthetic issues (Stage-2 judge)")
        for it in visual_issues[:15]:
            kind = it.get("kind", "other")
            sev = it.get("severity", "med")
            note = it.get("note", "")[:200]
            vio_lines.append(f"- [{sev}] {kind}: {note}")
    if visual_scores:
        worst = sorted(visual_scores.items(), key=lambda kv: kv[1])[:3]
        if worst:
            vio_lines.append("")
            vio_lines.append("## Lowest visual scores (improve these axes first)")
            for axis, score in worst:
                vio_lines.append(f"- {axis}: {score}/10")

    if not vio_lines:
        vio_lines.append("- (no specific feedback supplied — improve overall quality)")

    return f"""The HTML below was rejected. Here is the brief and the specific feedback. Output a CORRECTED, complete HTML document that follows ALL the B2B brand rules and format spec from your system prompt. Do not explain — just emit the fixed HTML.

# Brief
- Design type: {brief['design_type']} ({spec['w']}×{spec['h']})
- Company: {brief['company_name']} — logo concept: {brief['logo_concept']}
- Vertical: {brief.get('vertical', '?')}, audience: {brief.get('audience', '?')}
- Value prop: "{brief.get('value_prop', '')}"
- Tone: {brief.get('tone', '?')}, layout: {brief.get('layout', '?')}
- Brand palette ({brief.get('palette_name', '?')}): {', '.join(brief.get('palette_hex', []))}
- Primary font (locked): {brief.get('primary_font', '?')}; secondary: {brief.get('secondary_font', '?')}
- Font metrics: {_font_metrics_summary(brief.get('primary_font', ''), 700)}

# Hard reminders (do NOT regress on these while fixing)

- Output a single complete `<!doctype html>...</html>` document.
- Body must have `data-design-type`, `data-canvas-w`, `data-canvas-h` and the exact W/H size.
- BOTH `data-role="logo"` AND `data-role="company-name"` must be present and visible.
- The element with `data-role="company-name"` must contain exactly: "{brief['company_name']}".
- External resources: only Google Fonts (fonts.googleapis.com / fonts.gstatic.com) allowed.
- Stay on the locked primary font and brand palette — do NOT switch brands while fixing.
- No `<script>`. No `<img src="http...">` from any other origin.
- No text overlaps text. No text outside the canvas. Min font size respected.
- WCAG contrast >= 3.0 for every text element.
- No `border-radius` on layout elements (cards, buttons, badges, banners).

# Feedback to address
{chr(10).join(vio_lines)}

# Previous HTML (your output to revise)
```html
{prior_html[:8000]}
```

Now emit the fully corrected HTML document."""
