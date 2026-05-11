"""Pure-Python rule checks applied to the data returned by dom_extract.js.

Each check returns a list of violation dicts. A sample is "valid" iff every
check returns an empty list.
"""
from __future__ import annotations

import math
from typing import Any


def _bboxes_overlap(a: dict, b: dict, min_overlap_px: float) -> tuple[bool, float, float]:
    ax2, ay2 = a["x"] + a["w"], a["y"] + a["h"]
    bx2, by2 = b["x"] + b["w"], b["y"] + b["h"]
    ox = min(ax2, bx2) - max(a["x"], b["x"])
    oy = min(ay2, by2) - max(a["y"], b["y"])
    return (ox > min_overlap_px and oy > min_overlap_px, ox, oy)


def _is_ancestor(path_a: str, path_b: str) -> bool:
    # Ancestor iff path_a is a strict prefix of path_b in our ">"-segmented path.
    return path_b.startswith(path_a + ">") or path_a.startswith(path_b + ">")


def _wcag_relative_luminance(c: dict) -> float:
    def lin(v: float) -> float:
        v = v / 255.0
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4
    return 0.2126 * lin(c["r"]) + 0.7152 * lin(c["g"]) + 0.0722 * lin(c["b"])


def _wcag_contrast(fg: dict, bg: dict) -> float:
    l1 = _wcag_relative_luminance(fg)
    l2 = _wcag_relative_luminance(bg)
    lighter, darker = max(l1, l2), min(l1, l2)
    return (lighter + 0.05) / (darker + 0.05)


def check_canvas_size(extract: dict, expected_w: int, expected_h: int, tol: int) -> list[dict]:
    cw = extract["canvas"]["bodyClientW"]
    ch = extract["canvas"]["bodyClientH"]
    if abs(cw - expected_w) > tol or abs(ch - expected_h) > tol:
        return [{
            "kind": "canvas_size_mismatch",
            "expected": [expected_w, expected_h],
            "actual": [cw, ch],
        }]
    return []


def check_data_attributes(extract: dict, expected_type: str) -> list[dict]:
    actual = extract["canvas"]["dataDesignType"]
    if actual != expected_type:
        return [{
            "kind": "data_design_type_mismatch",
            "expected": expected_type,
            "actual": actual,
        }]
    return []


def check_no_scripts_or_iframes(extract: dict) -> list[dict]:
    out = []
    if extract.get("scriptCount", 0) > 0:
        out.append({"kind": "has_script", "count": extract["scriptCount"]})
    if extract.get("iframeCount", 0) > 0:
        out.append({"kind": "has_iframe", "count": extract["iframeCount"]})
    return out


def check_text_collisions(extract: dict, min_overlap_px: float) -> list[dict]:
    leaves = extract["textLeaves"]
    out = []
    n = len(leaves)
    for i in range(n):
        a = leaves[i]
        for j in range(i + 1, n):
            b = leaves[j]
            if _is_ancestor(a["path"], b["path"]):
                continue
            hit, ox, oy = _bboxes_overlap(a["rect"], b["rect"], min_overlap_px)
            if not hit:
                continue
            out.append({
                "kind": "text_collision",
                "a": {"path": a["path"], "text": a["text"]},
                "b": {"path": b["path"], "text": b["text"]},
                "overlap_px": [round(ox, 1), round(oy, 1)],
            })
    return out


def check_off_canvas_text(
    extract: dict, w: int, h: int, shape: str
) -> list[dict]:
    out = []
    for leaf in extract["textLeaves"]:
        r = leaf["rect"]
        # Rectangle bounds
        if r["x"] < -1 or r["y"] < -1 or r["x"] + r["w"] > w + 1 or r["y"] + r["h"] > h + 1:
            out.append({
                "kind": "off_canvas_text",
                "path": leaf["path"],
                "text": leaf["text"],
                "rect": r,
            })
            continue
        # Circle bounds (round stickers)
        if shape == "circle":
            cx, cy = w / 2, h / 2
            radius = min(w, h) / 2
            corners = [
                (r["x"], r["y"]),
                (r["x"] + r["w"], r["y"]),
                (r["x"], r["y"] + r["h"]),
                (r["x"] + r["w"], r["y"] + r["h"]),
            ]
            for (px, py) in corners:
                if math.hypot(px - cx, py - cy) > radius + 1:
                    out.append({
                        "kind": "outside_circle_text",
                        "path": leaf["path"],
                        "text": leaf["text"],
                        "rect": r,
                    })
                    break
    return out


