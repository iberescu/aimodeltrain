"""Poll a single HF Job to terminal state (COMPLETED / ERROR / CANCELED).
On COMPLETED, verify the output adapter repo exists and report file list.
Usage: python scripts/_wait_job.py <job_id>
"""
from __future__ import annotations

import os
import sys
import time
import requests


def main():
    if len(sys.argv) < 2:
        print("usage: _wait_job.py <job_id>"); sys.exit(2)
    jid = sys.argv[1]
    tok = os.environ.get("HUGGINGFACE_HUB_TOKEN") or os.environ.get("HF_TOKEN")
    if not tok:
        print("ERROR: no token"); sys.exit(2)

    sys.stdout.reconfigure(encoding="utf-8")
    hdr = {"Authorization": f"Bearer {tok}"}
    url = f"https://huggingface.co/api/jobs/iberescu2201/{jid}"

    last = None
    t0 = time.time()
    TIMEOUT = 4 * 3600
    while time.time() - t0 < TIMEOUT:
        r = requests.get(url, headers=hdr, timeout=30)
        info = r.json()
        stage = (info.get("status") or {}).get("stage")
        if stage != last:
            print(f"[{int(time.time()-t0):>5}s] stage={stage}", flush=True)
            last = stage
        if stage in ("COMPLETED",):
            print("DONE — verifying adapter repo")
            from huggingface_hub import HfApi
            api = HfApi(token=tok)
            for repo in ("iberescu2201/aimodeltrain-qwen2.5coder-7b-lora-1k-v1",
                         "iberescu2201/aimodeltrain-qwen2.5coder-7b-merged-1k-v1"):
                try:
                    files = api.list_repo_files(repo, repo_type="model")
                    print(f"{repo}: {len(files)} files")
                except Exception as e:
                    print(f"{repo}: not found ({e})")
            sys.exit(0)
        if stage in ("ERROR", "CANCELED"):
            msg = (info.get("status") or {}).get("message") or ""
            print(f"FAILED: {msg[:1500]}")
            sys.exit(1)
        time.sleep(30)
    print("timeout waiting for job"); sys.exit(1)


if __name__ == "__main__":
    main()
