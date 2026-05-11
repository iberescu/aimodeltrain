"""Report Gemini call counts + token + USD cost for the current run.

Primary source: `logs/api_calls.jsonl` — one JSON record per API call, written
by `generators/api_log.py:log_api_call()`. This is the canonical data.

Fallback source: sample-level metadata. Older runs (before the api_log
infrastructure existed) embedded token usage in `sample.teacher_meta` and
`sample.repair_history[*].meta`. The fallback reconstructs cost from those,
but it ONLY sees generation + repair calls — visual-judge calls had no
token tracking before. Use the primary source for any future analysis.

Usage:
    python scripts/cost_report.py              # current data/{validated,rejected}
    python scripts/cost_report.py --root data/_archive_pilot2_20260511_2222
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from generators.api_log import compute_cost_for_call  # noqa: E402

PHASE_LABELS = {
    "generate": "Phase 2 generate",
    "repair":   "Phase 3.5 repair",
    "judge_gemini":    "Phase 3.2 judge (gemini)",
    "judge_anthropic": "Phase 3.2 judge (anthropic)",
}


def report_from_log(log_path: Path) -> None:
    if not log_path.exists():
        print(f"no api_calls.jsonl at {log_path} — falling back to sample-level data.\n")
        return None
    records: list[dict] = []
    with log_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    if not records:
        print(f"api_calls.jsonl exists but is empty at {log_path}.\n")
        return None
    return records


def print_log_report(records: list[dict], plan_size: int | None = None) -> None:
    by_phase = defaultdict(lambda: {
        "calls": 0, "errors": 0,
        "tokens_in": 0, "tokens_out": 0, "tokens_thinking": 0, "tokens_cache": 0,
        "elapsed_s": 0.0, "cost_usd": 0.0,
    })
    for r in records:
        p = r.get("phase", "unknown")
        by_phase[p]["calls"] += 1
        if r.get("error"):
            by_phase[p]["errors"] += 1
        by_phase[p]["tokens_in"]       += r.get("tokens_in") or 0
        by_phase[p]["tokens_out"]      += r.get("tokens_out") or 0
        by_phase[p]["tokens_thinking"] += r.get("tokens_thinking") or 0
        by_phase[p]["tokens_cache"]    += r.get("tokens_cache") or 0
        by_phase[p]["elapsed_s"]       += r.get("elapsed_s") or 0
        by_phase[p]["cost_usd"]        += r.get("cost_usd") or compute_cost_for_call(r)

    print("=== API call log report ===")
    print(f"  source: {len(records)} entries")
    print()
    grand_cost = 0.0
    grand_calls = 0
    for p in ["generate", "repair", "judge_gemini", "judge_anthropic"]:
        if p not in by_phase:
            continue
        d = by_phase[p]
        label = PHASE_LABELS.get(p, p)
        print(f"  {label}")
        print(f"    calls:     {d['calls']:>6}  ({d['errors']} errors)")
        print(f"    tokens in: {d['tokens_in']:>12,}")
        print(f"    thinking:  {d['tokens_thinking']:>12,}")
        print(f"    out:       {d['tokens_out']:>12,}")
        if d['tokens_cache']:
            print(f"    cached:    {d['tokens_cache']:>12,}")
        print(f"    elapsed:   {d['elapsed_s']:>11.0f} s")
        print(f"    COST:      ${d['cost_usd']:>10.2f}")
        print()
        grand_cost += d['cost_usd']
        grand_calls += d['calls']

    print(f"  GRAND TOTAL:  {grand_calls} calls  /  ${grand_cost:.2f}")
    if plan_size:
        print(f"  per planned sample:  ${grand_cost / plan_size:.4f}")
    print()


# ---------- fallback: reconstruct from sample metadata ----------

def fallback_report(root: Path) -> None:
    # Live project layout (data/validated/) and archive layout (validated/)
    candidates = [
        root / "data" / "validated", root / "data" / "rejected",
        root / "validated",          root / "rejected",
    ]
    samples = []
    for d in candidates:
        if not d.exists():
            continue
        for p in d.rglob("*.json"):
            if p.name.endswith(".validation.json"):
                continue
            try:
                samples.append(json.loads(p.read_text(encoding="utf-8")))
            except Exception:
                continue

    print("=== Cost report (fallback path) ===")
    print(f"  reconstructing from {len(samples)} sample records (no api_calls.jsonl)")
    print(f"  note: visual-judge calls are NOT tracked in this path.\n")

    gen_calls = []
    rep_calls = []
    for s in samples:
        tm = s.get("teacher_meta") or {}
        if tm and (tm.get("tokens_in") or tm.get("tokens_out")):
            gen_calls.append(tm)
        for entry in s.get("repair_history") or []:
            m = entry.get("meta") or {}
            if m and (m.get("tokens_in") or m.get("tokens_out")):
                rep_calls.append(m)

    def synth_record(m: dict, phase: str) -> dict:
        return {
            "phase": phase, "model": "gemini-3.1-pro-preview",
            "tokens_in": m.get("tokens_in") or 0,
            "tokens_out": m.get("tokens_out") or 0,
            "tokens_thinking": m.get("tokens_thinking") or 0,
            "tokens_cache": 0,
            "elapsed_s": m.get("elapsed") or 0,
            "error": None,
        }

    synthetic: list[dict] = []
    synthetic += [synth_record(m, "generate") for m in gen_calls]
    synthetic += [synth_record(m, "repair") for m in rep_calls]
    print_log_report(synthetic)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(REPO_ROOT),
                    help="Project root to inspect. Default: current repo.")
    ap.add_argument("--plan-size", type=int, default=None,
                    help="Optional plan size for per-sample cost.")
    args = ap.parse_args()
    root = Path(args.root).resolve()
    log_path = root / "logs" / "api_calls.jsonl"

    records = report_from_log(log_path)
    if records:
        print_log_report(records, plan_size=args.plan_size)
    else:
        fallback_report(root)


if __name__ == "__main__":
    main()
