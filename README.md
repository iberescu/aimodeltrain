# aimodeltrain

Synthetic-data pipeline for fine-tuning **Qwen/Qwen3.6-27B** to generate
production-grade HTML for **B2B brand assets** — flyers, business cards,
square + round stickers, posters, square / story / landscape social posts.

Gemini 3.1 Pro Preview is the teacher. Two stages of validation
(mechanical + multimodal-visual-judge) gate every sample. A repair loop
feeds rejected samples back to Gemini with full violation feedback and
re-runs both stages. The final dataset is intended for LoRA SFT on
Qwen3.6-27B → DPO (using the original-vs-repaired pairs from the cleaning
step as natural preference data) → optional GRPO.

---

## Pipeline at a glance

```
                  brand-locked brief                     each brief picks 1
                  (12 companies × 9 designs)             of 12 companies
                                                         (locked palette,
   ┌──────────────────┐                                   logo concept, font)
   │  Phase 2:        │   Gemini 3.1 Pro Preview          ↓
   │  GENERATE        │   thinking_level=MEDIUM
   │                  │   max_output=16384 (CRITICAL)
   └────────┬─────────┘   httpx timeout=180s
            │
            ▼  data/raw/<type>/<id>.json
   ┌──────────────────┐
   │  Phase 3.1:      │   Playwright headless Chromium
   │  STAGE-1 CHECKS  │   render → DOM bbox extract →
   │  (mechanical)    │   rules check
   └────────┬─────────┘
            │ valid                                       invalid
            ▼                                              │
       data/validated/                                     ▼
            │                                         data/rejected/
            ▼                                              │
   ┌──────────────────┐                                    │
   │  Phase 3.2:      │   Gemini 3.1 Pro                   │
   │  VISUAL JUDGE    │   7-axis structured score          │
   │  (Stage-2)       │   ship: true | false               │
   └────────┬─────────┘                                    │
            │ pass (overall>=7 & ship)                     │
            ▼                                              │
       (kept)                                              │
            │                                              │
            │           ┌──────────────────────────────────┘
            │           │
            │           ▼
            │   ┌──────────────────┐
            │   │ Phase 3.5:       │   Gemini 3.1 Pro
            │   │ REPAIR LOOP      │   ← feedback: Stage-1 violations +
            │   │ (up to 2 rounds) │     Stage-2 issues + low scores
            │   └────────┬─────────┘
            │            │
            │            ▼  rewrites sample.html, appends to repair_history
            │     re-validate
            │     re-judge
            │            │
            └────────────┘  (final yield)

                       ⟶ cost_report.py reads logs/api_calls.jsonl
                       ⟶ pilot_summary.py reads sample metadata
```

---

## Quick start

```powershell
# 0. Prerequisites (Windows + PowerShell). One-time:
winget install Python.Python.3.12 --scope user --silent
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned -Force

# 1. Clone + setup
git clone https://github.com/iberescu/aimodeltrain.git
cd aimodeltrain
.\scripts\setup.ps1                    # venv + pip + Playwright Chromium (~3min)

# 2. Fonts (re-fetchable; not in repo)
.\.venv\Scripts\python.exe scripts\download_fonts.py    # ~30s
.\.venv\Scripts\python.exe scripts\measure_fonts.py     # ~2 min

# 3. API key (persistent, user-scope)
[System.Environment]::SetEnvironmentVariable('GOOGLE_API_KEY', 'YOUR_KEY', 'User')

# 4. Smoke test (no money spent) — must pass before running the pipeline
.\scripts\smoketest.ps1

# 5. Pilot run (50 samples, ~$10, ~25 min)
$env:PLAN_SIZE = 50
.\scripts\run_pipeline.ps1

# 6. Full 10k run (~$1,970, ~2-3 days at concurrency=10)
Remove-Item Env:\PLAN_SIZE -EA 0
$env:GEN_CONCURRENCY = 10
.\scripts\run_pipeline.ps1
```

---

## Repository layout

