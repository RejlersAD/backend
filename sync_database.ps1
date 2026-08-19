# ============================================================================
# Database Synchronization Script (PowerShell)
# ============================================================================
# This script syncs the local database with production (100% alignment)
# ============================================================================

Write-Host ""
Write-Host "============================================================================" -ForegroundColor Cyan
Write-Host "  RAD AI - Database Synchronization Script" -ForegroundColor Cyan
Write-Host "============================================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "This will sync your local database with production (FULL REPLACEMENT MODE)" -ForegroundColor Yellow
Write-Host ""
Write-Host "WARNING: This will overwrite local data with production data!" -ForegroundColor Red
Write-Host ""

$confirmation = Read-Host "Type 'YES' to continue or 'N' to cancel"
if ($confirmation -ne 'YES') {
    Write-Host "Sync cancelled." -ForegroundColor Yellow
    exit
}

Write-Host ""
Write-Host "[1/4] Checking environment..." -ForegroundColor Green

# Check if .env file exists
if (-not (Test-Path ".env")) {
    Write-Host "ERROR: .env file not found in backend directory" -ForegroundColor Red
    Write-Host "Please create .env file and add PROD_DATABASE_URL" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "Example:" -ForegroundColor Cyan
    Write-Host "  PROD_DATABASE_URL=postgresql://postgres:password@host:port/railway"
    exit 1
}

# Check if manage.py exists
if (-not (Test-Path "manage.py")) {
    Write-Host "ERROR: manage.py not found. Are you in the backend directory?" -ForegroundColor Red
    exit 1
}

Write-Host "[2/4] Activating virtual environment..." -ForegroundColor Green

# Try to activate virtual environment
$venvActivated = $false
$venvPaths = @("venv\Scripts\Activate.ps1", ".venv\Scripts\Activate.ps1", "env\Scripts\Activate.ps1")

foreach ($venvPath in $venvPaths) {
    if (Test-Path $venvPath) {
        & $venvPath
        Write-Host "Virtual environment activated: $venvPath" -ForegroundColor Green
        $venvActivated = $true
        break
    }
}

if (-not $venvActivated) {
    Write-Host "WARNING: No virtual environment found, using system Python" -ForegroundColor Yellow
    Write-Host "Consider creating one with: python -m venv venv" -ForegroundColor Yellow
}

Write-Host "[3/4] Checking Django installation..." -ForegroundColor Green
try {
    python -c "import django" 2>$null
    if ($LASTEXITCODE -ne 0) {
        throw "Django not found"
    }
} catch {
    Write-Host "ERROR: Django not installed. Please run: pip install -r requirements.txt" -ForegroundColor Red
    exit 1
}

Write-Host "[4/4] Starting database synchronization..." -ForegroundColor Green
Write-Host ""
Write-Host "============================================================================" -ForegroundColor Cyan
Write-Host "  Syncing database in FULL mode (100% alignment)" -ForegroundColor Cyan
Write-Host "============================================================================" -ForegroundColor Cyan
Write-Host ""

# Run the sync command
python manage.py sync_from_production --mode full

if ($LASTEXITCODE -ne 0) {
    Write-Host ""
    Write-Host "============================================================================" -ForegroundColor Red
    Write-Host "  SYNC FAILED!" -ForegroundColor Red
    Write-Host "============================================================================" -ForegroundColor Red
    Write-Host ""
    Write-Host "Common issues:" -ForegroundColor Yellow
    Write-Host "  1. PROD_DATABASE_URL not set in .env file"
    Write-Host "  2. Cannot connect to production database (check network/VPN)"
    Write-Host "  3. Database credentials are incorrect"
    Write-Host ""
    exit 1
}

Write-Host ""
Write-Host "============================================================================" -ForegroundColor Green
Write-Host "  SYNC COMPLETED SUCCESSFULLY!" -ForegroundColor Green
Write-Host "============================================================================" -ForegroundColor Green
Write-Host ""
Write-Host "Your local database is now 100% aligned with production." -ForegroundColor Green
Write-Host ""
