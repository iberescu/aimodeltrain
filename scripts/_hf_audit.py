"""Quick audit of all HF resources to see what's still running."""
import os, sys, requests
sys.stdout.reconfigure(encoding="utf-8")
from huggingface_hub import HfApi

tok = os.environ["HUGGINGFACE_HUB_TOKEN"]
hdr = {"Authorization": f"Bearer {tok}"}
api = HfApi(token=tok)

print("=== Space iberescu2201/aimt-vllm ===")
rt = api.get_space_runtime(repo_id="iberescu2201/aimt-vllm")
print(f"  stage={rt.stage}  hardware={rt.hardware}  requested={rt.requested_hardware}")
print(f"  sleep_time={rt.sleep_time}s")

print("\n=== Endpoint aimt-dryrun ===")
try:
    ep = api.get_inference_endpoint("aimt-dryrun", namespace="iberescu2201")
    comp = ep.raw["compute"]
    print(f"  status={ep.status}  hw={comp['instanceType']} {comp['instanceSize']} ({comp['accelerator']})")
except Exception as e:
    print(f"  none ({e})")

print("\n=== Active Jobs ===")
r = requests.get("https://huggingface.co/api/jobs/iberescu2201", headers=hdr, timeout=30)
if r.ok:
    jobs = r.json() if isinstance(r.json(), list) else []
    running_states = {"RUNNING", "SCHEDULING", "UPDATING", "APP_STARTING", "BUILDING", "PENDING"}
    running = [j for j in jobs if (j.get("status") or {}).get("stage") in running_states]
    if not running:
        print("  (none running)")
    for j in running:
        print(f"  {j['id']}  stage={j['status']['stage']}  flavor={j.get('flavor')}")
else:
    print(f"  list failed: HTTP {r.status_code}")
