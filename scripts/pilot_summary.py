"""Produce a full pilot run summary from data/validated and data/rejected."""
from __future__ import annotations
import json
from pathlib import Path
from collections import Counter, defaultdict

REPO_ROOT = Path(__file__).resolve().parents[1]
VAL = REPO_ROOT / "data" / "validated"
REJ = REPO_ROOT / "data" / "rejected"

def load_pairs(d: Path):
    out = []
    for sp in d.rglob("*.json"):
        if sp.name.endswith(".validation.json"):
            continue
        rp = sp.with_suffix(".validation.json")
        if not rp.exists():
            continue
        try:
            s = json.loads(sp.read_text(encoding="utf-8"))
            r = json.loads(rp.read_text(encoding="utf-8"))
        except Exception:
            continue
        out.append((s, r))
    return out

val = load_pairs(VAL)
rej = load_pairs(REJ)
print(f"VALIDATED (both stages):  {len(val)}")
print(f"REJECTED:                 {len(rej)}")
print()

# Stage-1 violation breakdown on rejected
mech_viol = Counter()
for s, r in rej:
    for v in r.get("violations", []):
        mech_viol[v.get("kind", "unknown")] += 1
print("Stage-1 violations in REJECTED:")
for k, v in mech_viol.most_common():
    print(f"  {k}: {v}")
print()

# Visual judge scores on validated
axes = ["brief_adherence", "brand_presence", "typography",
        "layout_balance", "color_harmony", "b2b_register", "overall"]
score_lists = defaultdict(list)
ship_count = 0
judged = 0
for s, r in val:
    vj = r.get("visual_judge")
    if not vj or "scores" not in vj:
        continue
    judged += 1
    for a in axes:
        if a in vj["scores"]:
            score_lists[a].append(vj["scores"][a])
    if vj.get("ship"):
        ship_count += 1
print(f"Visual judge scores across {judged} VALIDATED samples (ship=yes: {ship_count}):")
print(f"  {'axis':<20} {'mean':>5} {'min':>4} {'max':>4}")
for a in axes:
    vs = score_lists[a]
    if not vs:
        continue
    print(f"  {a:<20} {sum(vs)/len(vs):>5.2f} {min(vs):>4} {max(vs):>4}")
print()

# Visual judge demotions (in rejected with visual_judge field)
demoted_axes = defaultdict(list)
demoted_kinds = Counter()
for s, r in rej:
    vj = r.get("visual_judge")
    if not vj:
        continue
    for a in axes:
        if a in vj.get("scores", {}):
            demoted_axes[a].append(vj["scores"][a])
    for it in vj.get("issues", []):
        demoted_kinds[it.get("kind", "other")] += 1

if demoted_axes:
    print(f"Visual-judge scores of DEMOTED samples ({len(demoted_axes['overall'])} samples):")
    print(f"  {'axis':<20} {'mean':>5} {'min':>4} {'max':>4}")
    for a in axes:
        vs = demoted_axes[a]
        if not vs:
            continue
        print(f"  {a:<20} {sum(vs)/len(vs):>5.2f} {min(vs):>4} {max(vs):>4}")
    print(f"\n  Issue kinds among demoted:")
    for k, v in demoted_kinds.most_common():
        print(f"    {k}: {v}")
    print()

# Repair effectiveness
repaired_pass = sum(1 for s, _ in val if s.get("repair_history"))
clean_pass = len(val) - repaired_pass
print(f"Among the {len(val)} validated samples:")
print(f"  passed without repair:    {clean_pass}")
print(f"  passed after >=1 repair:  {repaired_pass}")
print()

# Token usage estimate
gen_tokens_in = 0; gen_tokens_out = 0; gen_thinking = 0; gen_calls = 0
rep_tokens_in = 0; rep_tokens_out = 0; rep_thinking = 0; rep_calls = 0
for s, _ in val + rej:
    tm = s.get("teacher_meta", {})
    if tm:
        gen_calls += 1
        gen_tokens_in += tm.get("tokens_in") or 0
        gen_tokens_out += tm.get("tokens_out") or 0
        gen_thinking += tm.get("tokens_thinking") or 0
    for entry in s.get("repair_history", []):
        m = entry.get("meta", {})
        rep_calls += 1
        rep_tokens_in += m.get("tokens_in") or 0
        rep_tokens_out += m.get("tokens_out") or 0
        rep_thinking += m.get("tokens_thinking") or 0

print("Token usage (Gemini 3.1 Pro):")
print(f"  generation calls: {gen_calls}")
print(f"    input:    {gen_tokens_in:>10,}")
print(f"    thinking: {gen_thinking:>10,}")
print(f"    output:   {gen_tokens_out:>10,}")
print(f"  repair calls:     {rep_calls}")
print(f"    input:    {rep_tokens_in:>10,}")
print(f"    thinking: {rep_thinking:>10,}")
print(f"    output:   {rep_tokens_out:>10,}")
print(f"  GRAND TOTAL tokens:")
print(f"    input    (incl. thinking): {gen_tokens_in + gen_thinking + rep_tokens_in + rep_thinking:>10,}")
print(f"    output:                    {gen_tokens_out + rep_tokens_out:>10,}")
