# Design-Type Specification

Source of truth for canvas dimensions, valid HTML format, and collision rules. Both the **generator** and the **validator** load this spec. Any change here cascades to both.

## 0. Scope: B2B only

Every design generated for this dataset is a **B2B marketing/brand asset**. That means:

- The audience is a business decision-maker (CTO, ops manager, procurement, HR, marketing lead, etc.), not an individual consumer.
- The brand owner is a company. **Every design MUST include a visible company identity:** a logo AND the company name. Personal-services (yoga, hair salon, nail bar, wedding photographer) and direct-to-consumer retail are excluded from the industry pool.
- Copy register is professional, results-oriented, trust-signaling. Common surfaces: webinars, demos, case studies, whitepapers, conferences, recruiting, product launches, partner programs, trade-show swag, ISO/SOC2 badges, "powered by" stickers.

### Required brand elements (enforced by validator)

Every generated HTML must contain at minimum:

1. A logo element marked `data-role="logo"`. The logo must be one of:
   - inline `<svg>` mark (preferred), OR
   - a stylized monogram (CSS-shaped letter inside a circle/square/badge), OR
   - a wordmark with distinctive typography.
   - **Never** an `<img src="http...">`.
2. A company-name element marked `data-role="company-name"` containing the company's name as readable text.

The validator's `check_required_b2b_roles` enforces that both elements exist, are visible (non-zero size, not display:none, not opacity 0), and the company-name element has non-empty text content.

Optional but recommended (not enforced):
- `data-role="tagline"` — a one-line value proposition
- `data-role="cta"` — primary call to action (button / link)
- `data-role="contact"` — phone/email/url block

---

## 1. Design types & canvas sizes

All sizes in CSS pixels at 96 DPI. The HTML must render to an exact, fixed-size canvas — no scrolling, no responsiveness.

| ID                 | Real-world size | CSS px (W × H) | Notes                                  |
|--------------------|-----------------|----------------|----------------------------------------|
| `flyer_us_letter`  | 8.5" × 11"      | 816 × 1056     | Portrait                               |
| `flyer_a4`         | 210 × 297 mm    | 794 × 1123     | Portrait                               |
| `business_card`    | 3.5" × 2"       | 336 × 192      | Landscape, small canvas — tight layout |
| `sticker_square`   | 3" × 3"         | 288 × 288      |                                        |
| `sticker_round`    | 3" × 3"         | 288 × 288      | `clip-path: circle(50%)` on root       |
| `poster`           | 18" × 24"       | 864 × 1152     | Downscaled 2:1 to keep render cheap    |
| `social_post_sq`   | 1080 × 1080     | 1080 × 1080    | Instagram/FB square post               |
| `social_post_story`| 1080 × 1920     | 1080 × 1920    | IG story / TikTok / Reel               |
| `social_ad_lscape` | 1200 × 628      | 1200 × 628     | LinkedIn / FB landscape ad             |

---

## 2. Output HTML format (mandatory)

The model must emit **one self-contained HTML document** with this exact shape:

```html
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>{design_type}</title>
  <style>
    /* all CSS inline here */
    html, body { margin: 0; padding: 0; }
    body { width: {W}px; height: {H}px; overflow: hidden; position: relative; }
    /* ... */
  </style>
</head>
<body data-design-type="{design_type}" data-canvas-w="{W}" data-canvas-h="{H}">
  <!-- design content -->
</body>
</html>
```

### Hard rules

- **External resources:** forbidden EXCEPT Google Fonts. Only origins `fonts.googleapis.com` + `fonts.gstatic.com` are allowed to be fetched. No remote stylesheets from other origins, no `<img src="http...">`, no `<script src=...>`. The validator's Playwright network monitor records any other URL as `external_resource_fetch`.
- **Allowed assets:** inline SVG, CSS gradients, CSS shapes, base64 data URIs (under 100KB per image, max 3 per design), Google Fonts via `<link>`.
- **Fonts:** must be picked from a curated allowlist. System stack (Segoe UI, Roboto, Arial, Helvetica, Georgia, Times, Impact, Verdana, Tahoma, Trebuchet MS) is the fallback. Allowed Google Fonts:
  - Sans: Inter, Manrope, DM Sans, IBM Plex Sans, Space Grotesk, Work Sans
  - Display: Bebas Neue, Archivo Black, Anton, Oswald
  - Serif: IBM Plex Serif, Source Serif 4, Playfair Display
  - Monospace: JetBrains Mono, IBM Plex Mono, Space Mono
