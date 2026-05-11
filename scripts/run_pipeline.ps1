# Orchestrates the test batch end-to-end:
#   Phase 2:    generate N samples with Gemini 3.1 Pro
#   Phase 3.1:  Stage-1 mechanical validation (DOM bbox, collisions, B2B roles, contrast)
#   Phase 3.2:  Stage-2 visual judge (multimodal scoring; demotes low scorers)
#   Phase 3.5:  Repair loop  -- feed validation+judge feedback back to Gemini,
#               regenerate, then RE-VALIDATE + RE-JUDGE. Up to $maxRepairRounds rounds.
#
# Run from C:\ibe\aimodel:  .\scripts\run_pipeline.ps1

$ErrorActionPreference = "Stop"

# Fall back to the user-scope env var if it's not set in this session.
# (Windows: a process started before the var was persisted won't inherit it
# via PATH-style propagation; reading it from the registry fixes that.)
if (-not $env:GOOGLE_API_KEY) {
    $persisted = [System.Environment]::GetEnvironmentVariable('GOOGLE_API_KEY','User')
    if ($persisted) { $env:GOOGLE_API_KEY = $persisted }
}

if (-not $env:GOOGLE_API_KEY) {
    Write-Host "ERROR: set `$env:GOOGLE_API_KEY first" -ForegroundColor Red
    exit 1
}

. .\.venv\Scripts\Activate.ps1

$planSize         = if ($env:PLAN_SIZE)         { [int]$env:PLAN_SIZE }         else { 10000 }
$genConcurrency   = if ($env:GEN_CONCURRENCY)   { [int]$env:GEN_CONCURRENCY }   else { 6 }
$valConcurrency   = if ($env:VAL_CONCURRENCY)   { [int]$env:VAL_CONCURRENCY }   else { 6 }
$judgeConcurrency = if ($env:JUDGE_CONCURRENCY) { [int]$env:JUDGE_CONCURRENCY } else { 4 }
$judgeThreshold   = if ($env:JUDGE_THRESHOLD)   { [int]$env:JUDGE_THRESHOLD }   else { 7 }
$judgeProvider    = if ($env:JUDGE_PROVIDER)    { $env:JUDGE_PROVIDER }         else { "gemini" }
$maxRepairRounds  = if ($env:MAX_REPAIR_ROUNDS) { [int]$env:MAX_REPAIR_ROUNDS } else { 2 }
$repairConcurrency = if ($env:REPAIR_CONCURRENCY) { [int]$env:REPAIR_CONCURRENCY } else { 4 }

function Count-Samples($dir) {
    if (-not (Test-Path $dir)) { return 0 }
    return (Get-ChildItem -Path $dir -Recurse -Filter '*.json' -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -notlike '*.validation.json' }).Count
}

Write-Host "=== Phase 2: generate $planSize samples with Gemini 3.1 Pro ==="
python generators\generate.py --plan-size $planSize --concurrency $genConcurrency

Write-Host ""
Write-Host "=== Phase 3.1: Stage-1 validation (mechanical) ==="
python validators\validate.py --input data\raw --output data\validated --rejected data\rejected `
    --concurrency $valConcurrency --screenshots --screenshots-dir renders

Write-Host ""
Write-Host "=== Phase 3.2: Stage-2 visual judge ($judgeProvider, threshold=$judgeThreshold) ==="
python validators\visual_judge.py --input data\validated --provider $judgeProvider `
    --threshold $judgeThreshold --concurrency $judgeConcurrency

$validated0 = Count-Samples 'data\validated'
$rejected0  = Count-Samples 'data\rejected'
Write-Host ""
Write-Host "  after first pass: validated=$validated0  rejected=$rejected0"

# ----- Repair loop -----------------------------------------------------------
for ($round = 1; $round -le $maxRepairRounds; $round++) {
    $rejectedNow = Count-Samples 'data\rejected'
    if ($rejectedNow -eq 0) {
        Write-Host ""
        Write-Host "no rejected samples remain; skipping repair rounds $round..$maxRepairRounds"
        break
    }

    Write-Host ""
    Write-Host "=== Phase 3.5 round $round/$maxRepairRounds : repairing $rejectedNow rejected samples ==="
    python generators\repair.py --input data\rejected --max-attempts $maxRepairRounds `
        --concurrency $repairConcurrency

    Write-Host ""
    Write-Host "    re-validating repaired samples (Stage-1)"
    # NOTE: validator now uses move-semantics. Samples that pass Stage-1 will
    # be moved from data\rejected\ -> data\validated\. Failing ones stay put.
    python validators\validate.py --input data\rejected --output data\validated --rejected data\rejected `
        --concurrency $valConcurrency --screenshots --screenshots-dir renders

    Write-Host ""
    Write-Host "    re-judging newly-promoted samples (Stage-2)"
    python validators\visual_judge.py --input data\validated --provider $judgeProvider `
        --threshold $judgeThreshold --concurrency $judgeConcurrency

    $validatedNow = Count-Samples 'data\validated'
    $rejectedNow  = Count-Samples 'data\rejected'
    Write-Host "    after round $round : validated=$validatedNow  rejected=$rejectedNow"
}

# ----- Final summary ---------------------------------------------------------
Write-Host ""
Write-Host "=== final summary ==="
$validatedFinal = Count-Samples 'data\validated'
$rejectedFinal  = Count-Samples 'data\rejected'
Write-Host "  validated (kept):   $validatedFinal"
Write-Host "  rejected (dropped): $rejectedFinal"
$pctKept = if ($planSize -gt 0) { [Math]::Round(100.0 * $validatedFinal / $planSize, 1) } else { 0 }
Write-Host "  yield: $pctKept% of the plan"

Write-Host ""
python scripts\cost_report.py --plan-size $planSize