def check_off_canvas_decoration(
    extract: dict, w: int, h: int, bleed_pct: float
) -> list[dict]:
    out = []
    margin_x = w * bleed_pct
    margin_y = h * bleed_pct
    for el in extract["decorativeElements"]:
        r = el["rect"]
        # Skip elements that are basically the canvas itself or larger ancestors
        if r["w"] >= w * 0.95 and r["h"] >= h * 0.95:
            continue
        if (
            r["x"] < -margin_x - 1
            or r["y"] < -margin_y - 1
            or r["x"] + r["w"] > w + margin_x + 1
            or r["y"] + r["h"] > h + margin_y + 1
        ):
            out.append({
                "kind": "off_canvas_decoration",
                "path": el["path"],
                "tag": el["tag"],
                "rect": r,
            })
    return out


def check_font_sizes(extract: dict, min_px: float) -> list[dict]:
    out = []
    for leaf in extract["textLeaves"]:
        fs = leaf.get("fontSizePx", 0)
        if fs < min_px - 0.01:
            out.append({
                "kind": "font_too_small",
                "path": leaf["path"],
                "text": leaf["text"],
                "font_size_px": fs,
                "min_px": min_px,
            })
    return out


def _normalize_text(s: str) -> str:
    return "".join(ch.lower() for ch in s if ch.isalnum())


def _char_overlap_ratio(a: str, b: str) -> float:
    """Cheap fuzzy match: |intersection| / |b| over character multisets."""
    from collections import Counter
    ca, cb = Counter(a), Counter(b)
    inter = sum((ca & cb).values())
    return inter / max(len(b), 1)


def check_required_b2b_roles(
    extract: dict,
    expected_company_name: str | None,
    required_roles: list[str],
    name_substring: bool = True,
    name_min_overlap: float = 0.8,
) -> list[dict]:
    out: list[dict] = []
    role_map: dict = extract.get("roleElements") or {}

    for role in required_roles:
        entries = role_map.get(role, [])
        if not entries:
            out.append({"kind": f"missing_{role.replace('-', '_')}"})
            continue
        visible_entries = [e for e in entries if e.get("visible")]
        if not visible_entries:
            out.append({"kind": f"invisible_{role.replace('-', '_')}"})
            continue
        if role == "company-name":
            joined = " ".join(e.get("text", "") for e in visible_entries).strip()
            if not joined:
                out.append({"kind": "invisible_company_name", "note": "empty text content"})
            elif expected_company_name:
                norm_actual = _normalize_text(joined)
                norm_expected = _normalize_text(expected_company_name)
                if name_substring and norm_expected in norm_actual:
                    pass  # OK
                else:
                    overlap = _char_overlap_ratio(norm_actual, norm_expected)
                    if overlap < name_min_overlap:
                        out.append({
                            "kind": "company_name_mismatch",
                            "expected": expected_company_name,
                            "actual": joined[:120],
                            "char_overlap": round(overlap, 2),
                        })
    return out


def check_contrast(extract: dict, min_ratio: float) -> list[dict]:
    out = []
    for leaf in extract["textLeaves"]:
        ratio = _wcag_contrast(leaf["color"], leaf["bg"])
        if ratio < min_ratio:
            out.append({
                "kind": "low_contrast",
                "path": leaf["path"],
                "text": leaf["text"],
                "contrast_ratio": round(ratio, 2),
                "fg": leaf["color"],
                "bg": leaf["bg"],
            })
    return out


def run_all_checks(
    extract: dict,
    *,
    expected_type: str,
    w: int,
    h: int,
    shape: str,
    min_font_px: float,
    bleed_pct: float,
    min_overlap_px: float,
    min_contrast: float,
    canvas_tol: int,
    expected_company_name: str | None = None,
    required_roles: list[str] | None = None,
    company_name_substring: bool = True,
    company_name_min_overlap: float = 0.8,
) -> dict[str, Any]:
    violations: list[dict] = []
    violations += check_canvas_size(extract, w, h, canvas_tol)
    violations += check_data_attributes(extract, expected_type)
    violations += check_no_scripts_or_iframes(extract)
    if required_roles:
        violations += check_required_b2b_roles(
            extract,
            expected_company_name=expected_company_name,
            required_roles=required_roles,
            name_substring=company_name_substring,
            name_min_overlap=company_name_min_overlap,
        )
    violations += check_text_collisions(extract, min_overlap_px)
    violations += check_off_canvas_text(extract, w, h, shape)
    violations += check_off_canvas_decoration(extract, w, h, bleed_pct)
    violations += check_font_sizes(extract, min_font_px)
    violations += check_contrast(extract, min_contrast)

    counts: dict[str, int] = {}
    for v in violations:
        counts[v["kind"]] = counts.get(v["kind"], 0) + 1

    return {
        "valid": len(violations) == 0,
        "violation_count": len(violations),
        "violations_by_kind": counts,
        "violations": violations,
        "text_leaf_count": len(extract["textLeaves"]),
    }
