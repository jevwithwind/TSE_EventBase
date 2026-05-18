# Classifier full run — launches Python as a BACKGROUND PROCESS.
#
# The Python process runs independently of this PowerShell window.
# You can close this window after launch without killing the classifier.
#
# Monitor progress:    type classification_status.json
# Check if running:    Get-Process python | Where-Object {$_.StartTime -gt (Get-Date).AddDays(-3)}
# View live log:       Get-Content logs\classifier_resume.log -Tail 20 -Wait
# Resume after crash:  just re-run this script

$ErrorActionPreference = "Stop"

# Paths
$DB         = "E:\TSE_EventBase\data\tse_eventbase.db"
$WORK_DIR   = "E:\TSE_EventBase\classifier_v2"
$LOG_DIR    = "$WORK_DIR\logs"
$TIMESTAMP  = Get-Date -Format "yyyyMMdd_HHmmss"
$LOG_FILE   = "$LOG_DIR\classifier_run_$TIMESTAMP.log"
$STATUS     = "$WORK_DIR\classification_status.json"

New-Item -ItemType Directory -Force -Path $LOG_DIR | Out-Null

# Sanity checks
if (-not (Test-Path $DB)) {
    Write-Host "FATAL: DB not found at $DB" -ForegroundColor Red; exit 1
}
if (-not (Test-Path "$WORK_DIR\classifier.py")) {
    Write-Host "FATAL: classifier.py not found in $WORK_DIR" -ForegroundColor Red; exit 1
}

# Pre-flight: count remaining events
$countScript = @"
import sqlite3
c = sqlite3.connect(r'$DB')
scope = "event_type IN ('earnings','forecast_revision','dividend','buyback','ma','tender_offer')"
n = c.execute(f"SELECT COUNT(*) FROM events WHERE {scope} AND classified_at IS NULL AND classification_failed_at IS NULL").fetchone()[0]
print(n)
"@
$remaining = python -c $countScript 2>$null
Write-Host ""
Write-Host "=== Classifier Launch ===" -ForegroundColor Cyan
Write-Host "  DB:             $DB"
Write-Host "  Log:            $LOG_FILE"
Write-Host "  Status file:    $STATUS"
Write-Host "  Remaining:      $remaining events"
Write-Host ""

if ([int]$remaining -eq 0) {
    Write-Host "Nothing to classify. All in-scope events done or failed." -ForegroundColor Green
    exit 0
}

# Reset transient failures before resuming
$resetScript = @"
import sqlite3
c = sqlite3.connect(r'$DB')
n = c.execute("""
    UPDATE events
    SET classification_failed_at = NULL, classification_error = NULL
    WHERE classification_failed_at IS NOT NULL
      AND (classification_error LIKE '%timed out%'
           OR classification_error LIKE '%Connection error%'
           OR classification_error LIKE '%json_decode_error%')
""").rowcount
c.commit()
print(n)
"@
$resetCount = python -c $resetScript 2>$null
if ([int]$resetCount -gt 0) {
    Write-Host "  Reset $resetCount transient failures for retry" -ForegroundColor Yellow
    $remaining = python -c $countScript 2>$null
    Write-Host "  Updated remaining: $remaining events" -ForegroundColor Yellow
    Write-Host ""
}

# Build Python command arguments
$pyArgs = @(
    "$WORK_DIR\classifier.py",
    "--db", $DB,
    "--filter", "event_type IN ('earnings','forecast_revision','dividend','buyback','ma','tender_offer')",
    "--batch-size", "20",
    "--concurrency", "4",
    "--status-file", $STATUS
)

# Launch as BACKGROUND process (survives window closure)
Write-Host "Launching classifier as background process ..." -ForegroundColor Green
$proc = Start-Process python `
    -ArgumentList $pyArgs `
    -WorkingDirectory $WORK_DIR `
    -RedirectStandardOutput $LOG_FILE `
    -RedirectStandardError "$LOG_DIR\classifier_stderr_$TIMESTAMP.log" `
    -PassThru `
    -WindowStyle Hidden

Write-Host ""
Write-Host "=== Classifier is running ===" -ForegroundColor Green
Write-Host "  PID:            $($proc.Id)"
Write-Host "  Started:        $(Get-Date)"
Write-Host ""
Write-Host "  You can safely close this window now." -ForegroundColor Yellow
Write-Host ""
Write-Host "  Monitor progress (from any terminal):" -ForegroundColor Cyan
Write-Host "    type $STATUS"
Write-Host ""
Write-Host "  View live log:" -ForegroundColor Cyan
Write-Host "    Get-Content $LOG_FILE -Tail 20 -Wait"
Write-Host ""
Write-Host "  Check if still running:" -ForegroundColor Cyan
Write-Host "    Get-Process -Id $($proc.Id) -ErrorAction SilentlyContinue"
Write-Host ""
Write-Host "  Resume if it crashes:" -ForegroundColor Cyan
Write-Host "    Just re-run this script. Resume logic skips completed events."
Write-Host ""