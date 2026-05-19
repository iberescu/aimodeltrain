"""Build a self-contained viewer.html that shows every sample from every pilot.

Scans:
  - The live run: data/validated, data/rejected (+ renders/)
  - Each archived pilot: data/_archive_pilot*/validated, .../rejected (+ ./renders or repo renders/)
  - logs/api_calls.jsonl (for per-pilot cost when present alongside the pilot's dir)

Writes:
  - viewer.html in the repo root, with the manifest embedded inline as a
    <script type="application/json"> blob and PNGs referenced by relative
    path. Open by double-click — no local server needed.

Re-run any time (including while a pilot is running) to refresh the view.
"""
from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def load_pair(sp: Path):
    if sp.name.endswith(".validation.json"):
        return None
    rp = sp.with_suffix(".validation.json")
    try:
        sample = json.loads(sp.read_text(encoding="utf-8"))
    except Exception as e:
        return {"_error": f"sample read failed: {e}", "_path": str(sp)}
    report = None
    if rp.exists():
        try:
            report = json.loads(rp.read_text(encoding="utf-8"))
        except Exception:
            report = None
    return {"sample": sample, "report": report, "sample_path": sp, "report_path": rp if rp.exists() else None}


def screenshot_rel(report: dict | None, sample: dict, pilot_root: Path) -> str | None:
    """Resolve a screenshot path that the browser can load from the repo root."""
    if not report:
        return None
    candidate = report.get("screenshot")
    if not candidate:
        return None
    cand_path = Path(candidate)
    # absolute path — relativize to repo root
    if cand_path.is_absolute():
        try:
            rel = cand_path.resolve().relative_to(REPO_ROOT)
            return rel.as_posix()
        except Exception:
            return None
    # relative path — try a few resolutions
    tries = [
        REPO_ROOT / cand_path,
        pilot_root / cand_path,
        pilot_root / "renders" / cand_path.name,
        REPO_ROOT / "renders" / sample.get("design_type", "unknown") / f"{sample.get('id', '')}.png",
    ]
    for t in tries:
        if t.exists():
            try:
                return t.resolve().relative_to(REPO_ROOT).as_posix()
            except Exception:
                continue
    # fall back to the recorded value verbatim (may 404 in the browser)
    return cand_path.as_posix()


