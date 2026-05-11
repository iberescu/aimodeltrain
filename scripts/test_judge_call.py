"""One-shot test of the Gemini visual judge call against a real sample."""
import asyncio
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from validators.visual_judge import judge_gemini, build_brief_summary  # noqa: E402

# Pick the first validated sample and its screenshot
validated_samples = list((REPO_ROOT / "data" / "validated").rglob("*.json"))
validated_samples = [p for p in validated_samples if not p.name.endswith(".validation.json")]
if not validated_samples:
    print("no validated sample to test against")
    sys.exit(1)

sample_path = validated_samples[0]
sample = json.loads(sample_path.read_text(encoding="utf-8"))
sample_id = sample["id"]
design_type = sample["design_type"]
png_path = REPO_ROOT / "renders" / design_type / f"{sample_id}.png"
if not png_path.exists():
    print(f"no screenshot for {sample_id} at {png_path}")
    sys.exit(1)

print(f"testing judge on: {sample_id} ({design_type})")
print(f"  png: {png_path}  size={png_path.stat().st_size} bytes")

api_key = os.environ.get("GOOGLE_API_KEY")
if not api_key:
    from os import environ
    api_key = __import__("subprocess").run(
        ["powershell", "-NoProfile", "-Command",
         "[System.Environment]::GetEnvironmentVariable('GOOGLE_API_KEY','User')"],
        capture_output=True, text=True
    ).stdout.strip()
if not api_key:
    print("no GOOGLE_API_KEY")
    sys.exit(1)

from google import genai  # noqa: E402
client = genai.Client(api_key=api_key)
png_bytes = png_path.read_bytes()
brief_summary = build_brief_summary(sample)

async def main():
    try:
        result = await judge_gemini(client, png_bytes, brief_summary)
        print("SUCCESS — judge returned:")
        print(json.dumps(result, indent=2))
    except Exception as e:
        print(f"FAILED: {type(e).__name__}: {e}")
        import traceback; traceback.print_exc()

asyncio.run(main())
