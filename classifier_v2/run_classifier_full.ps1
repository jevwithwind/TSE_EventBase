# Classifier full run — background process, survives window closure.
#
# Three-stage pipeline:
#   1. stage0_prices.py — overnight returns → data-driven sentiment
#   2. pre-filter       — auto-classify neutral earnings (no API)
#   3. classifier.py    — LLM headline classification (remaining events)
#
# Monitor:  type classification_status.json
# Resume:   re-run this script (each stage skips events with classified_at set)
# Stop:     Get-Process python | Stop-Process

$WORK_DIR  = "E:\TSE_EventBase\classifier_v2"
$DB        = "E:\TSE_EventBase\data\tse_eventbase.db"
$LOG_DIR   = "$WORK_DIR\logs"
$TIMESTAMP = Get-Date -Format "yyyyMMdd_HHmmss"
$STATUS    = "$WORK_DIR\classification_status.json"

New-Item -ItemType Directory -Force -Path $LOG_DIR | Out-Null

# ---- Stage 0: Overnight returns (seconds, no API) ----

Write-Host "=== Stage 0: Overnight Return Classification ===" -ForegroundColor Cyan
$LOG_STAGE0 = "$LOG_DIR\stage0_$TIMESTAMP.log"

python "$WORK_DIR\stage0_prices.py" `
    --db $DB `
    --in-scope-only

if ($LASTEXITCODE -ne 0) {
    Write-Host "Stage 0 failed with exit code $LASTEXITCODE" -ForegroundColor Red
    exit $LASTEXITCODE
}
Write-Host "Stage 0 complete." -ForegroundColor Green

# ---- Stage 1: Pre-filter + LLM classifier (hours, requires API) ----

Write-Host ""
Write-Host "=== Stage 1: Pre-filter + LLM Classifier ===" -ForegroundColor Cyan
Write-Host "Classifier: batch_size=20, concurrency=4, timeout=90s" -ForegroundColor Green

$LOG_FILE = "$LOG_DIR\classifier_run_$TIMESTAMP.log"
$ERR_FILE = "$LOG_DIR\classifier_stderr_$TIMESTAMP.log"

$pyArgs = @(
    "$WORK_DIR\classifier.py",
    "--db", $DB,
    "--in-scope-only",
    "--pre-filter",
    "--batch-size", "20",
    "--timeout", "90",
    "--concurrency", "4",
    "--status-file", $STATUS,
    "--reset-failures"
)

$proc = Start-Process python `
    -ArgumentList $pyArgs `
    -WorkingDirectory $WORK_DIR `
    -RedirectStandardOutput $LOG_FILE `
    -RedirectStandardError $ERR_FILE `
    -PassThru `
    -WindowStyle Hidden

Write-Host ""
Write-Host "=== Running ===" -ForegroundColor Green
Write-Host "  PID:     $($proc.Id)"
Write-Host "  Log:     $LOG_FILE"
Write-Host "  Status:  $STATUS"
Write-Host ""
Write-Host "  You can close this window." -ForegroundColor Yellow
Write-Host ""
Write-Host "  Monitor:   type $STATUS" -ForegroundColor Cyan
Write-Host "  Live log:  Get-Content $LOG_FILE -Tail 20 -Wait" -ForegroundColor Cyan
Write-Host "  Check:     Get-Process -Id $($proc.Id) -ErrorAction SilentlyContinue" -ForegroundColor Cyan
Write-Host ""
