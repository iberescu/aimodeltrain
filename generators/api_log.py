"""Centralised API-call logging and cost arithmetic.

Every Gemini (or Anthropic) call across the pipeline appends ONE JSON line
to `logs/api_calls.jsonl` via `log_api_call(...)`. This is the canonical
source of truth for "how much did this run cost". `scripts/cost_report.py`
reads the same file.

The log is append-only and uses one-line-per-call JSON, which is safe to
read mid-run (each line is a complete record). Multiple async workers may
write concurrently — append-mode file open on Windows/NTFS is atomic up to
~4KB per write, and our records are well under that.

Cost helpers (`compute_cost_for_call`) read pricing from configs/pricing.json
so swapping pricing requires zero code changes.
"""
from __future__ import annotations

import json
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_REPO_ROOT = Path(__file__).resolve().parents[1]
_LOG_PATH = _REPO_ROOT / "logs" / "api_calls.jsonl"
_PRICING_PATH = _REPO_ROOT / "configs" / "pricing.json"

_LOCK = threading.Lock()  # file-append serialization within one process
_PRICING_CACHE: dict | None = None
_TIER_BREAKPOINT = 200_000  # prompts above this hit the higher tier


# ---------- pricing ----------

def _load_pricing() -> dict:
    global _PRICING_CACHE
    if _PRICING_CACHE is None:
        _PRICING_CACHE = json.loads(_PRICING_PATH.read_text(encoding="utf-8"))
    return _PRICING_CACHE


def _rate(rate_obj: dict, prompt_tokens: int) -> float:
    """Pick the right tier rate given the prompt size."""
    if "flat" in rate_obj:
        return float(rate_obj["flat"])
    if prompt_tokens > _TIER_BREAKPOINT:
        return float(rate_obj.get("gt_200k", rate_obj.get("le_200k", 0.0)))
    return float(rate_obj.get("le_200k", 0.0))


def compute_cost_for_call(call: dict) -> float:
    """Return USD cost for one log entry. Returns 0 for errored calls."""
    pricing = _load_pricing()["models"]
    model = call.get("model") or "gemini-3.1-pro-preview"
    rates = pricing.get(model)
    if rates is None:
        return 0.0

    tokens_in_billable = (call.get("tokens_in") or 0) - (call.get("tokens_cache") or 0)
    if tokens_in_billable < 0:
        tokens_in_billable = 0
    tokens_cache = call.get("tokens_cache") or 0
    # Output cost includes thinking tokens (per Gemini pricing page).
    tokens_out_billable = (call.get("tokens_out") or 0) + (call.get("tokens_thinking") or 0)

    prompt_size_total = (call.get("tokens_in") or 0)

    cost = 0.0
    cost += (tokens_in_billable / 1_000_000) * _rate(rates["input_per_million"], prompt_size_total)
    cost += (tokens_out_billable / 1_000_000) * _rate(rates["output_per_million"], prompt_size_total)
    if tokens_cache:
        cost += (tokens_cache / 1_000_000) * _rate(rates["cache_hit_per_million"], prompt_size_total)
    return cost


# ---------- logging ----------

def log_api_call(
    *,
    phase: str,
    model: str,
    sample_id: Optional[str],
    tokens_in: Optional[int],
    tokens_out: Optional[int],
    tokens_thinking: Optional[int] = None,
    tokens_cache: Optional[int] = None,
    elapsed_s: Optional[float] = None,
    error: Optional[str] = None,
    extra: Optional[dict] = None,
) -> None:
    """Append one line to logs/api_calls.jsonl describing this API call."""
    rec: dict = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "phase": phase,            # "generate" | "repair" | "judge_gemini" | "judge_anthropic"
        "model": model,
        "sample_id": sample_id,
        "tokens_in": tokens_in or 0,
        "tokens_out": tokens_out or 0,
        "tokens_thinking": tokens_thinking or 0,
        "tokens_cache": tokens_cache or 0,
        "elapsed_s": round(elapsed_s, 3) if elapsed_s is not None else None,
        "error": error,
    }
    if extra:
        rec["extra"] = extra
    rec["cost_usd"] = round(compute_cost_for_call(rec), 6)

    _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(rec, ensure_ascii=False) + "\n"
    with _LOCK:
        # 'a' mode + small line keeps appends atomic on Windows.
        with _LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(line)


def extract_usage_from_genai_response(resp) -> dict:
    """Pull the tokens we care about from a google-genai response object."""
    usage = getattr(resp, "usage_metadata", None) or getattr(resp, "usageMetadata", None)
    if usage is None:
        return {}
    return {
        "tokens_in":       getattr(usage, "prompt_token_count", 0)        or 0,
        "tokens_out":      getattr(usage, "candidates_token_count", 0)    or 0,
        "tokens_thinking": getattr(usage, "thoughts_token_count", 0)      or 0,
        "tokens_cache":    getattr(usage, "cached_content_token_count", 0) or 0,
    }