def collect_pilot(name: str, validated_dir: Path, rejected_dir: Path, pilot_root: Path, api_log: Path | None):
    samples = []
    for status, d in (("validated", validated_dir), ("rejected", rejected_dir)):
        if not d.exists():
            continue
        for sp in sorted(d.rglob("*.json")):
            if sp.name.endswith(".validation.json"):
                continue
            pair = load_pair(sp)
            if pair is None or pair.get("_error"):
                continue
            sample = pair["sample"]
            report = pair["report"]
            ssrel = screenshot_rel(report, sample, pilot_root)
            brief = sample.get("brief", {}) or {}
            vj = (report or {}).get("visual_judge") or {}
            scores = vj.get("scores") or {}
            samples.append({
                "id": sample.get("id"),
                "status": status,
                "design_type": sample.get("design_type"),
                "company_name": brief.get("company_name"),
                "vertical": brief.get("vertical"),
                "audience": brief.get("audience"),
                "tone": brief.get("tone"),
                "palette_name": brief.get("palette_name"),
                "palette_hex": brief.get("palette_hex") or [],
                "layout": brief.get("layout"),
                "value_prop": brief.get("value_prop"),
                "logo_concept": brief.get("logo_concept"),
                "screenshot": ssrel,
                "canvas": (report or {}).get("canvas"),
                "valid": bool((report or {}).get("valid")),
                "violations": (report or {}).get("violations") or [],
                "violations_by_kind": (report or {}).get("violations_by_kind") or {},
                "visual_judge": {
                    "provider": vj.get("provider"),
                    "judged_at": vj.get("judged_at"),
                    "scores": scores,
                    "issues": vj.get("issues") or [],
                    "ship": vj.get("ship"),
                    "passed": vj.get("passed"),
                } if vj else None,
                "overall_pass": (report or {}).get("overall_pass"),
                "teacher_meta": sample.get("teacher_meta"),
                "repair_history": [
                    {
                        "round": i + 1,
                        "previous_html": entry.get("previous_html"),
                        "meta": entry.get("meta"),
                        "feedback": entry.get("feedback"),
                        "at": entry.get("at"),
                    }
                    for i, entry in enumerate(sample.get("repair_history") or [])
                ],
                "html": sample.get("html"),
            })

    # Aggregate stats
    n = len(samples)
    n_valid = sum(1 for s in samples if s["status"] == "validated")
    n_rej = n - n_valid
    by_type = Counter(s["design_type"] for s in samples)
    by_brand = Counter(s["company_name"] for s in samples if s.get("company_name"))
    viol_kinds = Counter()
    for s in samples:
        for k, c in (s.get("violations_by_kind") or {}).items():
            viol_kinds[k] += int(c)

    judged_scores = defaultdict(list)
    for s in samples:
        vj = s.get("visual_judge") or {}
        for axis, val in (vj.get("scores") or {}).items():
            try:
                judged_scores[axis].append(int(val))
            except Exception:
                pass
    mean_scores = {a: round(sum(v) / len(v), 2) for a, v in judged_scores.items() if v}

    # Cost from api_calls.jsonl, if present
    cost = {"total_usd": 0.0, "by_phase": {}, "calls": 0}
    if api_log and api_log.exists():
        try:
            for line in api_log.read_text(encoding="utf-8", errors="ignore").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                phase = rec.get("phase") or rec.get("stage") or "unknown"
                usd = rec.get("cost_usd") or rec.get("cost") or 0
                try:
                    usd = float(usd)
                except Exception:
                    usd = 0.0
                cost["total_usd"] += usd
                cost["by_phase"][phase] = cost["by_phase"].get(phase, 0.0) + usd
                cost["calls"] += 1
            cost["total_usd"] = round(cost["total_usd"], 4)
            cost["by_phase"] = {k: round(v, 4) for k, v in cost["by_phase"].items()}
        except Exception:
            pass

    return {
        "name": name,
        "samples": samples,
        "stats": {
            "total": n,
            "validated": n_valid,
            "rejected": n_rej,
            "yield_pct": round(100.0 * n_valid / n, 1) if n else 0.0,
            "by_design_type": dict(by_type.most_common()),
            "by_brand": dict(by_brand.most_common()),
            "violation_kinds": dict(viol_kinds.most_common()),
            "mean_judge_scores": mean_scores,
            "cost": cost,
        },
    }


def discover_pilots():
    pilots = []
    live = collect_pilot(
        name="current run",
        validated_dir=REPO_ROOT / "data" / "validated",
        rejected_dir=REPO_ROOT / "data" / "rejected",
        pilot_root=REPO_ROOT / "data",
        api_log=REPO_ROOT / "logs" / "api_calls.jsonl",
    )
    if live["samples"]:
        pilots.append(live)

    archives = sorted((REPO_ROOT / "data").glob("_archive_pilot*"))
    for arch in archives:
        # archive layout may have validated/ and rejected/ directly,
        # or under a data/ subdir
        cands = [
            (arch / "validated", arch / "rejected", arch),
            (arch / "data" / "validated", arch / "data" / "rejected", arch / "data"),
        ]
        for v, r, root in cands:
            if v.exists() or r.exists():
                # find an api log if archived
                api_log = None
                for c in [arch / "logs" / "api_calls.jsonl", arch / "api_calls.jsonl"]:
                    if c.exists():
                        api_log = c
                        break
                pilots.append(collect_pilot(name=arch.name, validated_dir=v, rejected_dir=r, pilot_root=root, api_log=api_log))
                break

    return pilots