```
aimodeltrain/
├── configs/
│   ├── design_spec.md          The single source of truth for the design
│   │                            contract (canvas sizes, B2B rules, format).
│   │                            Both generator and validator read from here.
│   ├── design_types.json       Machine-readable companion: canvas px,
│   │                            thresholds, generation weights.
│   ├── font_catalog.json       20 curated Google Fonts (families + weights).
│   ├── font_metrics.json       Measured per-(family, weight) metrics:
│   │                            cap-height, x-height, ascent, descent,
│   │                            line-height-normal, avg_advance,
│   │                            per-char widths for ASCII. Used by the
│   │                            user prompt as a layout safety net.
│   └── pricing.json            Gemini 3.1 Pro tier pricing (verified
│                                from ai.google.dev May 2026).
│
├── generators/
│   ├── briefs.py               12-company brand-locked diversifier.
│   │                            Each brief = company + design_type +
│   │                            audience + tone + layout + content surface.
│   ├── system_prompts.py       BASE_SYSTEM (cached prefix, 8k chars) +
│   │                            render_user_prompt + render_repair_prompt.
│   ├── generate.py             Phase-2 driver. Async, concurrency-bound,
│   │                            resumable, httpx timeout, retry on failure.
│   ├── repair.py               Phase-3.5 driver. Reads rejected samples,
│   │                            sends Stage-1 violations + Stage-2 issues +
│   │                            worst score axes to Gemini, appends to
│   │                            repair_history (preserving previous_html
│   │                            for free DPO pairs).
│   └── api_log.py              log_api_call(): one JSON line per API call
│                                to logs/api_calls.jsonl. Includes per-call
│                                cost computed from pricing.json.
│
├── validators/
│   ├── dom_extract.js          Injected into the rendered page; pulls
│   │                            text leaves, bboxes, computed colors,
│   │                            data-role elements, scripts/iframes count.
│   ├── checks.py               Pure-Python rules: text collisions,
│   │                            off-canvas (text/decoration), font-size
│   │                            floor, WCAG contrast >= 3.0, canvas size,
│   │                            required B2B roles (logo + company-name).
│   ├── validate.py             Phase-3.1 driver. Playwright headless
│   │                            Chromium, waits networkidle + fonts.ready,
│   │                            move-semantics (clean repair loop).
│   └── visual_judge.py         Phase-3.2 driver. Multimodal Gemini call
│                                with the rendered PNG + brief summary;
│                                returns 7-axis structured JSON.
│
├── scripts/
│   ├── setup.ps1               Bootstrap (venv, pip, Playwright Chromium).
│   ├── run_pipeline.ps1        End-to-end orchestrator: generate →
│   │                            validate → judge → (repair → validate →
│   │                            judge) × MAX_REPAIR_ROUNDS → cost report.
│   ├── smoketest.ps1           Runs 3 hand-crafted samples through
│   │                            validate. Idempotent.
│   ├── download_fonts.py       Fetches the curated 20 Google Fonts.
│   │                            Filters the /* latin */ subset.
│   ├── measure_fonts.py        Playwright + FontFace API extracts
│   │                            per-font metrics → font_metrics.json.
│   ├── cost_report.py          Reads logs/api_calls.jsonl OR reconstructs
│   │                            from teacher_meta if log absent.
│   ├── pilot_summary.py        Yield + judge scores + violation breakdown.
│   ├── patch_screenshot_paths.py  One-shot tool for migration bug.
│   └── test_judge_call.py      Test the judge in isolation against an
│                                existing validated sample.
│
├── samples_handcrafted/
│   ├── 01_pass_business_card.json         expected: VALID
│   ├── 02_fail_text_collision_flyer.json  expected: text_collision
│   └── 03_fail_outside_circle_sticker.json expected: outside_circle_text
│
├── data/                       (gitignored — generated artifacts)
├── fonts/                      (gitignored — re-fetchable)
├── logs/                       (gitignored — runtime logs)
├── renders/                    (gitignored — PNG screenshots)
│
├── requirements.txt
├── .gitignore
└── README.md
```

---

## The design contract (configs/design_spec.md)

### Design types

| ID                  | Real-world size | Canvas (px) | Notes                                  |
|---------------------|-----------------|-------------|----------------------------------------|
| `flyer_us_letter`   | 8.5" × 11"      | 816 × 1056  | Portrait                               |
| `flyer_a4`          | 210 × 297 mm    | 794 × 1123  | Portrait                               |
| `business_card`     | 3.5" × 2"       | 336 × 192   | Landscape, tight                       |
| `sticker_square`    | 3" × 3"         | 288 × 288   |                                        |
| `sticker_round`     | 3" × 3"         | 288 × 288   | `clip-path: circle(50%)` on root       |
| `poster`            | 18" × 24"       | 864 × 1152  | Downscaled 2:1 for render cost         |
| `social_post_sq`    | 1080 × 1080     | 1080 × 1080 | IG / FB square                         |
| `social_post_story` | 1080 × 1920     | 1080 × 1920 | IG story / TikTok                      |
| `social_ad_lscape`  | 1200 × 628      | 1200 × 628  | LinkedIn / FB landscape                |

