"""B2B brief diversifier — brand-locked edition.

We define a fixed roster of fictional B2B companies. Each company has a locked
brand identity: name, logo concept, 5-color palette, primary Google Font, and
a B2B vertical. Every generated brief picks ONE company, then varies the
orthogonal axes — design type, audience persona, tone (per-campaign), layout
archetype, content template, value prop.

This mirrors how a real B2B design team works: many assets per brand, each
expressing the SAME brand identity in different formats. It teaches the model
brand consistency. With 10k briefs across 12 companies, every brand gets
~830 designs spread across all 9 design types and many surfaces (webinars,
case studies, ads, trade-show swag, …).

To add a company: append to `COMPANIES`. To increase brand emphasis on a
specific company: add weight in `_company_weights()`.
"""
from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass, asdict
from pathlib import Path


# ---------- Companies (brand-locked) ----------

COMPANIES: list[dict] = [
    {
        "company_name": "Pivotline",
        "vertical": "b2b devtools saas",
        "logo_concept": "geometric chevron mark in indigo (#1e3a8a) pointing right, beside a tight Inter Bold wordmark in the same indigo. Mark sits at ~1.2× the cap-height of the wordmark.",
        "palette_name": "cool tech",
        "palette_hex": ["#0a0e27", "#1e3a8a", "#3b82f6", "#bfdbfe", "#f1f5f9"],
        "primary_font": "Inter",
        "secondary_font": "Inter",
        "value_props": [
            "Cut CI runtime by 50%",
            "Ship from PR to prod in under 5 minutes",
            "10x faster CI without rewriting your pipeline",
            "Drop-in CI optimization — install in one command",
            "Faster CI for the teams shipping daily",
        ],
    },
    {
        "company_name": "Statera",
        "vertical": "compliance automation saas",
        "logo_concept": "shield-shaped seal (rounded only as an SVG path — no border-radius) with a stylized 'S' notched out of the top, in cream (#f4f7f1) on forest-moss (#1b3a2c). Wordmark in Manrope SemiBold to the right.",
        "palette_name": "forest moss",
        "palette_hex": ["#1b3a2c", "#3d6b4c", "#7ba17b", "#cfe1cf", "#f4f7f1"],
        "primary_font": "Manrope",
        "secondary_font": "Manrope",
        "value_props": [
            "SOC 2 ready in 30 days",
            "ISO 27001 audit-ready continuously",
            "From spreadsheet GRC to evidence on autopilot",
            "Compliance that fits the way your team already works",
            "Audit prep cut from weeks to hours",
        ],
    },
    {
        "company_name": "Vertex OS",
        "vertical": "observability platform",
        "logo_concept": "isosceles triangle mark in bright mint (#2dd4bf), point-up, with a thin inset gap forming a smaller inner triangle. Beside it, 'Vertex OS' wordmark in IBM Plex Sans, the 'OS' rendered slightly lighter.",
        "palette_name": "electric mint",
        "palette_hex": ["#062b2b", "#0e7c7b", "#2dd4bf", "#cdf3ec", "#f4fbfa"],
        "primary_font": "IBM Plex Sans",
        "secondary_font": "IBM Plex Mono",
        "value_props": [
            "Cut incident MTTR by 70%",
            "From alert to root cause in 5 minutes",
            "Stop drowning in noisy alerts",
            "Real-time tracing without the SaaS bill shock",
            "Observability that scales with your traffic, not your team",
        ],
    },
    {
        "company_name": "Helio Capital",
        "vertical": "m&a advisory firm",
        "logo_concept": "minimalist sun mark — a circle inscribed in a square-cropped beam (no rounded corners on the container; the beam is an SVG `<polygon>`), in burnished gold (#a37e3f) on cream (#fbf8f1). Wordmark in Playfair Display Regular, small-caps.",
        "palette_name": "editorial cream",
        "palette_hex": ["#1a1a1a", "#3b3a36", "#a37e3f", "#ede4d3", "#fbf8f1"],
        "primary_font": "Playfair Display",
        "secondary_font": "IBM Plex Serif",
        "value_props": [
            "Mid-market M&A advisory since 2007",
            "Sell-side specialists for founder-owned businesses",
            "Buy-side mandates from $25M to $500M",
            "Strategic capital for inflection-point operators",
            "Independent advice. Aligned outcomes.",
        ],
    },
    {
        "company_name": "Cardinal Logistics",
        "vertical": "logistics / 3pl provider",
        "logo_concept": "stylized arrowhead built from two angular planes (one ember-red #e15a3a, one charcoal #1a0e0a), pointing right. To its right, 'Cardinal' in DM Sans Bold, 'Logistics' in DM Sans Regular below it.",
        "palette_name": "ember industrial",
        "palette_hex": ["#1a0e0a", "#7a2e1f", "#e15a3a", "#f5c79a", "#f7f3ee"],
        "primary_font": "DM Sans",
        "secondary_font": "DM Sans",
        "value_props": [
            "Same-day cross-dock at 14 US hubs",
            "B2B fulfillment without the platform tax",
            "EDI, API, or both — we meet you where your stack lives",
            "Freight visibility your customers can actually read",
            "From port to dock, one logistics partner",
        ],
    },
    {
        "company_name": "Cipher Cloud",
        "vertical": "cloud security saas",
        "logo_concept": "monospaced 'C[]' glyph in JetBrains Mono Bold — the brackets read as a vault — in pure white on charcoal (#171717). Below the wordmark, a thin 1px accent line in safety-yellow (#facc15) the width of the mark.",
        "palette_name": "mono noir + signal yellow",
        "palette_hex": ["#000000", "#171717", "#404040", "#a0a0a0", "#facc15"],
        "primary_font": "JetBrains Mono",
        "secondary_font": "Inter",
        "value_props": [
            "Cloud posture management that doesn't lie",
            "From CVE alert to confirmed fix in one workflow",
            "Stop chasing noisy CSPM findings",
            "Zero-trust runtime for AWS, GCP, and Azure",
            "Security tooling your engineers actually open",
        ],
    },
    {
        "company_name": "Lattice HR",
        "vertical": "hr / payroll saas",
        "logo_concept": "interlocking square-grid mark (8 small squares forming a 3×3 with the center removed) in violet (#8b5cf6) on cream (#fbfaff). Wordmark 'Lattice' in DM Sans SemiBold beside it, with 'HR' as a smaller superscript-style label.",
        "palette_name": "violet enterprise",
        "palette_hex": ["#1a0a2e", "#4c1d95", "#8b5cf6", "#e4d9fb", "#fbfaff"],
        "primary_font": "DM Sans",
        "secondary_font": "Manrope",
        "value_props": [
            "Payroll and HRIS for teams under 500",
            "From offer letter to W-2, one platform",
            "International payroll without the spreadsheet handoff",
            "Performance reviews your managers actually finish",
            "Onboarding workflows your new hires don't dread",
        ],
    },
    {
        "company_name": "Beacon Data",
        "vertical": "data warehouse vendor",
        "logo_concept": "lighthouse beam mark — a tall isoceles triangle widening downward (the beam) sitting above a small rectangular base (the tower), in bright blue (#5b8def) on corporate navy (#0b1d3a). 'Beacon' wordmark in Work Sans Bold to the right.",
        "palette_name": "corporate navy",
        "palette_hex": ["#0b1d3a", "#15407a", "#5b8def", "#dde6f5", "#ffffff"],
        "primary_font": "Work Sans",
        "secondary_font": "IBM Plex Sans",
        "value_props": [
            "Petabyte-scale analytics at predictable cost",
            "Open-table-format warehouse, no vendor lock-in",
            "Run dbt, Spark, and Trino on one storage layer",
            "Replace your $2M warehouse bill with one number you control",
            "Built for the data team, priced for the CFO",
        ],
    },
    {
        "company_name": "Granite Engineering",
        "vertical": "engineering services firm",
        "logo_concept": "angular mountain mark made of three stacked triangles in graphite (#3f3f46) on sand (#e6e1d7). No rounded corners anywhere. Wordmark in IBM Plex Sans Condensed Bold, all-caps, tight tracking.",
        "palette_name": "sand & graphite",
        "palette_hex": ["#1f1f1f", "#3f3f46", "#a89d8a", "#e6e1d7", "#fafaf7"],
        "primary_font": "IBM Plex Sans",
        "secondary_font": "IBM Plex Sans",
        "value_props": [
            "Structural engineering for industrial-scale builds",
            "Mechanical design from concept to fabrication drawings",
            "Civil + structural under one PE seal",
            "FEA, CFD, and qualification testing in-house",
            "DFM reviews that pay for themselves in tooling savings",
        ],
    },
    {
        "company_name": "Auric Capital",
        "vertical": "venture capital firm",
        "logo_concept": "minimal serif 'A' monogram with a horizontal crossbar extended into a thin underline beneath the wordmark. Mark and wordmark both in champagne gold (#c4a572) on near-black (#0d0d0d). 'Auric' in Playfair Display Regular, 'Capital' below in IBM Plex Sans small-caps.",
        "palette_name": "noir & champagne",
        "palette_hex": ["#0d0d0d", "#1c1c1c", "#c4a572", "#e9dcc1", "#faf6ec"],
        "primary_font": "Playfair Display",
        "secondary_font": "IBM Plex Sans",
        "value_props": [
            "Seed and Series A for infrastructure founders",
            "Patient capital. Operator network. Conviction checks.",
            "$2-12M lead investments in B2B and deeptech",
            "Pre-revenue okay if the wedge is real",
            "Backed Globex (acq. by Initech 2024) and Statera (Series C 2026)",
        ],
    },
    {
        "company_name": "Tessera AI",
        "vertical": "ai infrastructure platform",
        "logo_concept": "hexagonal tessellation mark — three hexagons fused into a triangular cluster — in bright blue (#5b8def) on steel (#1e2433). Wordmark 'Tessera' in Space Grotesk Medium, the 'AI' suffix in Space Grotesk Bold one size smaller, baseline-aligned.",
        "palette_name": "steel & ink",
        "palette_hex": ["#0f1115", "#1e2433", "#7c8499", "#cfd5e1", "#f2f4f8"],
        "primary_font": "Space Grotesk",
        "secondary_font": "JetBrains Mono",
        "value_props": [
            "Multi-GPU inference orchestration at 1ms tail latency",
            "Serve LLMs at scale without the cloud-bill surprises",
            "Spot-friendly inference with no cold starts",
            "From research-grade checkpoint to production endpoint in one CLI",
            "Trusted by AI teams shipping to millions of requests per day",
        ],
    },
    {
        "company_name": "Foundry Partners",
        "vertical": "branding agency (b2b clients)",
        "logo_concept": "bold all-caps wordmark only — 'FOUNDRY' in Archivo Black, with 'PARTNERS' below in the same family, regular weight, much smaller, tracked out wide. Two-color treatment: 'FOUNDRY' in jet (#0a0a0a), 'PARTNERS' in hot magenta (#ec0860).",
        "palette_name": "jet + magenta",
        "palette_hex": ["#0a0a0a", "#1a1a1a", "#ec0860", "#fde2ec", "#ffffff"],
        "primary_font": "Archivo Black",
        "secondary_font": "Inter",
        "value_props": [
            "B2B brand identity for category-defining founders",
            "We don't do 'fun' or 'playful'. We do 'inevitable'.",
            "Brand systems built to survive a Series B rebrand",
            "60+ B2B identities shipped since 2018",
            "Strategy through to brand book, all in 8 weeks",
        ],
    },
]

