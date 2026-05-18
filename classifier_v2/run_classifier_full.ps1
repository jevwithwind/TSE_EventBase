# Classifier full run — background process, survives window closure.
#
# Monitor:  type classification_status.json
# Resume:   re-run this script (resumes from last classified event)
# Stop:     Get-Process python -Id <PID> | Stop-Process

$WORK_DIR  = "E:\TSE_EventBase\classifier_v2"
$DB        = "E:\TSE_EventBase\data\tse_eventbase.db"
$LOG_DIR   = "$WORK_DIR\logs"
$TIMESTAMP = Get-Date -Format "yyyyMMdd_HHmmss"
$LOG_FILE  = "$LOG_DIR\classifier_run_$TIMESTAMP.log"
$ERR_FILE  = "$LOG_DIR\classifier_stderr_$TIMESTAMP.log"
$STATUS    = "$WORK_DIR\classification_status.json"

New-Item -ItemType Directory -Force -Path $LOG_DIR | Out-Null

$pyArgs = @(
    "$WORK_DIR\classifier.py",
    "--db", $DB,
    "--in-scope-only",
    "--batch-size", "20",
    "--timeout", "90",
    "--concurrency", "4",
    "--status-file", $STATUS,
    "--reset-failures"
)

Write-Host ""
Write-Host "Classifier: batch_size=20, concurrency=4, timeout=90s" -ForegroundColor Green
Write-Host "In-scope events: ~327,500 | Est. elapsed: ~68 hours" -ForegroundColor Cyan
Write-Host ""

$proc = Start-Process python `
    -ArgumentList $pyArgs `
    -WorkingDirectory $WORK_DIR `
    -RedirectStandardOutput $LOG_FILE `
    -RedirectStandardError $ERR_FILE `
    -PassThru `
    -WindowStyle Hidden

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