### Hard rules (enforced by validator)

1. **Scope: B2B only.** No B2C / consumer industries.
2. **Every output must contain** `[data-role="logo"]` AND `[data-role="company-name"]`, both visible.
3. **Self-contained HTML.** Only external origin allowed: `fonts.googleapis.com` + `fonts.gstatic.com`.
4. **No JavaScript, no iframes, no remote stylesheets** outside Google Fonts.
5. **Max 2 font families** per design.
6. **No `border-radius`** on layout elements (cards/buttons/badges). SVG `<circle>` for logo geometry is fine; `clip-path: circle(50%)` on the round-sticker body is fine.
7. **No text overlaps text** (sibling-text bbox overlap > 2px = collision).
8. **No element bbox exits the canvas** (5% bleed allowed for decoration; 0% for text).
9. **WCAG contrast ≥ 3.0** for every text element against its rendered background.
10. **Minimum font size** per canvas (8px on cards/stickers; 10px elsewhere).
11. **Canvas size match** within 1px of declared dimensions.
12. **Round stickers**: all text must fit inside the inscribed circle.

---

## The 12-brand roster (generators/briefs.py)

Every brief locks the brand identity. Only orthogonal axes vary
(audience persona, tone-per-campaign, layout archetype, content surface,
design type).

| Company             | Vertical                        | Palette              | Primary Font      |
|---------------------|---------------------------------|----------------------|-------------------|
| **Pivotline**       | b2b devtools / CI               | cool tech (indigo)   | Inter             |
| **Statera**         | compliance automation           | forest moss          | Manrope           |
| **Vertex OS**       | observability                   | electric mint        | IBM Plex Sans     |
| **Helio Capital**   | M&A advisory                    | editorial cream      | Playfair Display  |
| **Cardinal Logistics** | 3PL / freight                | ember industrial     | DM Sans           |
| **Cipher Cloud**    | cloud security                  | mono noir + yellow   | JetBrains Mono    |
| **Lattice HR**      | HR / payroll                    | violet enterprise    | DM Sans           |
| **Beacon Data**     | data warehouse                  | corporate navy       | Work Sans         |
| **Granite Engineering** | engineering services        | sand & graphite      | IBM Plex Sans     |
| **Auric Capital**   | venture capital                 | noir + champagne     | Playfair Display  |
| **Tessera AI**      | AI infrastructure               | steel & ink          | Space Grotesk     |
| **Foundry Partners** | branding agency (b2b)          | jet + magenta        | Archivo Black     |

At 10k briefs: ~830 designs / brand × ~92 designs / (brand × design_type).
Plenty for the model to learn brand-consistent expression across surfaces.

---

## Curated Google Fonts (configs/font_catalog.json)

The model may only pick from these 20 families. The catalog covers the
brand roster's needs with headroom.

| Category   | Families |
|------------|----------|
| Sans-serif | Inter, Manrope, DM Sans, IBM Plex Sans, Space Grotesk, Work Sans, Plus Jakarta Sans |
| Display    | Bebas Neue, Archivo Black, Anton, Oswald, Syne |
| Serif      | IBM Plex Serif, Source Serif 4, Playfair Display, Crimson Pro |
| Monospace  | JetBrains Mono, IBM Plex Mono, Space Mono, Fira Code |

Per-font metrics (measured by `scripts/measure_fonts.py`) are surfaced
in every user prompt as a **safety net against overflow** — explicitly
framed as FYI, not a layout recipe. Example:

```
Inter w700: cap_height=0.734em, x_height=0.547em, ascent=0.969em,
descent=0.249em, line_height_normal=1.218em, avg_char_advance=0.496em
(English-weighted). Sample widths: 'M'=0.952, 'W'=1.142, ' '=0.281.

Approximate fit check: Inter at 36px averages ~17.9px per character.
816px of width fits roughly 45 characters per line.
```

---