assert len(COMPANIES) >= 10, "user requirement: at least 10 companies"
assert len(set(c["company_name"] for c in COMPANIES)) == len(COMPANIES)


# ---------- Per-brief variable axes ----------

AUDIENCE_PERSONAS = [
    "cto / vp engineering", "vp data", "ciso / vp security",
    "vp / head of devops", "platform team lead",
    "cfo", "vp finance", "controller", "head of procurement",
    "chro / vp people", "head of talent acquisition",
    "vp / director of marketing", "demand-gen lead", "head of revops",
    "vp sales", "head of customer success",
    "coo / vp operations", "head of supply chain", "plant manager",
    "general counsel", "head of compliance", "head of risk",
    "head of facilities", "head of it",
    "founder / ceo (smb)", "founder / ceo (mid-market)",
]

TONES = [
    "premium / enterprise",
    "trust / compliance",
    "bold / disruptive",
    "calm / consultative",
    "urgent / time-sensitive",
    "minimal / technical",
]

LAYOUT_ARCHETYPES = [
    "centered_hero",
    "asymmetric_split",
    "grid_modular",
    "overlay_image_bg",
    "top_heavy",
    "bottom_anchor",
    "diagonal_dynamic",
]

TYPE_LAYOUT_PREFS = {
    "business_card":     ["asymmetric_split", "centered_hero"],
    "sticker_round":     ["centered_hero"],
    "sticker_square":    ["centered_hero", "asymmetric_split"],
    "social_ad_lscape":  ["asymmetric_split", "overlay_image_bg"],
    "social_post_story": ["top_heavy", "bottom_anchor", "centered_hero"],
}