- **Max 2 font families per design.** Soft rule enforced by visual judge (currently no mechanical check; could be added by inspecting computed font-family across all text elements).
- **No rounded corners on layout elements.** No `border-radius` on cards, buttons, badges, banners, containers, tags. Exceptions: SVG `<circle>` for logo geometry is fine; `clip-path: circle(50%)` on the round-sticker body is fine (it's the canvas shape). `border-radius: 50%` on a `<div>` is forbidden — use an inline SVG circle instead.
- **`body` element must carry** `data-design-type` and `data-canvas-w`/`data-canvas-h` attributes — the validator reads these.
- **No JavaScript.**
- **Single document** — no iframes, no Shadow DOM.
- **For `sticker_round`:** the body or root container must have `clip-path: circle(50%)`. The validator enforces that no text bbox exits the circle.

---

## 3. Collision & validity rules

The validator (`validators/validate.py`) renders each HTML in headless Chromium, extracts bounding boxes from every text-bearing leaf node, and applies these checks. A sample passes only if ALL checks pass.

### 3.1 Text-bbox collision (CRITICAL)

A "text node" is any DOM element whose `textContent.trim()` is non-empty AND whose `getClientRects()` returns at least one rect, AND whose computed style has `display != 'none'` and `visibility != 'hidden'` and `opacity > 0.05`.

Two text nodes **collide** if:
- Neither is an ancestor of the other (parent–child overlap is fine).
- Their bounding boxes overlap by more than `MIN_OVERLAP_PX = 2px` on both axes (small anti-alias overlap is tolerated).
- They are not siblings inside the same `inline-flex`/`flex`/`grid` container where the overlap is exactly zero on the major axis (gap-only).

Implementation note: walk the DOM, collect leaves with text, then O(n²) pairwise check (n is small per document).

### 3.2 Off-canvas text

Any text node whose bounding box exceeds `[0, 0, W, H]` by more than 1px on any side is a violation. The validator flags it as `off_canvas`.

For `sticker_round`: text must additionally fit inside the inscribed circle. A text bbox is "outside the circle" if any corner of the bbox is more than `R = W/2` from the center `(W/2, H/2)`.

### 3.3 Off-canvas elements (non-text)

Decorative shapes/images may extend up to 5% beyond the canvas (bleed). Beyond that → violation.

### 3.4 Minimum font size

- `business_card`, `sticker_*`: minimum legible font is **8px** (computed).
- All others: minimum **10px**.
- Text nodes below threshold → violation `font_too_small`.

### 3.5 Color contrast

For every text node, compute WCAG contrast ratio between its computed color and the dominant background color sampled at the bbox center. Threshold: **3.0** (relaxed AA-large, since the model often uses display weights).

Violation: `low_contrast`.

### 3.6 Canvas size match

`<body>` computed `clientWidth` × `clientHeight` must equal the design type's canvas size within ±1px. Violation: `canvas_size_mismatch`.

### 3.7 HTML structural validity

- Must parse without errors (Chromium parser is the source of truth — anything it accepts, we accept).
- `data-design-type` attribute must match the expected type for the sample.
- No `<script>` tags. No external resources actually fetched (network monitor in Playwright catches this).

### 3.8 B2B required brand elements

The validator enforces that every sample contains:

- Exactly one (or more) element matching `[data-role="logo"]` that is visible and has non-zero bbox.
- At least one element matching `[data-role="company-name"]` that is visible AND has non-empty text content (matching the brief's `company_name` is a soft check — substring match, case-insensitive, with a 80% character-overlap fallback).

Violations: `missing_logo`, `missing_company_name`, `invisible_logo`, `invisible_company_name`, `company_name_mismatch`.

---

## 4. Sample record schema

Each generated sample lives in `data/raw/<design_type>/<sha8>.json`:

```json
{
  "id": "sha8 of html",
  "design_type": "flyer_us_letter",
  "brief": {
    "industry": "b2b devtools saas",
    "company_name": "Pivotline",
    "logo_concept": "geometric arrow mark in indigo + sans-serif wordmark",
    "value_prop": "Cut your CI runtime by half",
    "tone": "premium",
    "palette_name": "cool tech",
    "palette_hex": ["#0a0e27", "#1e3a8a", "#3b82f6", "#bfdbfe", "#f1f5f9"],
    "layout": "asymmetric_split",
    "content_template": {
      "primary_text": "webinar_title",
      "secondary_text": "value_prop",
      "details": ["date", "speakers", "duration"],
      "cta": "register_url"
    }
  },
  "html": "<!doctype html>...",
  "teacher_model": "gemini-3.1-pro-preview",
  "teacher_temperature": 1.0,
  "generated_at": "2026-05-11T14:00:00Z"
}
```

After validation, a copy goes to `data/validated/...` and a `validation_report.json` sibling is added.

---

## 5. Diversity targets (for the generator)

Of the ~15k raw generations we aim for, the distribution across design types is:

| Type                | Target raw | After ~30% rejection |
|---------------------|-----------:|---------------------:|
| `flyer_us_letter`   | 2400       | 1700                 |
| `flyer_a4`          | 1200       | 850                  |
| `business_card`     | 1800       | 1250                 |
| `sticker_square`    | 1500       | 1050                 |
| `sticker_round`     | 1500       | 1050                 |
| `poster`            | 1800       | 1250                 |
| `social_post_sq`    | 2400       | 1700                 |
| `social_post_story` | 1200       | 850                  |
| `social_ad_lscape`  | 1200       | 850                  |
| **Total**           | **15000**  | **10550**            |

Within each type, the brief diversifier (`generators/briefs.py`) cross-products:

- ~80 industries (yoga studio, dentist, food truck, etc.)
- 6 tones (premium, playful, bold, calm, urgent, minimal)
- 8 palette families (warm earth, cool tech, neon, monochrome b&w, pastel, etc.)
- 5 layout archetypes (centered hero, asymmetric split, grid, overlay, full-bleed)

Sampling is stratified to avoid mode collapse on any one combo.

---

## 6. Reserved for later

- **DPO preference pairs** (Phase 6): two outputs for the same brief; the cleaner-per-validator wins.
- **RL reward** (Phase 7, optional): weighted sum of (validity, collision-free, contrast, layout aesthetic score from a learned discriminator).
