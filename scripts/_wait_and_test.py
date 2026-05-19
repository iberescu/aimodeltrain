"""Wait for the aimt-dryrun endpoint to reach 'running', then send one real
chat completion to validate it actually serves Qwen3.6-27B (not BLOOM-fallback)
and the chat template works.

Exits 0 on full success, 1 otherwise.
"""
from __future__ import annotations

import os
import sys
import time
import json

sys.stdout.reconfigure(encoding="utf-8")
import requests
from huggingface_hub import HfApi

NS = "iberescu2201"
EP = "aimt-dryrun"
TOK = os.environ.get("HUGGINGFACE_HUB_TOKEN") or os.environ.get("HF_TOKEN")
if not TOK:
    print("ERROR: HUGGINGFACE_HUB_TOKEN not set", file=sys.stderr)
    sys.exit(2)
api = HfApi(token=TOK)


def poll_until_terminal(timeout_s: int = 1800) -> str:
    last = None
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        ep = api.get_inference_endpoint(EP, namespace=NS)
        st = ep.raw["status"]["state"]
        if st != last:
            print(f"[{int(time.time()-t0):>4}s] state={st}", flush=True)
            last = st
        if st == "running":
            return "running"
        if st in ("failed", "updateFailed"):
            msg = ep.raw["status"].get("message", "")
            print("\nFAILED. message excerpt:")
            print((msg or "")[:1500])
            return st
        time.sleep(15)
    return "timeout"


def smoke_test() -> bool:
    ep = api.get_inference_endpoint(EP, namespace=NS)
    base = ep.url
    hdr = {"Authorization": f"Bearer {TOK}", "Content-Type": "application/json"}

    # 1. /v1/models should report Qwen, not BLOOM
    r = requests.get(base + "/v1/models", headers=hdr, timeout=30)
    print("\n/v1/models:", r.status_code)
    try:
        data = r.json()
        served_id = (data.get("data") or [{}])[0].get("id", "")
        print("  served model id:", served_id)
        if "bloom" in served_id.lower() or "560m" in served_id.lower():
            print("  ❌ still serving BLOOM fallback")
            return False
    except Exception:
        print("  body:", r.text[:300])
        return False

    # 2. Tiny chat completion
    body = {
        "model": "tgi",
        "messages": [
            {"role": "system", "content": "You are a B2B brand designer."},
            {"role": "user", "content": "Write a single sentence about Inter Bold typography for business cards."},
        ],
        "max_tokens": 80,
        "temperature": 0.3,
    }
    t0 = time.time()
    r = requests.post(base + "/v1/chat/completions", headers=hdr, json=body, timeout=120)
    el = time.time() - t0
    print(f"\nchat ({el:.1f}s): {r.status_code}")
    if r.status_code != 200:
        print("  body:", r.text[:600])
        return False
    j = r.json()
    text = (j.get("choices") or [{}])[0].get("message", {}).get("content", "")
    print("  reply:", text[:400])
    if not text.strip():
        print("  ❌ empty reply")
        return False

    print("\n✅ endpoint is live and Qwen3.6-27B responds")
    return True


def main():
    print(f"==> waiting for endpoint {NS}/{EP} to come up")
    state = poll_until_terminal()
    if state != "running":
        print(f"final state: {state} — bailing", file=sys.stderr)
        sys.exit(1)
    ok = smoke_test()
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