CONTENT_TEMPLATES = {
    "flyer_us_letter": [
        {"surface": "webinar_invite",
         "primary": "webinar_title", "secondary": "value_prop",
         "details": ["date", "speakers", "duration"], "cta": "register_url"},
        {"surface": "whitepaper_promo",
         "primary": "whitepaper_title", "secondary": "subhead",
         "details": ["author", "page_count"], "cta": "download_url"},
        {"surface": "trade_show_booth",
         "primary": "company_pitch", "secondary": "demo_offer",
         "details": ["booth_number", "event_name", "dates"], "cta": "book_demo"},
        {"surface": "case_study_handout",
         "primary": "result_headline", "secondary": "customer_quote",
         "details": ["customer_name", "industry", "metric_3"], "cta": "url"},
    ],
    "flyer_a4": [
        {"surface": "conference_handout",
         "primary": "session_title", "secondary": "speakers",
         "details": ["track", "time", "room"], "cta": "company_url"},
        {"surface": "product_launch",
         "primary": "product_name", "secondary": "tagline",
         "details": ["3_feature_bullets", "ga_date"], "cta": "book_demo"},
        {"surface": "partner_program_recruit",
         "primary": "program_name", "secondary": "value_to_partner",
         "details": ["3_perks", "tier_summary"], "cta": "apply_url"},
    ],
    "business_card": [
        {"surface": "exec_card",
         "primary": "person_name", "secondary": "role",
         "details": ["company_name", "email", "phone", "linkedin"], "cta": None},
        {"surface": "sales_rep_card",
         "primary": "person_name", "secondary": "role_with_region",
         "details": ["company_name", "direct_dial", "email"], "cta": None},
    ],
    "sticker_square": [
        {"surface": "trade_show_swag",
         "primary": "company_name_or_slogan", "secondary": None,
         "details": [], "cta": None},
        {"surface": "powered_by_badge",
         "primary": "powered_by_company", "secondary": None,
         "details": [], "cta": None},
        {"surface": "certification_badge",
         "primary": "cert_short_text", "secondary": "year",
         "details": [], "cta": None},
    ],
    "sticker_round": [
        {"surface": "circular_brand_mark",
         "primary": "company_name", "secondary": None,
         "details": ["est_year"], "cta": None},
        {"surface": "partner_seal",
         "primary": "partner_tier_text", "secondary": "year",
         "details": [], "cta": None},
    ],
    "poster": [
        {"surface": "conference_session",
         "primary": "session_title", "secondary": "speakers",
         "details": ["date", "room", "track"], "cta": "qr_or_url"},
        {"surface": "recruiting_poster",
         "primary": "role_or_team", "secondary": "headline",
         "details": ["3_benefits", "location"], "cta": "careers_url"},
        {"surface": "booth_backdrop",
         "primary": "company_value_prop", "secondary": "supporting_line",
         "details": ["booth_number"], "cta": "book_demo"},
    ],
    "social_post_sq": [
        {"surface": "webinar_promo",
         "primary": "webinar_hook", "secondary": "speaker_titles",
         "details": ["date"], "cta": "link_in_bio"},
        {"surface": "customer_quote",
         "primary": "quote", "secondary": "person_role_company",
         "details": [], "cta": None},
        {"surface": "stat_card",
         "primary": "headline_metric", "secondary": "supporting_line",
         "details": ["source_line"], "cta": "url"},
        {"surface": "feature_announcement",
         "primary": "feature_name", "secondary": "one_line_value",
         "details": ["one_detail"], "cta": "see_changelog"},
    ],
    "social_post_story": [
        {"surface": "webinar_story",
         "primary": "webinar_title", "secondary": "subhead",
         "details": ["date"], "cta": "swipe_up_register"},
        {"surface": "event_recap",
         "primary": "headline", "secondary": "subhead",
         "details": ["one_detail"], "cta": "swipe_up_url"},
    ],
    "social_ad_lscape": [
        {"surface": "demo_cta_ad",
         "primary": "value_prop", "secondary": "supporting_line",
         "details": ["one_feature"], "cta": "book_demo_button"},
        {"surface": "whitepaper_ad",
         "primary": "whitepaper_title", "secondary": "subhead",
         "details": ["page_count"], "cta": "download_button"},
        {"surface": "conference_promo",
         "primary": "event_name", "secondary": "dates_location",
         "details": ["one_speaker"], "cta": "register_button"},
    ],
}


