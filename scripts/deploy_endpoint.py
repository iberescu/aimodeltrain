"""Create / inspect / pause / delete an HF Inference Endpoint serving
Qwen3.6-27B with our LoRA adapter loaded on top via TGI.

Examples:

  # Create the endpoint and wait for it to be ready (~10-15 min first time)
  python scripts/deploy_endpoint.py up

  # Just check status / URL
  python scripts/deploy_endpoint.py status

  # Pause to stop billing without losing the config
  python scripts/deploy_endpoint.py pause

  # Resume a paused endpoint
  python scripts/deploy_endpoint.py resume

  # Delete it entirely
  python scripts/deploy_endpoint.py delete

Reads HUGGINGFACE_HUB_TOKEN from env (process or Windows user-scope).
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

ENDPOINT_NAME = "aimt-1k"
# Points at the merged model (base+LoRA), not a separate adapter — this lets us
# deploy via HF's default toolkit with no custom Docker image / LoRA mounting.
BASE_MODEL = "iberescu2201/aimodeltrain-qwen2.5coder-7b-merged-1k-v1"
NAMESPACE = "iberescu2201"

# Qwen2.5-Coder-7B fits easily on A10G 24GB in bf16. Cheaper + faster startup
# than the A100 we used for 27B.
INSTANCE_VENDOR = "aws"
INSTANCE_REGION = "us-east-1"
INSTANCE_TYPE = "nvidia-a10g"
INSTANCE_SIZE = "x1"
ACCELERATOR = "gpu"


def get_token() -> str:
    tok = os.environ.get("HUGGINGFACE_HUB_TOKEN") or os.environ.get("HF_TOKEN")
    if not tok:
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment") as key:
                tok = winreg.QueryValueEx(key, "HUGGINGFACE_HUB_TOKEN")[0]
        except Exception:
            pass
    if not tok:
        print("ERROR: HUGGINGFACE_HUB_TOKEN not set", file=sys.stderr)
        sys.exit(2)
    return tok


def get_api(token: str):
    from huggingface_hub import HfApi  # type: ignore
    return HfApi(token=token)


def get_endpoint(api, name=ENDPOINT_NAME, namespace=NAMESPACE):
    from huggingface_hub.errors import HfHubHTTPError  # type: ignore
    try:
        return api.get_inference_endpoint(name, namespace=namespace)
    except HfHubHTTPError as e:
        if e.response.status_code == 404:
            return None
        raise


def tgi_env_and_secrets(token: str) -> tuple[dict, dict]:
    """Env + secrets for the HF default toolkit serving our merged model.

    The merged model is a complete fine-tuned Qwen2.5-Coder-7B-Instruct — no
    LoRA mounting at inference, no custom image. The default toolkit
    auto-selects a backend (TGI for decoder-only) which knows Qwen2.5 natively.
    """
    env = {
        "HF_HUB_ENABLE_HF_TRANSFER": "1",
    }
    secrets = {"HF_TOKEN": token}  # to pull the private merged model
    return env, secrets


def create_endpoint(api):
    from huggingface_hub import create_inference_endpoint  # type: ignore
    print(f"==> creating endpoint '{ENDPOINT_NAME}' under '{NAMESPACE}'")
    print(f"    model: {BASE_MODEL}  (merged base+LoRA, default HF toolkit)")
    print(f"    hardware: {INSTANCE_VENDOR}/{INSTANCE_REGION} {INSTANCE_TYPE} {INSTANCE_SIZE} ({ACCELERATOR})")
    env, secrets = tgi_env_and_secrets(api.token)
    ep = create_inference_endpoint(
        name=ENDPOINT_NAME,
        namespace=NAMESPACE,
        repository=BASE_MODEL,
        framework="pytorch",
        task="text-generation",
        accelerator=ACCELERATOR,
        vendor=INSTANCE_VENDOR,
        region=INSTANCE_REGION,
        instance_type=INSTANCE_TYPE,
        instance_size=INSTANCE_SIZE,
        min_replica=1,
        max_replica=1,
        type="protected",
        env=env,
        secrets=secrets,
        token=api.token,
    )
    print(f"    started — name='{ep.name}'  url={getattr(ep, 'url', None) or '(not yet assigned)'}")
    return ep


def update_endpoint_env(api):
    """Push env + secrets onto an existing endpoint (triggers redeploy)."""
    env, secrets = tgi_env_and_secrets(api.token)
    print(f"==> updating endpoint '{ENDPOINT_NAME}' env + secrets (will trigger redeploy)")
    ep = api.update_inference_endpoint(
        name=ENDPOINT_NAME,
        namespace=NAMESPACE,
        env=env,
        secrets=secrets,
        repository=BASE_MODEL,
        framework="pytorch",
        task="text-generation",
        token=api.token,
    )
    return ep


def wait_until_running(api, timeout_s=1800):
    print(f"==> waiting for endpoint to come up (up to {timeout_s // 60} min)")
    t0 = time.time()
    last_status = None
    while time.time() - t0 < timeout_s:
        ep = get_endpoint(api)
        status = (ep.status if ep else "missing")
        if status != last_status:
            print(f"    [{int(time.time() - t0):>4}s] status={status}  url={getattr(ep, 'url', None) or '-'}")
            last_status = status
        if status == "running":
            print(f"    READY in {int(time.time() - t0)}s")
            return ep
        if status in ("failed", "error"):
            print(f"    FAILED — see https://endpoints.huggingface.co/{NAMESPACE}/endpoints/{ENDPOINT_NAME}", file=sys.stderr)
            return ep
        time.sleep(15)
    print(f"    timed out after {timeout_s}s; check the HF dashboard", file=sys.stderr)
    return get_endpoint(api)


def cmd_up(args):
    api = get_api(get_token())
    ep = get_endpoint(api)
    if ep is None:
        ep = create_endpoint(api)
    elif ep.status in ("paused", "scaledToZero"):
        print(f"==> endpoint exists in status='{ep.status}'; resuming")
        ep.resume(token=api.token)
    else:
        print(f"==> endpoint already exists with status='{ep.status}'")
    if not args.no_wait:
        ep = wait_until_running(api, timeout_s=args.timeout)
    cmd_status(args)


def cmd_status(_args):
    api = get_api(get_token())
    ep = get_endpoint(api)
    if not ep:
        print(f"no endpoint named '{ENDPOINT_NAME}' under '{NAMESPACE}'")
        return
    print(f"name:     {ep.name}")
    print(f"status:   {ep.status}")
    print(f"url:      {getattr(ep, 'url', None) or '-'}")
    raw = ep.raw if hasattr(ep, 'raw') else {}
    if raw:
        comp = raw.get("compute") or {}
        print(f"hardware: {comp.get('vendor')}/{comp.get('region')} {comp.get('instanceType')} {comp.get('instanceSize')} ({comp.get('accelerator')})")
        print(f"scaling:  min={comp.get('scaling', {}).get('minReplica')} max={comp.get('scaling', {}).get('maxReplica')}")
    if getattr(ep, "url", None):
        print()
        print(f"chat completions URL (paste into playground.html):")
        print(f"  {ep.url}/v1/chat/completions")


def cmd_pause(_args):
    api = get_api(get_token())
    ep = get_endpoint(api)
    if not ep:
        print("no endpoint to pause"); return
    print(f"pausing endpoint (status was '{ep.status}')")
    ep.pause(token=api.token)
    print("paused.")


def cmd_resume(args):
    api = get_api(get_token())
    ep = get_endpoint(api)
    if not ep:
        print("no endpoint to resume"); return
    if ep.status in ("paused", "scaledToZero"):
        print(f"resuming endpoint (status was '{ep.status}')")
        ep.resume(token=api.token)
    else:
        print(f"endpoint not paused (status='{ep.status}')")
    if not args.no_wait:
        wait_until_running(api, timeout_s=args.timeout)
    cmd_status(args)


def cmd_delete(_args):
    api = get_api(get_token())
    ep = get_endpoint(api)
    if not ep:
        print("no endpoint to delete"); return
    confirm = input(f"delete endpoint '{ep.name}'? (y/N) ").strip().lower()
    if confirm != "y":
        print("aborted."); return
    ep.delete(token=api.token)
    print("deleted.")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd")

    p_up = sub.add_parser("up", help="Create or resume the endpoint")
    p_up.add_argument("--no-wait", action="store_true")
    p_up.add_argument("--timeout", type=int, default=1800)

    sub.add_parser("status", help="Print current status + URL")
    sub.add_parser("pause", help="Pause the endpoint (stops billing)")

    p_re = sub.add_parser("resume", help="Resume a paused endpoint")
    p_re.add_argument("--no-wait", action="store_true")
    p_re.add_argument("--timeout", type=int, default=1800)

    p_up2 = sub.add_parser("update", help="Push env+secrets onto an existing endpoint (triggers redeploy)")
    p_up2.add_argument("--no-wait", action="store_true")
    p_up2.add_argument("--timeout", type=int, default=1800)

    sub.add_parser("delete", help="Delete the endpoint entirely")

    args = ap.parse_args()
    cmd = args.cmd or "status"
    funcs = {"up": cmd_up, "status": cmd_status, "pause": cmd_pause, "resume": cmd_resume, "delete": cmd_delete, "update": cmd_update}
    funcs[cmd](args)


def cmd_update(args):
    api = get_api(get_token())
    ep = get_endpoint(api)
    if not ep:
        print("no endpoint to update"); return
    update_endpoint_env(api)
    if not args.no_wait:
        wait_until_running(api, timeout_s=args.timeout)
    cmd_status(args)


if __name__ == "__main__":
    main()
