# ============================================================================
# Auto-Sync Service (PowerShell)
# ============================================================================
# Automatic database synchronization at regular intervals
# ============================================================================

param(
    [int]$IntervalMinutes = 5,
    [string]$Mode = "incremental"
)

Write-Host ""
Write-Host "============================================================================" -ForegroundColor Cyan
Write-Host "  RAD AI - Auto-Sync Service" -ForegroundColor Cyan
Write-Host "============================================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Configuration:" -ForegroundColor Yellow
Write-Host "  Mode: $Mode"
Write-Host "  Interval: $IntervalMinutes minutes"
Write-Host ""
Write-Host "Press Ctrl+C to stop at any time" -ForegroundColor Yellow
Write-Host ""

# Check .env file
if (-not (Test-Path ".env")) {
    Write-Host "ERROR: .env file not found" -ForegroundColor Red
    exit 1
}

# Activate virtual environment
$venvActivated = $false
$venvPaths = @("venv\Scripts\Activate.ps1", ".venv\Scripts\Activate.ps1", "env\Scripts\Activate.ps1")

foreach ($venvPath in $venvPaths) {
    if (Test-Path $venvPath) {
        & $venvPath
        $venvActivated = $true
        break
    }
}

Write-Host "============================================================================" -ForegroundColor Cyan
Write-Host "  Auto-Sync Started" -ForegroundColor Green
Write-Host "============================================================================" -ForegroundColor Cyan
Write-Host ""

$syncCount = 0

while ($true) {
    $syncCount++
    $timestamp = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    
    Write-Host ""
    Write-Host "[$timestamp] 🔄 Sync #$syncCount starting..." -ForegroundColor Cyan
    Write-Host ""
    
    # Run sync
    python manage.py sync_from_production --mode $Mode
    
    if ($LASTEXITCODE -eq 0) {
        Write-Host ""
        Write-Host "[$timestamp] ✅ Sync completed successfully!" -ForegroundColor Green
    } else {
        Write-Host ""
        Write-Host "[$timestamp] ⚠️  Sync failed! Will retry in $IntervalMinutes minutes..." -ForegroundColor Yellow
    }
    
    Write-Host ""
    Write-Host "Waiting $IntervalMinutes minutes before next sync..." -ForegroundColor Gray
    Write-Host "Press Ctrl+C to stop" -ForegroundColor Gray
    Write-Host ""
    
    Start-Sleep -Seconds ($IntervalMinutes * 60)
}