## Configuration knobs (env vars for run_pipeline.ps1)

```
PLAN_SIZE             50          # total designs to plan
GEN_CONCURRENCY       6           # Gemini generation workers
VAL_CONCURRENCY       6           # Playwright validator workers
JUDGE_CONCURRENCY     4           # visual judge workers
REPAIR_CONCURRENCY    4           # repair workers
JUDGE_THRESHOLD       7           # min visual-judge overall score to pass
JUDGE_PROVIDER        gemini      # or "anthropic" for cross-vendor judge
MAX_REPAIR_ROUNDS     2           # how many repair rounds
```

---

## Visual judge schema (validators/visual_judge.py)

Every rendered design gets a structured 7-axis score from Gemini:

```json
{
  "scores": {
    "brief_adherence": 1-10,
    "brand_presence":  1-10,
    "typography":      1-10,
    "layout_balance":  1-10,
    "color_harmony":   1-10,
    "b2b_register":    1-10,
    "overall":         1-10
  },
  "issues": [
    {"kind": "text_collision|tiny_or_hidden_logo|wrong_company_name|...",
     "severity": "low|med|high", "note": "..."}
  ],
  "ship": true | false
}
```

Ship criterion: `overall >= JUDGE_THRESHOLD AND ship == true`. Below
threshold: sample moves to `data/rejected/` and the repair loop picks
it up next round.

---

## Pilot results (the journey)

| Pilot | Plan | Yield | Cost  | Notable |
|------:|-----:|------:|------:|---------|
| 1     | 100  | 64%   | ~$13  | First end-to-end. Visual judge errored 29 times (path bug). |
| 2     | 100  | 75%   | ~$14  | Path bug fixed. Brand-locked roster added. Stalled — asyncio timeout didn't propagate through google-genai SDK. |
| 3     | 100  | (killed) | ~$12 | httpx fix attempted. Font metrics added. Off-canvas violations regressed (model designed against system-fallback fonts, validator rendered Google Fonts). |
| 4 v1  | 50   | (killed) | $7.71 lost | **63% malformed_html_response.** Caught by api_calls.jsonl: `max_output_tokens=8192` was insufficient — MEDIUM thinking ate 7.8k, leaving only 325 tokens for HTML output. |
| **4 v2** | **50** | **92%** | **$9.86** | **First fully autonomous run.** All fixes stacked. |

Each pilot's data is preserved under `data/_archive_pilot*_<timestamp>/`
in the runtime workspace (gitignored).

---

## Lessons learned (so future-self doesn't repeat them)

### 1. The google-genai SDK doesn't honor `asyncio.wait_for` cancellation
`asyncio.wait_for(coro, timeout=...)` raises `CancelledError` *into* the
coroutine, but the SDK appears to internally `asyncio.shield()` the httpx
call, so the wait_for itself blocks indefinitely. **Fix**: set
`http_options=types.HttpOptions(timeout=180_000)` on the `genai.Client()`
itself. The httpx-layer timeout cuts the actual TCP socket and is
honored.

### 2. `max_output_tokens` counts THINKING + actual output combined
At `thinking_level=MEDIUM`, Gemini 3.1 Pro uses 5–8k tokens of thinking
before emitting the HTML. If `max_output_tokens=8192` (the SDK default),
the HTML gets truncated mid-document and the response fails our
`is_well_formed_html()` check. **Fix**: bump to `16384`. Cost goes up
slightly per call but the malformed-response failure rate collapses.

### 3. Google Fonts CSS returns multiple `@font-face` blocks per family
One per Unicode subset (`latin`, `latin-ext`, `cyrillic`, …). A naive
regex that grabs the FIRST block likely gets `latin-ext` (accented
characters only, no ASCII). Canvas2D then silently falls back to Arial
when measuring 'M' or 'A'. **Fix**: filter for the comment `/* latin */`
preceding the @font-face block — that's the one covering U+0020–U+007F.

### 4. The validator must MOVE samples (not copy) between dirs
Otherwise the repair loop creates duplicates as samples bounce between
`data/rejected/` and `data/validated/`. **Fix**: `validate.py` uses
`shutil.move` with a self-source guard (no-op when src == dest).

