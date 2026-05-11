"""One-shot: fix already-written validation.json files that have a missing
screenshot field because of the (now-fixed) relative_to() bug. Idempotent —
safe to re-run."""
from __future__ import annotations
import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
VALIDATED = REPO_ROOT / "data" / "validated"
RENDERS = REPO_ROOT / "renders"

patched = 0
skipped = 0
missing_png = 0
for rp in VALIDATED.rglob("*.validation.json"):
    try:
        report = json.loads(rp.read_text(encoding="utf-8"))
    except Exception:
        continue
    if not report.get("valid"):
        skipped += 1
        continue
    if report.get("screenshot"):
        skipped += 1
        continue
    sample_id = report.get("id")
    design_type = report.get("design_type")
    if not sample_id or not design_type:
        skipped += 1
        continue
    png = RENDERS / design_type / f"{sample_id}.png"
    if not png.exists():
        missing_png += 1
        continue
    report["screenshot"] = f"renders/{design_type}/{sample_id}.png"
    # Drop the stale error so visual_judge runs cleanly
    report.pop("screenshot_error", None)
    # Also drop any prior visual_judge field so it gets re-run
    report.pop("visual_judge", None)
    report.pop("overall_pass", None)
    rp.write_text(json.dumps(report, indent=2), encoding="utf-8")
    patched += 1

print(f"patched: {patched}")
print(f"skipped: {skipped}")
print(f"missing screenshot file: {missing_png}")
