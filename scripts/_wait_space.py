"""Poll the aimt-vllm Space until it's RUNNING, then smoke-test /v1/models +
/v1/chat/completions. Exits 0 only if the model actually responds.
"""
from __future__ import annotations
import os, sys, time, requests
sys.stdout.reconfigure(encoding="utf-8")
from huggingface_hub import HfApi

SPACE = "iberescu2201/aimt-vllm"
TOK = os.environ.get("HUGGINGFACE_HUB_TOKEN") or os.environ.get("HF_TOKEN")
if not TOK:
    print("ERROR: no token", file=sys.stderr); sys.exit(2)
api = HfApi(token=TOK)

HOST = "https://iberescu2201-aimt-vllm.hf.space"


def poll(timeout_s: int = 2400) -> str:
    """A push triggers a rebuild, but the prior error state lingers briefly.
    Ignore terminal states for the first 90 s of polling so we don't exit on
    stale carry-over. After that, trust them.
    """
    GRACE = 90
    last = None
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        rt = api.get_space_runtime(repo_id=SPACE)
        stage = getattr(rt, "stage", None)
        hw = getattr(rt, "hardware", None)
        if stage != last:
            print(f"[{int(time.time()-t0):>4}s] stage={stage}  hardware={hw}", flush=True)
            last = stage
        if stage == "RUNNING":
            return "RUNNING"
        if stage in ("BUILD_ERROR", "RUNTIME_ERROR", "STOPPED", "DELETED") and (time.time() - t0) > GRACE:
            return str(stage)
        time.sleep(15)
    return "TIMEOUT"


def smoke() -> bool:
    hdr = {"Authorization": f"Bearer {TOK}", "Content-Type": "application/json"}
    # 1. /health
    try:
        r = requests.get(HOST + "/health", headers=hdr, timeout=20)
        print(f"\n/health: {r.status_code} {r.text[:120]}")
    except Exception as e:
        print(f"/health err: {e}"); return False
    # 2. /v1/models — should report aimt-dryrun or Qwen
    r = requests.get(HOST + "/v1/models", headers=hdr, timeout=30)
    print(f"/v1/models: {r.status_code} {r.text[:300]}")
    if r.status_code != 200: return False
    try:
        ids = [m.get("id") for m in (r.json().get("data") or [])]
    except Exception:
        ids = []
    print(f"  served ids: {ids}")
    if any("bloom" in (i or "").lower() for i in ids):
        print("  ❌ serving BLOOM fallback"); return False
    # 3. base-model chat
    t0 = time.time()
    r = requests.post(HOST + "/v1/chat/completions", headers=hdr, timeout=120, json={
        "model": "aimt-dryrun",
        "messages": [{"role": "user", "content": "Reply with exactly: PONG"}],
        "max_tokens": 8,
        "temperature": 0,
    })
    print(f"\nbase chat ({time.time()-t0:.1f}s): {r.status_code}")
    print(f"  body: {r.text[:400]}")
    if r.status_code != 200: return False
    # 4. adapter chat
    t0 = time.time()
    r = requests.post(HOST + "/v1/chat/completions", headers=hdr, timeout=120, json={
        "model": "dryrun",
        "messages": [{"role": "user", "content": "Reply with exactly: PONG"}],
        "max_tokens": 8,
        "temperature": 0,
    })
    print(f"\nadapter chat ({time.time()-t0:.1f}s): {r.status_code}")
    print(f"  body: {r.text[:400]}")
    return r.status_code == 200


def main():
    state = poll()
    if state != "RUNNING":
        print(f"final state: {state}", file=sys.stderr)
        sys.exit(1)
    print("\n==> Space is RUNNING. Smoke testing…")
    sys.exit(0 if smoke() else 1)


if __name__ == "__main__":
    main()