# ---------- Brief ----------

@dataclass
class Brief:
    design_type: str
    company_name: str
    vertical: str
    logo_concept: str
    palette_name: str
    palette_hex: list[str]
    primary_font: str
    secondary_font: str
    value_prop: str
    audience: str
    tone: str
    layout: str
    content_template: dict
    seed: int

    def id(self) -> str:
        payload = json.dumps(asdict(self), sort_keys=True).encode()
        return hashlib.sha256(payload).hexdigest()[:8]


def _company_weights() -> list[float]:
    # Uniform by default. Bump a specific company by changing its weight.
    return [1.0] * len(COMPANIES)


def sample_briefs(design_type: str, n: int, rng: random.Random) -> list[Brief]:
    out: list[Brief] = []
    layouts = TYPE_LAYOUT_PREFS.get(design_type, LAYOUT_ARCHETYPES)
    templates = CONTENT_TEMPLATES[design_type]
    weights = _company_weights()
    seen_ids: set[str] = set()
    attempts = 0
    while len(out) < n and attempts < n * 4:
        attempts += 1
        company = rng.choices(COMPANIES, weights=weights, k=1)[0]
        b = Brief(
            design_type=design_type,
            company_name=company["company_name"],
            vertical=company["vertical"],
            logo_concept=company["logo_concept"],
            palette_name=company["palette_name"],
            palette_hex=list(company["palette_hex"]),
            primary_font=company["primary_font"],
            secondary_font=company["secondary_font"],
            value_prop=rng.choice(company["value_props"]),
            audience=rng.choice(AUDIENCE_PERSONAS),
            tone=rng.choice(TONES),
            layout=layouts[len(out) % len(layouts)],
            content_template=rng.choice(templates),
            seed=rng.randint(1, 10_000_000),
        )
        bid = b.id()
        if bid in seen_ids:
            continue
        seen_ids.add(bid)
        out.append(b)
    return out


def build_plan(targets: dict[str, int], seed: int = 0) -> list[Brief]:
    rng = random.Random(seed)
    plan: list[Brief] = []
    for design_type, count in targets.items():
        plan.extend(sample_briefs(design_type, count, rng))
    rng.shuffle(plan)
    return plan


if __name__ == "__main__":
    import sys
    from collections import Counter
    config = json.loads(Path(__file__).resolve().parents[1].joinpath("configs/design_types.json").read_text())
    targets = config["generation_targets"]
    if "--small" in sys.argv:
        targets = {k: max(5, v // 100) for k, v in targets.items()}
    plan = build_plan(targets, seed=42)
    print(f"plan size: {len(plan)}")
    print("\nper design_type:")
    for k, v in sorted(Counter(b.design_type for b in plan).items()):
        print(f"  {k}: {v}")
    print("\nper company:")
    for k, v in Counter(b.company_name for b in plan).most_common():
        print(f"  {k}: {v}")
    print("\nfirst 2 briefs:")
    for b in plan[:2]:
        print(json.dumps(asdict(b), indent=2))
        print("---")