### 5. Path-bug killed pilot 1's visual judge
`Path.relative_to(REPO_ROOT)` fails when `out_png` is a relative path —
`'renders\\X.png' is not in the subpath of 'C:\\ibe\\aimodel'`. **Fix**:
`.resolve()` before `.relative_to()`, and use forward slashes for
portability. Also: `visual_judge.find_screenshot()` now handles both
relative-to-repo paths and absolute paths defensively.

### 6. Centralized API logging unlocks fast cost surprises
Without `logs/api_calls.jsonl`, pilot 4 v1's 63% failure rate would have
shown up only in final yield stats after 30+ min and $40+ spent. With
the log, the failure pattern was visible after the first 75 calls (~5
min) and saved an order of magnitude in burnt API spend. **Lesson**:
log every API call to a structured file from day one.

### 7. Font metrics in the prompt help — but only as a safety net
Surfacing `cap_height_em`, `avg_char_advance`, per-char widths drops
off_canvas_text violations 83% (54 → 9) by giving the model the
information it needs to do line-fit math. But explicit framing matters:
the prompt says "FYI — NOT a layout recipe", and an anti-pattern calls
out "formula-driven layouts where every margin is `avg_char × N`". This
preserves creativity while preventing the dominant overflow class.

### 8. PowerShell execution policy blocks scripts by default
On a fresh Windows install, `.\scripts\setup.ps1` will fail with
"running scripts is disabled on this system". **Fix**:
`Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned -Force`.

### 9. New PowerShell shells don't see env vars set in the parent
`[System.Environment]::SetEnvironmentVariable('FOO', '...', 'User')`
persists to the registry, but the harness process's snapshot of env
doesn't refresh when it spawns children. **Fix**: scripts read
`[System.Environment]::GetEnvironmentVariable('FOO', 'User')` as a
fallback if `$env:FOO` is empty.

---

## Cost expectations

Real numbers from pilot 4 v2 (50 samples, autonomous run):

```
Phase             calls    in        thinking   out      cost
─────────────────────────────────────────────────────────────
generate           51     202k      332k       104k    $5.64
repair             38     222k      142k        81k    $3.13
judge (gemini)     56     115k       65k         7k    $1.10
─────────────────────────────────────────────────────────────
GRAND TOTAL       145                                  $9.86
                                  per planned sample: $0.197
                                  per validated:      $0.214
```

Linear extrapolation to 10k:

```
generation:  10000 × $5.64/50  = $1128
repair:      ~7600  × $3.13/38 = $626   (~30-40% of plan needs repair)
judge:       ~11000 × $1.10/56 = $216   (multi-round judging)
─────────────────────────────────────────
TOTAL 10k                       ≈ $1970
expected validated samples      ≈ 9200  (at 92% yield)
wall time at concurrency=6     ≈ ~80 hours / 3.3 days
wall time at concurrency=12    ≈ ~40 hours / 1.7 days
```

Cost report at the end of every run via `scripts/cost_report.py` (auto-
invoked by run_pipeline.ps1). Reads `logs/api_calls.jsonl` directly.

---

## What's next (not in this repo yet)

1. **Phase 4 — dataset packaging.** JSONL in Qwen chat template, train/val/test split, push to HuggingFace Hub as a private dataset.
2. **Phase 5 — LoRA SFT on HF.** Qwen3.6-27B base, LoRA r=64, 2-3 epochs on H100 via HF Jobs.
3. **Phase 6 — DPO.** Use the `repair_history` pairs (original = rejected, final = chosen) as natural preference data. ~3000 pairs available from a 10k generation.
4. **Phase 7 (optional) — GRPO** with the validator as a verifier-style reward, only if DPO plateaus.

---

## API key handling

The pipeline reads `GOOGLE_API_KEY` from environment variables in this
order: process env → user-scope persisted env (Windows registry). Set it
persistently:

```powershell
[System.Environment]::SetEnvironmentVariable('GOOGLE_API_KEY', 'YOUR_KEY', 'User')
```

**Never** commit the key. Optional `ANTHROPIC_API_KEY` for the
cross-vendor judge (`--provider anthropic`). Optional
`HUGGINGFACE_HUB_TOKEN` for dataset/model push (Phase 4+).

---

## License

Code: MIT (intended). Generated samples: yours.

Brand/company names in `generators/briefs.py` are **fictional** —
deliberately invented so the resulting training data carries no
trademark risk. Logo concepts are descriptive of marks the model will
draw inline; no real-world logos are referenced.
