# Smoke test: run Stage-1 validator on three hand-crafted samples and verify
# that #1 is reported VALID and #2 + #3 are reported INVALID with the
# expected violation kinds. No API calls, no money.
#
# Idempotent: copies the source samples into a working dir before running, so
# the validator's move-semantics doesn't consume the originals.
#
# Run from C:\ibe\aimodel:  .\scripts\smoketest.ps1

$ErrorActionPreference = "Stop"

. .\.venv\Scripts\Activate.ps1

# Set up a fresh working directory (samples here are CONSUMED by the move).
$work = 'samples_handcrafted\_run'
Remove-Item -Recurse -Force $work -ErrorAction SilentlyContinue
New-Item -ItemType Directory "$work\input" | Out-Null
New-Item -ItemType Directory "$work\validated" | Out-Null
New-Item -ItemType Directory "$work\rejected" | Out-Null
Copy-Item 'samples_handcrafted\01_pass_business_card.json'         "$work\input\"
Copy-Item 'samples_handcrafted\02_fail_text_collision_flyer.json'  "$work\input\"
Copy-Item 'samples_handcrafted\03_fail_outside_circle_sticker.json' "$work\input\"

python validators\validate.py `
    --input "$work\input" `
    --output "$work\validated" `
    --rejected "$work\rejected" `
    --concurrency 2 `
    --screenshots --screenshots-dir "$work\renders"

Write-Host ""
Write-Host "=== smoke results ==="

function Expect-Outcome($file, $bucket, $shouldKind) {
    $path = "$work\$bucket\$file"
    if (-not (Test-Path $path)) {
        Write-Host "  FAIL: expected $file in $bucket\, not found" -ForegroundColor Red
        return $false
    }
    if ($shouldKind) {
        $report = "$work\$bucket\$($file -replace '\.json$', '.validation.json')"
        if (-not (Test-Path $report)) {
            Write-Host "  FAIL: missing validation report for $file" -ForegroundColor Red
            return $false
        }
        $txt = Get-Content $report -Raw
        if ($txt -notmatch [regex]::Escape($shouldKind)) {
            Write-Host "  FAIL: $file did not report violation '$shouldKind'" -ForegroundColor Red
            Write-Host "  report excerpt: $($txt.Substring(0, [Math]::Min(400, $txt.Length)))"
            return $false
        }
    }
    $suffix = ''
    if ($shouldKind) { $suffix = " ($shouldKind)" }
    Write-Host "  OK: $file -> $bucket$suffix" -ForegroundColor Green
    return $true
}

$ok = $true
$ok = $ok -and (Expect-Outcome "01_pass_business_card.json"        "validated" $null)
$ok = $ok -and (Expect-Outcome "02_fail_text_collision_flyer.json" "rejected"  "text_collision")
$ok = $ok -and (Expect-Outcome "03_fail_outside_circle_sticker.json" "rejected" "outside_circle_text")

Write-Host ""
if ($ok) {
    Write-Host "ALL SMOKE TESTS PASSED" -ForegroundColor Green
    exit 0
} else {
    Write-Host "SMOKE TESTS FAILED" -ForegroundColor Red
    exit 1
}
