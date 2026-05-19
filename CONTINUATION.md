# CONTINUATION — current state + how to pick up on another machine

Last updated: 2026-05-15 (during Claude session that switched the base model
from Qwen3.6-27B → Qwen2.5-Coder-7B-Instruct).

---

## TL;DR — where we are

- **Dataset is ready** on HF: [`iberescu2201/aimodeltrain-1k-v1`](https://huggingface.co/datasets/iberescu2201/aimodeltrain-1k-v1) — 788 validated samples, train/val/test split 706/41/41, OpenAI-messages format
- **Training is in flight** on HF Jobs (or has just landed): job `6a0c1a50a5e509f1a8416a04`. Base = `Qwen/Qwen2.5-Coder-7B-Instruct`, hardware = `a10g-large`
- **After training succeeds, two HF model repos will exist**:
  - Adapter: `iberescu2201/aimodeltrain-qwen2.5coder-7b-lora-1k-v1`
  - **Merged model** (base+LoRA, ready for one-command deploy): `iberescu2201/aimodeltrain-qwen2.5coder-7b-merged-1k-v1`
- **Total Gemini spend so far**: ~$200 to produce the dataset. **HF spend so far**: ~$5 across attempted training + previous Space/Endpoint experiments. Inference is paused/deleted — $0/h right now.

---

## Setting up on a new machine

```powershell
# 1. Prerequisites (Windows + PowerShell)
winget install Python.Python.3.12 --scope user --silent
Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned -Force

# 2. Clone + venv
git clone https://github.com/iberescu/aimodeltrain.git
cd aimodeltrain
py -3 -m venv .venv
. .\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip wheel setuptools
python -m pip install -r requirements.txt
python -m pip install truststore                # fixes HF SSL on Windows
python -m playwright install chromium           # for the validator

# 3. Persist secrets (user-scope). The same token works on any machine.
[System.Environment]::SetEnvironmentVariable('GOOGLE_API_KEY',          '<your gemini key>', 'User')
[System.Environment]::SetEnvironmentVariable('HUGGINGFACE_HUB_TOKEN',   '<your hf write token>', 'User')

# 4. Create the gitignored .env so playground.html / build scripts pick up the keys
@"
HUGGINGFACE_HUB_TOKEN=<your hf write token>
GOOGLE_API_KEY=<your gemini key>
"@ | Out-File -Encoding utf8 -NoNewline .env

# 5. Authenticate the hf CLI (one-time, stored in C:\Users\<you>\.cache\huggingface\token)
hf auth login --token <your hf write token>
```

Everything below assumes you've activated the venv (`. .\.venv\Scripts\Activate.ps1`).

---

## What's in git vs not

**Tracked (you get these via `git pull`):**
- All scripts under `scripts/`, `generators/`, `validators/`, `training/`, `space/`
- `configs/` (design specs, font catalog, **font_metrics.json measurement output**, pricing)
- `samples_handcrafted/` (smoke-test fixtures)
- `requirements.txt`, `.gitignore`, README.md, this file

**Gitignored (rebuild on the new machine):**
- `.env` — contains tokens, recreate per step 4 above
- `.venv/`, `fonts/` — fetched/built via setup
- `data/` — gitignored. The 788 generated samples live ONLY on the machine that produced them and on HF Hub as the dataset. To get them back:
  - For training: pull from HF (`load_dataset('iberescu2201/aimodeltrain-1k-v1')`)
  - For local browsing: re-run the pipeline (~$200) OR scp from the old machine
- `renders/` — PNG screenshots from the validator. Re-create by running `validators/validate.py` on the samples
- `viewer.html`, `playground.html` — generated; rebuild with `python scripts/build_viewer.py` and `python scripts/build_playground.py`
- `logs/` — local runtime logs

---

## Monitoring the in-flight training

```powershell
# Check job state
python scripts\_wait_job.py 6a0c1a50a5e509f1a8416a04

# Or directly via API
python -c "import os,truststore,requests,json; truststore.inject_into_ssl(); print(json.dumps(requests.get('https://huggingface.co/api/jobs/iberescu2201/6a0c1a50a5e509f1a8416a04', headers={'Authorization':'Bearer ' + os.environ['HUGGINGFACE_HUB_TOKEN']}, timeout=30).json()['status'], indent=2))"

# Or in browser
# https://huggingface.co/jobs/iberescu2201/6a0c1a50a5e509f1a8416a04
```

**Expected outcomes:**
- `stage=RUNNING` → training in progress (~30 min total)
- `stage=COMPLETED` → adapter pushed; the merge step ran; check `iberescu2201/aimodeltrain-qwen2.5coder-7b-merged-1k-v1` for the merged model
- `stage=ERROR` → fetch full logs:
  ```powershell
  python -c "import os,truststore,requests; truststore.inject_into_ssl(); open('logs/job.log','w',encoding='utf-8').write(requests.get('https://huggingface.co/api/jobs/iberescu2201/6a0c1a50a5e509f1a8416a04/logs', headers={'Authorization':'Bearer ' + os.environ['HUGGINGFACE_HUB_TOKEN']}, timeout=60).text)"
  ```
  Then `grep -oE '"data":"[^"]+"' logs/job.log | sed 's/^"data":"//;s/"$//' | tail -40`

---

## After training succeeds — deploy

```powershell
python scripts\deploy_endpoint.py up    # creates Inference Endpoint on a10g, ~$1.30/hr
python scripts\deploy_endpoint.py status # get the URL
```

Then update `.env` with the new `ENDPOINT_URL` and rebuild the playground:

```powershell
# Update ENDPOINT_URL in .env to https://<endpoint>.endpoints.huggingface.cloud/v1/chat/completions
python scripts\build_playground.py
# Open playground.html (double-click)
```

The deploy script targets `iberescu2201/aimodeltrain-qwen2.5coder-7b-merged-1k-v1` and uses **HF's default toolkit (no custom_image)** — Qwen2.5 is supported natively, so this Just Works.

When done testing:

```powershell
python scripts\deploy_endpoint.py pause  # $0/h, config preserved
python scripts\deploy_endpoint.py resume # bring back online
```

---

## The story of this session (so you don't repeat work)

1. **Got the pilot 50 dryrun training working** end-to-end on HF Jobs against Qwen/Qwen3.6-27B. The dryrun adapter is at `iberescu2201/aimodeltrain-qwen-lora-dryrun` (LoRA on top of 27B, useful as a smoke-test target only — barely trained).

2. **Ran the 1k Gemini generation pipeline.** Phase 2 (generate) + 3.1 (validate) + 3.2 (judge) ran fine. Phase 3.5 (repair) hung repeatedly — google-genai SDK doesn't reliably honor `asyncio.wait_for` cancellation (README lesson #1). Each hang required manual kill. Eventually stopped at:
   - **788 validated** samples (passed Stage-1 mechanical + most Stage-2 judge)
   - 190 rejected
   - **$200.23 total Gemini spend**

3. **Built two viewers**:
   - `viewer.html` — browse all generated samples (renders, briefs, judge scores, repair history)
   - `playground.html` — talk to a deployed inference endpoint with a form-driven brief

4. **Tried serving Qwen3.6-27B on HF Inference Endpoints** — failed. Its `model_type=qwen3_5` is too new for TGI's model registry, and HF's default toolkit also doesn't know it (falls back to BLOOM-560m). Tried `bitsandbytes-nf4` quant (fails: "4bit not supported for AutoModel"), bf16 (fails: "Unsupported model type qwen3_5"), `vllm/vllm-openai` via Docker Space (worked, 14-min boot, served correctly — but the custom_image path is brittle and the user wanted to stop using Docker).

5. **Briefly tried** adding a hard "position:absolute only" rule to BASE_SYSTEM and a corresponding inliner (`scripts/add_inline_positions.py`) that renders each HTML in Playwright, snapshots `getBoundingClientRect()` of every body-level child, and bakes coordinates into inline `style=""`. 713/788 samples revalidated cleanly. **Then reverted**: user decided originals (with `<style>` blocks + flex/normal positioning) are the better training set. The inliner script is still in the repo for future use.

6. **Switched base model to Qwen2.5-Coder-7B-Instruct.** Reasons documented in `training/job_command.sh`'s comments. Switched HF Job hardware to `a10g-large`. Added a `--merge-repo` flag to `train_lora.py` so a fully-merged (base+LoRA) model gets pushed too — that means deployment doesn't need any LoRA-mounting trickery and can use HF's default Inference Endpoints toolkit straightforwardly.

7. **First training attempt** (`6a0c1958e7940de6ee6cf82b`) OOM'd at step 0: Qwen2.5's 152K vocab × batch=2 × seq=4096 → cross-entropy logits ~5GB which didn't fit on A10G 24GB. Fixed: dropped batch_size to 1, kept effective batch via `--grad-accum 16`, set `PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True` to reduce fragmentation.

8. **Second training attempt** (`6a0c1a50a5e509f1a8416a04`) — running at time of this writing. Expected ~25–40 min for training + merge + push. After this completes, the merged model is the thing the playground talks to.

---

## Key file map

```
generators/system_prompts.py   BASE_SYSTEM (the system prompt) — currently
                                the original from initial commit. Keep it
                                this way as long as we train on data/validated
                                originals.
generators/generate.py         Phase 2 — calls Gemini with the brief.
generators/repair.py           Phase 3.5 — feeds violations back. KNOWN ISSUE:
                                SDK hangs at some point in long batches; need
                                manual kill to unblock the orchestrator. Lower
                                concurrency helps but doesn't fully fix.
generators/briefs.py           12-company brand roster + brief sampler.

validators/validate.py         Stage-1 (mechanical) — Playwright + DOM bbox + rules.
validators/visual_judge.py     Stage-2 (visual) — multimodal Gemini scoring.
validators/checks.py           The pure-Python rule predicates.
validators/dom_extract.js      Injected into the rendered page.

training/job_command.sh        Bootstrap for the HF Job container — copy code,
                                set HF caches to /tmp, run train_lora.py.
training/train_lora.py         The actual training entry point — LoRA via TRL
                                SFTTrainer, optional --merge-repo for merged
                                model push.
training/requirements.txt      Heavier deps installed inside the HF Job only.

scripts/run_pipeline.ps1       Run the full pipeline: gen → validate → judge → repair × 2.
scripts/run_phase35.ps1        Resume from rejected/ — repair + revalidate + rejudge only.
scripts/build_dataset.py       Pack data/validated into OpenAI-messages JSONL + push to HF.
scripts/build_viewer.py        Generate viewer.html (browse samples).
scripts/build_playground.py    Generate playground.html (test endpoint).
scripts/add_inline_positions.py  Rewrite HTML so body children carry inline absolute positioning.
                                  Run on the OLD originals if we ever decide to switch.
scripts/deploy_endpoint.py     Create/update/pause/resume the Inference Endpoint.
scripts/deploy_space.py        Create/push/pause the Docker Space. (Space currently deleted.)
scripts/cost_report.py         Reads logs/api_calls.jsonl, prints per-phase spend.
scripts/_wait_job.py           Poll an HF Job to terminal state; verify output repos.

space/Dockerfile               vLLM container the Space used to serve from. Kept as
                                a fallback for if we ever need to serve a base
                                model HF's toolkit can't load.
space/entrypoint.sh            Boot script with all the Space-environment fixes
                                (HOME=/tmp, USER=spaceuser, etc).

configs/design_types.json      Canvas sizes, thresholds, generation weights per type.
configs/design_spec.md         Human-readable design contract.
configs/font_catalog.json      The 20 curated Google Fonts.
configs/font_metrics.json      Measured per-(family, weight) metrics. Committed.
configs/pricing.json           Gemini 3.1 Pro pricing for the cost report.
```

---

## HF resources directory

| Kind | URL | What it is |
|---|---|---|
| Dataset | https://huggingface.co/datasets/iberescu2201/aimodeltrain-1k-v1 | 788 training samples |
| Dataset (dryrun) | https://huggingface.co/datasets/iberescu2201/aimodeltrain-50pilot-dryrun | 47 samples from pilot 50; used for the dryrun training |
| Code repo | https://huggingface.co/iberescu2201/aimodeltrain-code | `train_lora.py` + `requirements.txt` + `job_command.sh` mounted into HF Jobs |
| Dryrun adapter | https://huggingface.co/iberescu2201/aimodeltrain-qwen-lora-dryrun | LoRA on Qwen3.6-27B, 11 training steps. Smoke-test target only. |
| **1k adapter** | https://huggingface.co/iberescu2201/aimodeltrain-qwen2.5coder-7b-lora-1k-v1 | LoRA on Qwen2.5-Coder-7B. Created on training success. |
| **1k merged** | https://huggingface.co/iberescu2201/aimodeltrain-qwen2.5coder-7b-merged-1k-v1 | Base+LoRA merged. **Deploy target.** Created on training success. |

---

## If you want to start from scratch on the new machine

To regenerate data/validated/ (e.g., for re-running the inliner or building a new dataset variant), you'd need to:

1. Re-run `scripts/run_pipeline.ps1` with `$env:PLAN_SIZE = 1000` (~$170, ~24h with the SDK hangs).

OR

2. Pull the dataset from HF (`load_dataset('iberescu2201/aimodeltrain-1k-v1')`) and reconstruct local files from the JSONL records — but the validator's mechanical check needs raw HTML files on disk, so this only helps for training.

If you only need to browse / test the model, **don't bother** with data/validated — viewer + playground rebuild fine without it (well, viewer doesn't show samples without data/, but playground talks to the live endpoint).

---

## Resuming the training poll (if interrupted)

The `b9ssik2cq` / `bamnuuz72` background polls were process-local; they don't survive a terminal restart. To resume polling on the new machine:

```powershell
python scripts\_wait_job.py 6a0c1a50a5e509f1a8416a04
```

This will block until the job hits a terminal state, then print the repo file lists. Safe to run any time.
