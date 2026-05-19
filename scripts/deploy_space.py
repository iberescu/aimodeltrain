"""Create / update / inspect / pause the HF Space that serves Qwen3.6-27B
with our LoRA adapter via vLLM. Uses the Docker SDK so we control the
container entrypoint (HF Inference Endpoints' custom_image API doesn't
accept Docker command/args, which is why we're on a Space instead).

Examples:

  python scripts/deploy_space.py up        # create or update Space + upgrade hardware
  python scripts/deploy_space.py push      # just push Dockerfile + entrypoint changes
  python scripts/deploy_space.py status
  python scripts/deploy_space.py pause     # set hardware -> cpu-basic (stops billing)
  python scripts/deploy_space.py resume    # set hardware -> a100-large
  python scripts/deploy_space.py delete

Reads HUGGINGFACE_HUB_TOKEN from process env, .env, or Windows user-scope env.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

SPACE_REPO = "iberescu2201/aimt-vllm"
HARDWARE_RUN = "a100-large"
HARDWARE_PAUSE = "cpu-basic"
SPACE_DIR = REPO_ROOT / "space"


def load_dotenv() -> dict:
    out: dict = {}
    p = REPO_ROOT / ".env"
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            out[k.strip()] = v.strip().strip("'").strip('"')
    return out


def get_token() -> str:
    env = load_dotenv()
    tok = os.environ.get("HUGGINGFACE_HUB_TOKEN") or os.environ.get("HF_TOKEN") or env.get("HUGGINGFACE_HUB_TOKEN")
    if not tok:
        try:
            import winreg  # type: ignore
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment") as key:
                tok = winreg.QueryValueEx(key, "HUGGINGFACE_HUB_TOKEN")[0]
        except Exception:
            pass
    if not tok:
        print("ERROR: HUGGINGFACE_HUB_TOKEN not set", file=sys.stderr)
        sys.exit(2)
    return tok


def get_api(tok: str):
    from huggingface_hub import HfApi  # type: ignore
    return HfApi(token=tok)


def space_exists(api) -> bool:
    from huggingface_hub.errors import RepositoryNotFoundError  # type: ignore
    try:
        api.repo_info(SPACE_REPO, repo_type="space")
        return True
    except RepositoryNotFoundError:
        return False


def push_space(api):
    print(f"==> pushing Space files to {SPACE_REPO}")
    if not space_exists(api):
        print("    creating Space repo (Docker SDK, private)")
        api.create_repo(SPACE_REPO, repo_type="space", space_sdk="docker", private=True, exist_ok=True)
    api.upload_folder(
        repo_id=SPACE_REPO, repo_type="space",
        folder_path=str(SPACE_DIR),
        commit_message="push Dockerfile + entrypoint.sh + README",
    )
    print(f"    pushed -> https://huggingface.co/spaces/{SPACE_REPO}")


def set_secret(api, key: str, value: str):
    print(f"==> setting Space secret '{key}'")
    api.add_space_secret(repo_id=SPACE_REPO, key=key, value=value)


def set_hardware(api, flavor: str):
    print(f"==> requesting Space hardware '{flavor}'")
    api.request_space_hardware(repo_id=SPACE_REPO, hardware=flavor)


def get_status(api) -> dict:
    return api.get_space_runtime(repo_id=SPACE_REPO).__dict__


def wait_until_running(api, timeout_s: int = 1800) -> str:
    print(f"==> waiting for Space to come up (up to {timeout_s // 60} min)")
    last = None
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        rt = api.get_space_runtime(repo_id=SPACE_REPO)
        stage = getattr(rt, "stage", None)
        hw = getattr(rt, "hardware", None)
        if stage != last:
            print(f"    [{int(time.time()-t0):>4}s] stage={stage}  hardware={hw}")
            last = stage
        if stage == "RUNNING":
            return "RUNNING"
        if stage in ("BUILD_ERROR", "RUNTIME_ERROR", "STOPPED", "PAUSED"):
            return str(stage)
        time.sleep(15)
    return "TIMEOUT"


def cmd_up(args):
    api = get_api(get_token())
    push_space(api)
    set_secret(api, "HF_TOKEN", get_token())
    set_hardware(api, HARDWARE_RUN)
    if not args.no_wait:
        wait_until_running(api, timeout_s=args.timeout)
    cmd_status(args)


def cmd_push(_args):
    api = get_api(get_token())
    push_space(api)


def cmd_status(_args):
    api = get_api(get_token())
    if not space_exists(api):
        print(f"no Space named '{SPACE_REPO}'")
        return
    rt = api.get_space_runtime(repo_id=SPACE_REPO)
    print(f"repo:       https://huggingface.co/spaces/{SPACE_REPO}")
    print(f"stage:      {getattr(rt, 'stage', '?')}")
    print(f"hardware:   {getattr(rt, 'hardware', '?')}")
    print(f"sleep_time: {getattr(rt, 'sleep_time', '?')}")
    info = api.repo_info(SPACE_REPO, repo_type="space")
    if hasattr(info, "host"):
        print(f"host:       {info.host}")
    short = SPACE_REPO.replace("/", "-")
    print(f"chat URL:   https://{short}.hf.space/v1/chat/completions")


def cmd_pause(_args):
    api = get_api(get_token())
    set_hardware(api, HARDWARE_PAUSE)
    print("hardware set to cpu-basic — paid GPU billing stops once the rebuild lands.")


def cmd_resume(args):
    api = get_api(get_token())
    set_hardware(api, HARDWARE_RUN)
    if not args.no_wait:
        wait_until_running(api, timeout_s=args.timeout)
    cmd_status(args)


def cmd_delete(_args):
    api = get_api(get_token())
    if not space_exists(api):
        print("no Space to delete"); return
    confirm = input(f"delete Space '{SPACE_REPO}'? (y/N) ").strip().lower()
    if confirm != "y":
        print("aborted."); return
    api.delete_repo(SPACE_REPO, repo_type="space")
    print("deleted.")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd")
    p_up = sub.add_parser("up"); p_up.add_argument("--no-wait", action="store_true"); p_up.add_argument("--timeout", type=int, default=1800)
    sub.add_parser("push")
    sub.add_parser("status")
    sub.add_parser("pause")
    p_re = sub.add_parser("resume"); p_re.add_argument("--no-wait", action="store_true"); p_re.add_argument("--timeout", type=int, default=1800)
    sub.add_parser("delete")
    args = ap.parse_args()
    cmd = args.cmd or "status"
    funcs = {"up": cmd_up, "push": cmd_push, "status": cmd_status, "pause": cmd_pause, "resume": cmd_resume, "delete": cmd_delete}
    funcs[cmd](args)


if __name__ == "__main__":
    main()