HTML_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>aimodeltrain — pilot viewer</title>
<style>
  :root {
    --bg: #0b0d12;
    --panel: #141823;
    --panel-2: #1c2230;
    --border: #262d3d;
    --fg: #e6e9ef;
    --fg-dim: #9aa3b2;
    --accent: #6ea8fe;
    --good: #4ade80;
    --bad: #f87171;
    --warn: #fbbf24;
  }
  * { box-sizing: border-box; }
  html, body { margin: 0; padding: 0; background: var(--bg); color: var(--fg);
               font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
               font-size: 14px; }
  header { position: sticky; top: 0; z-index: 5; background: var(--panel);
           border-bottom: 1px solid var(--border); padding: 12px 20px;
           display: flex; align-items: center; gap: 16px; flex-wrap: wrap; }
  header h1 { font-size: 16px; margin: 0; font-weight: 600; letter-spacing: 0.3px; }
  header h1 .dim { color: var(--fg-dim); font-weight: 400; }
  .controls { display: flex; gap: 10px; align-items: center; flex-wrap: wrap; margin-left: auto; }
  select, input[type=text] {
    background: var(--panel-2); border: 1px solid var(--border); color: var(--fg);
    padding: 6px 10px; border-radius: 6px; font-size: 13px; min-width: 130px;
  }
  .stats { display: flex; gap: 18px; padding: 14px 20px; background: var(--panel);
           border-bottom: 1px solid var(--border); flex-wrap: wrap; font-size: 13px; }
  .stat { display: flex; flex-direction: column; gap: 2px; min-width: 110px; }
  .stat .label { color: var(--fg-dim); font-size: 11px; text-transform: uppercase; letter-spacing: 0.6px; }
  .stat .value { font-size: 18px; font-weight: 600; }
  .breakdowns { padding: 8px 20px 14px; background: var(--panel);
                border-bottom: 1px solid var(--border); display: flex; gap: 24px; flex-wrap: wrap;
                font-size: 12px; color: var(--fg-dim); }
  .breakdowns .group { max-width: 360px; }
  .breakdowns .group b { color: var(--fg); display: block; margin-bottom: 4px; font-weight: 500;
                          text-transform: uppercase; letter-spacing: 0.5px; font-size: 11px; }
  .breakdowns .pill { display: inline-block; background: var(--panel-2); border: 1px solid var(--border);
                      padding: 2px 7px; border-radius: 999px; margin: 2px 4px 2px 0; color: var(--fg); }
  .breakdowns .pill .n { color: var(--fg-dim); margin-left: 4px; }
  main { padding: 18px 20px 40px; }
  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(220px, 1fr));
          gap: 14px; }
  .card { background: var(--panel); border: 1px solid var(--border); border-radius: 10px;
          overflow: hidden; cursor: pointer; transition: transform .08s, border-color .08s; display: flex; flex-direction: column; }
  .card:hover { transform: translateY(-1px); border-color: var(--accent); }
  .thumb { background: #fff; aspect-ratio: 1 / 1; display: flex; align-items: center; justify-content: center;
           overflow: hidden; position: relative; }
  .thumb img { max-width: 100%; max-height: 100%; object-fit: contain; display: block; }
  .thumb .missing { color: #888; font-size: 12px; padding: 12px; text-align: center; }
  .meta { padding: 10px 12px; font-size: 12px; display: flex; flex-direction: column; gap: 4px; }
  .meta .row { display: flex; justify-content: space-between; gap: 6px; align-items: center; }
  .meta .id { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; color: var(--fg-dim); font-size: 11px; }
  .meta .brand { font-weight: 600; }
  .meta .type { color: var(--fg-dim); }
  .badges { display: flex; gap: 4px; flex-wrap: wrap; }
  .badge { font-size: 10.5px; padding: 2px 6px; border-radius: 4px; border: 1px solid transparent;
           letter-spacing: 0.4px; text-transform: uppercase; font-weight: 600; }
  .badge.ok { background: rgba(74,222,128,.12); color: var(--good); border-color: rgba(74,222,128,.4); }
  .badge.bad { background: rgba(248,113,113,.12); color: var(--bad); border-color: rgba(248,113,113,.4); }
  .badge.warn { background: rgba(251,191,36,.12); color: var(--warn); border-color: rgba(251,191,36,.4); }
  .badge.repair { background: rgba(110,168,254,.12); color: var(--accent); border-color: rgba(110,168,254,.4); }
  .empty { color: var(--fg-dim); padding: 40px; text-align: center; }

  /* Modal */
  dialog { background: var(--panel); color: var(--fg); border: 1px solid var(--border);
           border-radius: 12px; padding: 0; max-width: 1100px; width: 92vw; max-height: 92vh; overflow: hidden; }
  dialog::backdrop { background: rgba(0,0,0,0.7); }
  .modal-head { display: flex; align-items: center; justify-content: space-between;
                padding: 14px 18px; border-bottom: 1px solid var(--border); position: sticky; top: 0;
                background: var(--panel); z-index: 2; }
  .modal-head .title { font-weight: 600; }
  .modal-head .title .dim { color: var(--fg-dim); font-weight: 400; margin-left: 6px; }
  .modal-head .close { background: transparent; color: var(--fg-dim); border: 1px solid var(--border);
                       border-radius: 6px; padding: 4px 10px; cursor: pointer; }
  .modal-body { padding: 18px; display: grid; grid-template-columns: minmax(280px, 460px) 1fr;
                gap: 18px; max-height: calc(92vh - 60px); overflow: auto; }
  @media (max-width: 900px) { .modal-body { grid-template-columns: 1fr; } }
  .modal-render { background: #fff; border: 1px solid var(--border); border-radius: 8px;
                  display: flex; align-items: center; justify-content: center; overflow: hidden;
                  min-height: 240px; }
  .modal-render img { max-width: 100%; max-height: 70vh; }
  .section { margin-bottom: 16px; }
  .section h3 { margin: 0 0 6px 0; font-size: 12px; text-transform: uppercase; letter-spacing: 0.7px;
                color: var(--fg-dim); font-weight: 600; }
  .kv { display: grid; grid-template-columns: 140px 1fr; gap: 4px 12px; font-size: 13px; }
  .kv dt { color: var(--fg-dim); }
  .kv dd { margin: 0; }
  .palette { display: flex; gap: 4px; }
  .palette span { width: 22px; height: 22px; border-radius: 4px; border: 1px solid var(--border); }
  table.scores { width: 100%; border-collapse: collapse; font-size: 13px; }
  table.scores th, table.scores td { text-align: left; padding: 4px 8px; border-bottom: 1px solid var(--border); }
  table.scores th { color: var(--fg-dim); font-weight: 500; font-size: 11px; text-transform: uppercase; letter-spacing: 0.5px; }
  table.scores td.score { text-align: right; font-variant-numeric: tabular-nums; font-weight: 600; }
  .score.s-low { color: var(--bad); }
  .score.s-mid { color: var(--warn); }
  .score.s-high { color: var(--good); }
  .violations, .issues { list-style: none; padding: 0; margin: 0; font-size: 13px; }
  .violations li, .issues li { background: var(--panel-2); border: 1px solid var(--border);
                                padding: 6px 10px; border-radius: 6px; margin-bottom: 6px; }
  .violations .kind, .issues .kind { color: var(--bad); font-weight: 600; font-family: ui-monospace, monospace; font-size: 12px; }
  .issues .sev { float: right; font-size: 11px; padding: 1px 6px; border-radius: 4px; border: 1px solid var(--border); }
  .issues .sev.high { background: rgba(248,113,113,.12); color: var(--bad); border-color: rgba(248,113,113,.4); }
  .issues .sev.med { background: rgba(251,191,36,.12); color: var(--warn); border-color: rgba(251,191,36,.4); }
  .issues .sev.low { background: rgba(110,168,254,.12); color: var(--accent); border-color: rgba(110,168,254,.4); }
  pre.code { background: #0a0c11; border: 1px solid var(--border); border-radius: 6px;
             padding: 10px; font-size: 11.5px; line-height: 1.45; overflow: auto; max-height: 320px;
             font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; }
  details { background: var(--panel-2); border: 1px solid var(--border); border-radius: 6px; margin-bottom: 6px; }
  details > summary { padding: 6px 10px; cursor: pointer; font-size: 12.5px; color: var(--fg-dim); }
  details[open] > summary { color: var(--fg); }
  details > div { padding: 0 10px 10px; }
  .tabs { display: flex; gap: 4px; border-bottom: 1px solid var(--border); margin-bottom: 8px; }
  .tab { padding: 6px 10px; cursor: pointer; color: var(--fg-dim); font-size: 12.5px;
         border-bottom: 2px solid transparent; }
  .tab.active { color: var(--fg); border-bottom-color: var(--accent); }
  .build-meta { color: var(--fg-dim); font-size: 11px; padding: 8px 20px; text-align: right; }
</style>
</head>
<body>
<header>
  <h1>aimodeltrain <span class="dim">— pilot viewer</span></h1>
  <div class="controls">
    <select id="pilot"></select>
    <select id="status">
      <option value="all">all</option>
      <option value="validated">validated</option>
      <option value="rejected">rejected</option>
    </select>
    <select id="design-type"><option value="all">all types</option></select>
    <select id="brand"><option value="all">all brands</option></select>
    <select id="sort">
      <option value="id">sort: id</option>
      <option value="overall-desc">sort: judge overall ↓</option>
      <option value="overall-asc">sort: judge overall ↑</option>
      <option value="violations-desc">sort: violations ↓</option>
      <option value="brand">sort: brand</option>
      <option value="type">sort: design type</option>
    </select>
    <input type="text" id="search" placeholder="search id / brand / violation…" />
  </div>
</header>
<section class="stats" id="stats"></section>
<section class="breakdowns" id="breakdowns"></section>
<main>
  <div class="grid" id="grid"></div>
  <div class="empty" id="empty" style="display:none">no samples in this view</div>
</main>
<div class="build-meta" id="buildmeta"></div>

<dialog id="modal">
  <div class="modal-head">
    <div class="title" id="modal-title"></div>
    <button class="close" id="modal-close">close</button>
  </div>
  <div class="modal-body" id="modal-body"></div>
</dialog>

<script type="application/json" id="data">__DATA__</script>
<script>
(function () {
  const DATA = JSON.parse(document.getElementById("data").textContent);
  if (!DATA.pilots || !DATA.pilots.length) {
    document.getElementById("grid").style.display = "none";
    document.getElementById("empty").style.display = "block";
    document.getElementById("empty").textContent = "No pilots found. Re-run scripts/build_viewer.py after generating data.";
    document.getElementById("buildmeta").textContent = "built " + DATA.built_at;
    return;
  }

  const els = {
    pilot: document.getElementById("pilot"),
    status: document.getElementById("status"),
    type: document.getElementById("design-type"),
    brand: document.getElementById("brand"),
    sort: document.getElementById("sort"),
    search: document.getElementById("search"),
    stats: document.getElementById("stats"),
    breakdowns: document.getElementById("breakdowns"),
    grid: document.getElementById("grid"),
    empty: document.getElementById("empty"),
    modal: document.getElementById("modal"),
    modalTitle: document.getElementById("modal-title"),
    modalBody: document.getElementById("modal-body"),
    modalClose: document.getElementById("modal-close"),
    buildmeta: document.getElementById("buildmeta"),
  };

  DATA.pilots.forEach((p, i) => {
    const opt = document.createElement("option");
    opt.value = String(i);
    opt.textContent = p.name + " (" + p.samples.length + ")";
    els.pilot.appendChild(opt);
  });

  els.buildmeta.textContent = "built " + DATA.built_at + " · " + DATA.pilots.length + " pilot(s) · " +
    DATA.pilots.reduce((a, p) => a + p.samples.length, 0) + " samples";

  function currentPilot() { return DATA.pilots[parseInt(els.pilot.value, 10) || 0]; }

  function fmtPct(n) { return (Math.round(n * 10) / 10).toFixed(1) + "%"; }
  function scoreClass(s) { if (s == null) return ""; if (s >= 7) return "s-high"; if (s >= 5) return "s-mid"; return "s-low"; }
  function el(tag, props, ...kids) {
    const e = document.createElement(tag);
    if (props) for (const k in props) {
      if (k === "class") e.className = props[k];
      else if (k === "style") e.style.cssText = props[k];
      else if (k.startsWith("on")) e.addEventListener(k.slice(2), props[k]);
      else e.setAttribute(k, props[k]);
    }
    for (const k of kids) {
      if (k == null) continue;
      e.appendChild(typeof k === "string" ? document.createTextNode(k) : k);
    }
    return e;
  }

  function refreshFiltersForPilot() {
    const p = currentPilot();
    const types = new Set(), brands = new Set();
    p.samples.forEach(s => { if (s.design_type) types.add(s.design_type); if (s.company_name) brands.add(s.company_name); });
    els.type.innerHTML = '<option value="all">all types</option>' +
      Array.from(types).sort().map(t => `<option>${t}</option>`).join("");
    els.brand.innerHTML = '<option value="all">all brands</option>' +
      Array.from(brands).sort().map(b => `<option>${b}</option>`).join("");
  }

  function renderStats() {
    const p = currentPilot();
    const s = p.stats;
    const items = [
      ["total", s.total],
      ["validated", s.validated],
      ["rejected", s.rejected],
      ["yield", fmtPct(s.yield_pct)],
    ];
    if (s.mean_judge_scores && s.mean_judge_scores.overall != null) {
      items.push(["judge mean", s.mean_judge_scores.overall.toFixed(2)]);
    }
    if (s.cost && s.cost.total_usd) {
      items.push(["api cost", "$" + s.cost.total_usd.toFixed(2)]);
      items.push(["api calls", s.cost.calls]);
    }
    els.stats.innerHTML = "";
    items.forEach(([label, value]) => {
      const div = el("div", { class: "stat" }, el("div", { class: "label" }, label), el("div", { class: "value" }, String(value)));
      els.stats.appendChild(div);
    });

    // breakdowns
    els.breakdowns.innerHTML = "";
    function groupPills(title, obj) {
      if (!obj || !Object.keys(obj).length) return;
      const g = el("div", { class: "group" }, el("b", null, title));
      Object.entries(obj).forEach(([k, v]) => {
        g.appendChild(el("span", { class: "pill" }, k, el("span", { class: "n" }, String(v))));
      });
      els.breakdowns.appendChild(g);
    }
    groupPills("by design type", s.by_design_type);
    groupPills("by brand", s.by_brand);
    groupPills("violation kinds", s.violation_kinds);
    if (s.mean_judge_scores && Object.keys(s.mean_judge_scores).length) {
      const g = el("div", { class: "group" }, el("b", null, "mean judge scores"));
      Object.entries(s.mean_judge_scores).forEach(([k, v]) => {
        g.appendChild(el("span", { class: "pill" }, k, el("span", { class: "n" }, v.toFixed(2))));
      });
      els.breakdowns.appendChild(g);
    }
  }

  function visible(sample) {
    if (els.status.value !== "all" && sample.status !== els.status.value) return false;
    if (els.type.value !== "all" && sample.design_type !== els.type.value) return false;
    if (els.brand.value !== "all" && sample.company_name !== els.brand.value) return false;
    const q = els.search.value.trim().toLowerCase();
    if (q) {
      const hay = [
        sample.id, sample.design_type, sample.company_name, sample.tone, sample.layout, sample.vertical,
        ...(sample.violations || []).map(v => v.kind),
        ...((sample.visual_judge?.issues) || []).map(i => i.kind || ""),
      ].filter(Boolean).join(" ").toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  }

  function sorted(samples) {
    const v = els.sort.value;
    const overall = s => (s.visual_judge?.scores?.overall ?? -1);
    const arr = samples.slice();
    switch (v) {
      case "overall-desc": arr.sort((a, b) => overall(b) - overall(a)); break;
      case "overall-asc": arr.sort((a, b) => overall(a) - overall(b)); break;
      case "violations-desc": arr.sort((a, b) => (b.violations?.length || 0) - (a.violations?.length || 0)); break;
      case "brand": arr.sort((a, b) => (a.company_name || "").localeCompare(b.company_name || "")); break;
      case "type": arr.sort((a, b) => (a.design_type || "").localeCompare(b.design_type || "")); break;
      default: arr.sort((a, b) => (a.id || "").localeCompare(b.id || ""));
    }
    return arr;
  }

  function card(s) {
    const c = el("div", { class: "card", onclick: () => openModal(s) });
    const thumb = el("div", { class: "thumb" });
    if (s.screenshot) {
      const img = new Image();
      img.src = s.screenshot;
      img.alt = s.id || "";
      img.loading = "lazy";
      img.onerror = () => { thumb.innerHTML = '<div class="missing">render missing<br>' + (s.screenshot || "") + '</div>'; };
      thumb.appendChild(img);
    } else {
      thumb.appendChild(el("div", { class: "missing" }, "no render"));
    }
    c.appendChild(thumb);
    const badges = el("div", { class: "badges" });
    badges.appendChild(el("span", { class: "badge " + (s.status === "validated" ? "ok" : "bad") }, s.status));
    if (s.visual_judge?.scores?.overall != null) {
      const sc = s.visual_judge.scores.overall;
      const cls = sc >= 7 ? "ok" : sc >= 5 ? "warn" : "bad";
      badges.appendChild(el("span", { class: "badge " + cls }, "j " + sc));
    }
    if (s.repair_history && s.repair_history.length) {
      badges.appendChild(el("span", { class: "badge repair" }, "repair " + s.repair_history.length));
    }
    const meta = el("div", { class: "meta" },
      el("div", { class: "row" }, el("span", { class: "brand" }, s.company_name || "—"), badges),
      el("div", { class: "row" }, el("span", { class: "type" }, s.design_type || ""), el("span", { class: "id" }, s.id || "")),
    );
    c.appendChild(meta);
    return c;
  }

  function renderGrid() {
    const p = currentPilot();
    const samples = sorted(p.samples.filter(visible));
    els.grid.innerHTML = "";
    if (!samples.length) {
      els.grid.style.display = "none";
      els.empty.style.display = "block";
    } else {
      els.grid.style.display = "";
      els.empty.style.display = "none";
      samples.forEach(s => els.grid.appendChild(card(s)));
    }
  }

  function modalSection(title, body) {
    return el("div", { class: "section" }, el("h3", null, title), body);
  }

  function openModal(s) {
    els.modalTitle.innerHTML = "";
    els.modalTitle.appendChild(el("span", null, (s.company_name || "—") + " · " + (s.design_type || "")));
    els.modalTitle.appendChild(el("span", { class: "dim" }, s.id || ""));

    els.modalBody.innerHTML = "";

    const left = el("div");
    const renderBox = el("div", { class: "modal-render" });
    if (s.screenshot) {
      const img = new Image();
      img.src = s.screenshot;
      img.alt = s.id || "";
      img.onerror = () => { renderBox.innerHTML = '<div style="color:#888;padding:18px">render missing<br>' + s.screenshot + '</div>'; };
      renderBox.appendChild(img);
    } else {
      renderBox.innerHTML = '<div style="color:#888;padding:18px">no render</div>';
    }
    left.appendChild(renderBox);

    // brief
    const palette = el("div", { class: "palette" });
    (s.palette_hex || []).forEach(h => palette.appendChild(el("span", { style: "background:" + h, title: h })));
    const kv = el("dl", { class: "kv" });
    function addKV(k, v) { if (v == null || v === "") return; kv.appendChild(el("dt", null, k)); kv.appendChild(el("dd", null, typeof v === "string" ? v : v)); }
    addKV("status", s.status);
    if (s.visual_judge?.ship != null) addKV("ship", String(s.visual_judge.ship));
    addKV("vertical", s.vertical);
    addKV("audience", s.audience);
    addKV("tone", s.tone);
    addKV("layout", s.layout);
    addKV("value prop", s.value_prop);
    addKV("logo concept", s.logo_concept);
    addKV("palette", s.palette_name);
    if (palette.children.length) kv.appendChild(el("dt", null, "swatches")), kv.appendChild(el("dd", null, palette));
    if (s.canvas) addKV("canvas", s.canvas.w + " × " + s.canvas.h + " (" + (s.canvas.shape || "rect") + ")");
    left.appendChild(modalSection("brief", kv));

    const right = el("div");

    // scores
    if (s.visual_judge?.scores && Object.keys(s.visual_judge.scores).length) {
      const t = el("table", { class: "scores" });
      const thead = el("thead", null, el("tr", null, el("th", null, "axis"), el("th", { class: "score" }, "score")));
      t.appendChild(thead);
      const tb = el("tbody");
      Object.entries(s.visual_judge.scores).forEach(([k, v]) => {
        tb.appendChild(el("tr", null, el("td", null, k), el("td", { class: "score " + scoreClass(v) }, String(v))));
      });
      t.appendChild(tb);
      right.appendChild(modalSection("visual judge scores" + (s.visual_judge.provider ? " (" + s.visual_judge.provider + ")" : ""), t));
    }

    if (s.visual_judge?.issues && s.visual_judge.issues.length) {
      const ul = el("ul", { class: "issues" });
      s.visual_judge.issues.forEach(it => {
        const li = el("li");
        const sev = (it.severity || "").toLowerCase();
        if (sev) li.appendChild(el("span", { class: "sev " + sev }, sev));
        li.appendChild(el("span", { class: "kind" }, it.kind || "issue"));
        if (it.note) li.appendChild(el("div", null, it.note));
        ul.appendChild(li);
      });
      right.appendChild(modalSection("judge issues", ul));
    }

    if (s.violations && s.violations.length) {
      const ul = el("ul", { class: "violations" });
      s.violations.forEach(v => {
        const li = el("li");
        li.appendChild(el("span", { class: "kind" }, v.kind || "violation"));
        const detail = Object.entries(v).filter(([k]) => k !== "kind")
          .map(([k, val]) => k + ": " + (typeof val === "object" ? JSON.stringify(val) : String(val))).join("  ");
        if (detail) li.appendChild(el("div", { style: "color:var(--fg-dim);font-size:12px;margin-top:2px;font-family:ui-monospace,monospace;word-break:break-word" }, detail));
        ul.appendChild(li);
      });
      right.appendChild(modalSection("stage-1 violations", ul));
    } else if (s.status === "validated") {
      right.appendChild(modalSection("stage-1 violations", el("div", { style: "color:var(--fg-dim);font-size:13px" }, "none — clean pass")));
    }

    if (s.repair_history && s.repair_history.length) {
      const wrap = el("div");
      s.repair_history.forEach((entry, i) => {
        const d = document.createElement("details");
        const sum = document.createElement("summary");
        sum.textContent = "repair round " + (entry.round || i + 1) + (entry.at ? " · " + entry.at : "");
        d.appendChild(sum);
        const body = document.createElement("div");
        if (entry.feedback) {
          body.appendChild(el("div", { style: "font-size:12px;color:var(--fg-dim);margin:6px 0" }, "feedback to teacher"));
          body.appendChild(el("pre", { class: "code" }, typeof entry.feedback === "string" ? entry.feedback : JSON.stringify(entry.feedback, null, 2)));
        }
        if (entry.previous_html) {
          body.appendChild(el("div", { style: "font-size:12px;color:var(--fg-dim);margin:6px 0" }, "previous html (pre-repair)"));
          body.appendChild(el("pre", { class: "code" }, entry.previous_html));
        }
        d.appendChild(body);
        wrap.appendChild(d);
      });
      right.appendChild(modalSection("repair history (" + s.repair_history.length + ")", wrap));
    }

    if (s.teacher_meta) {
      const kv2 = el("dl", { class: "kv" });
      Object.entries(s.teacher_meta).forEach(([k, v]) => { addKV2(kv2, k, v); });
      right.appendChild(modalSection("teacher meta", kv2));
    }

    if (s.html) {
      const d = document.createElement("details");
      d.appendChild(Object.assign(document.createElement("summary"), { textContent: "current html (" + s.html.length + " chars)" }));
      const body = document.createElement("div");
      body.appendChild(el("pre", { class: "code" }, s.html));
      d.appendChild(body);
      right.appendChild(modalSection("source", d));
    }

    els.modalBody.appendChild(left);
    els.modalBody.appendChild(right);
    els.modal.showModal();
  }

  function addKV2(kv, k, v) {
    kv.appendChild(el("dt", null, k));
    kv.appendChild(el("dd", null, typeof v === "object" ? JSON.stringify(v) : String(v)));
  }

  function applyAll() { renderStats(); refreshFiltersForPilot(); renderGrid(); }
  els.pilot.addEventListener("change", () => applyAll());
  [els.status, els.type, els.brand, els.sort].forEach(e => e.addEventListener("change", renderGrid));
  els.search.addEventListener("input", renderGrid);
  els.modalClose.addEventListener("click", () => els.modal.close());
  els.modal.addEventListener("click", (e) => { if (e.target === els.modal) els.modal.close(); });

  // initial render: most recent pilot first (last in array is the live one if appended last,
  // but we list live first in python — so default to index 0).
  els.pilot.value = "0";
  applyAll();
})();
</script>
</body>
</html>
"""


def main():
    pilots = discover_pilots()
    manifest = {
        "built_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ"),
        "pilots": pilots,
    }
    data_json = json.dumps(manifest, ensure_ascii=False, separators=(",", ":"))
    # Prevent the JSON from ever containing a </script> sequence that would
    # terminate the embedding <script> tag.
    data_json = data_json.replace("</", "<\\/")
    out = REPO_ROOT / "viewer.html"
    html = HTML_TEMPLATE.replace("__DATA__", data_json)
    out.write_text(html, encoding="utf-8")
    total = sum(len(p["samples"]) for p in pilots)
    print(f"wrote {out} — {len(pilots)} pilot(s), {total} sample(s)")
    for p in pilots:
        s = p["stats"]
        print(f"  - {p['name']}: total={s['total']} validated={s['validated']} rejected={s['rejected']} yield={s['yield_pct']}%")


if __name__ == "__main__":
    main()
