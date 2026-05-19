# Resume Phase 3.5 (repair × N rounds) without invoking Phase 2 generate.py.
# Run from C:\work\aimodel.
#
# Why a dedicated script: run_pipeline.ps1 always calls generate.py first.
# generate.py is resumable but still walks the brief plan, which is wasteful
# when we only want repair + revalidate + rejudge. This script calls each
# sub-script directly.

$ErrorActionPreference = "Stop"

# Fall back to user-scope env var if not set in this session.
if (-not $env:GOOGLE_API_KEY) {
    $persisted = [System.Environment]::GetEnvironmentVariable('GOOGLE_API_KEY','User')
    if ($persisted) { $env:GOOGLE_API_KEY = $persisted }
}
if (-not $env:GOOGLE_API_KEY) {
    Write-Host "ERROR: set GOOGLE_API_KEY first" -ForegroundColor Red
    exit 1
}

. .\.venv\Scripts\Activate.ps1

$repairConcurrency = if ($env:REPAIR_CONCURRENCY) { [int]$env:REPAIR_CONCURRENCY } else { 2 }
$valConcurrency    = if ($env:VAL_CONCURRENCY)    { [int]$env:VAL_CONCURRENCY }    else { 6 }
$judgeConcurrency  = if ($env:JUDGE_CONCURRENCY)  { [int]$env:JUDGE_CONCURRENCY }  else { 2 }
$judgeThreshold    = if ($env:JUDGE_THRESHOLD)    { [int]$env:JUDGE_THRESHOLD }    else { 7 }
$maxRepairRounds   = if ($env:MAX_REPAIR_ROUNDS)  { [int]$env:MAX_REPAIR_ROUNDS }  else { 2 }

function Count-Samples($dir) {
    if (-not (Test-Path $dir)) { return 0 }
    return (Get-ChildItem -Path $dir -Recurse -Filter '*.json' -EA SilentlyContinue |
            Where-Object { $_.Name -notlike '*.validation.json' }).Count
}

for ($round = 1; $round -le $maxRepairRounds; $round++) {
    $rejectedNow = Count-Samples 'data\rejected'
    if ($rejectedNow -eq 0) {
        Write-Host "no rejected samples remain; skipping rounds $round..$maxRepairRounds"
        break
    }
    Write-Host ""
    Write-Host "=== Phase 3.5 round $round/$maxRepairRounds : repairing $rejectedNow rejected (concurrency=$repairConcurrency) ==="
    python generators\repair.py --input data\rejected --max-attempts $maxRepairRounds `
        --concurrency $repairConcurrency

    Write-Host ""
    Write-Host "    re-validating repaired samples (Stage-1)"
    python validators\validate.py --input data\rejected --output data\validated --rejected data\rejected `
        --concurrency $valConcurrency --screenshots --screenshots-dir renders

    Write-Host ""
    Write-Host "    re-judging newly-promoted samples (Stage-2)"
    python validators\visual_judge.py --input data\validated --provider gemini `
        --threshold $judgeThreshold --concurrency $judgeConcurrency

    $validatedNow = Count-Samples 'data\validated'
    $rejectedNow  = Count-Samples 'data\rejected'
    Write-Host "    after round $round : validated=$validatedNow  rejected=$rejectedNow"
}

Write-Host ""
Write-Host "=== Phase 3.5 done ==="
$validatedFinal = Count-Samples 'data\validated'
$rejectedFinal  = Count-Samples 'data\rejected'
Write-Host "  validated (kept):   $validatedFinal"
Write-Host "  rejected (dropped): $rejectedFinal"
$pctKept = if ($validatedFinal + $rejectedFinal -gt 0) { [Math]::Round(100.0 * $validatedFinal / ($validatedFinal + $rejectedFinal), 1) } else { 0 }
Write-Host "  yield: $pctKept%"

Write-Host ""
python scripts\cost_report.py --plan-size $($validatedFinal + $rejectedFinal)
