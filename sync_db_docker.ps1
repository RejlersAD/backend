# Database Sync: Preprod to Production (PowerShell + Docker)
# This script uses Docker to avoid PostgreSQL version mismatch issues

Write-Host "========================================" -ForegroundColor Cyan
Write-Host "DATABASE SYNC: Preprod to Production" -ForegroundColor Cyan  
Write-Host "========================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Source (Preprod):  tokaido.proxy.rlwy.net:59798"
Write-Host "Target (Production): sakura.proxy.rlwy.net:31281"
Write-Host ""
Write-Host "WARNING: This will OVERWRITE ALL data in production database!" -ForegroundColor Red
Write-Host "         All existing production data will be DELETED!" -ForegroundColor Red
Write-Host ""
Write-Host "Make sure you have:"
Write-Host "  1. Docker Desktop installed and running"
Write-Host "  2. Backed up production database (if needed)"
Write-Host "  3. Verified preprod data is correct"
Write-Host "  4. Notified team members"
Write-Host ""

$confirmation = Read-Host "Type YES in CAPITAL letters to proceed"
if ($confirmation -ne "YES") {
    Write-Host ""
    Write-Host "[CANCELLED] Operation cancelled for safety." -ForegroundColor Yellow
    Write-Host ""
    exit 0
}

# Check Docker
Write-Host ""
Write-Host "[1/4] Checking Docker availability..." -ForegroundColor Yellow
try {
    docker --version | Out-Null
    if ($LASTEXITCODE -ne 0) { throw }
    Write-Host "[OK] Docker found" -ForegroundColor Green
} catch {
    Write-Host "[ERROR] Docker not found or not running!" -ForegroundColor Red
    Write-Host ""
    Write-Host "Please install Docker Desktop:"
    Write-Host "https://www.docker.com/products/docker-desktop/"
    Write-Host ""
    exit 1
}

# Database URLs
$PREPROD_DB_URL = $env:PREPROD_DB_URL
if (-not $PREPROD_DB_URL) {
    $PREPROD_DB_URL = "postgresql://postgres:thAEPEWfKHTGvCwRfaeeichfMNxwdnbD@tokaido.proxy.rlwy.net:59798/railway"
}

$PROD_DB_URL = $env:PROD_DB_URL
if (-not $PROD_DB_URL) {
    $PROD_DB_URL = "postgresql://postgres:iBEjCnCHbjwnnIhyJhoRXGiUtXNHMjpp@sakura.proxy.rlwy.net:31281/railway"
}

# Dump preprod database
Write-Host ""
Write-Host "[2/4] Dumping preprod database using PostgreSQL 18 (via Docker)..." -ForegroundColor Yellow
Write-Host "This may take a few minutes depending on data size..."

$dumpOutput = docker run --rm postgres:18 pg_dump --no-owner --no-acl --clean --if-exists $PREPROD_DB_URL 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "[ERROR] Failed to dump preprod database!" -ForegroundColor Red
    Write-Host ""
    Write-Host $dumpOutput
    Write-Host ""
    Write-Host "Common issues:"
    Write-Host "  1. Connection refused - Check network/VPN/firewall"
    Write-Host "  2. Wrong credentials - Verify password"
    Write-Host "  3. Docker not running - Start Docker Desktop"
    Write-Host ""
    exit 1
}

# Save to file
$dumpOutput | Out-File -FilePath "preprod_backup.sql" -Encoding UTF8

# Verify file was created
if (-not (Test-Path "preprod_backup.sql")) {
    Write-Host "[ERROR] Backup file was not created!" -ForegroundColor Red
    exit 1
}

$fileSize = (Get-Item "preprod_backup.sql").Length
if ($fileSize -lt 1024) {
    Write-Host "[WARNING] Backup file is suspiciously small ($fileSize bytes)" -ForegroundColor Yellow
    $continue = Read-Host "Continue anyway? (yes/no)"
    if ($continue -ne "yes") {
        Write-Host "Operation cancelled."
        exit 1
    }
}

Write-Host "[OK] Preprod database dumped to preprod_backup.sql ($fileSize bytes)" -ForegroundColor Green

# Backup production
Write-Host ""
Write-Host "[3/4] Creating production backup before overwriting..." -ForegroundColor Yellow
$timestamp = Get-Date -Format "yyyyMMdd_HHmmss"
$prodBackup = "production_backup_$timestamp.sql"

Write-Host "Creating safety backup of current production data..."
$backupOutput = docker run --rm postgres:18 pg_dump --no-owner --no-acl $PROD_DB_URL 2>&1
if ($LASTEXITCODE -eq 0) {
    $backupOutput | Out-File -FilePath $prodBackup -Encoding UTF8
    Write-Host "[OK] Production backup saved: $prodBackup" -ForegroundColor Green
} else {
    Write-Host "[WARNING] Could not backup production (might be empty or unreachable)" -ForegroundColor Yellow
}

# Restore to production
Write-Host ""
Write-Host "[4/4] Restoring preprod data to production database (via Docker)..." -ForegroundColor Yellow
Write-Host "This will OVERWRITE production data..."

$restoreOutput = Get-Content "preprod_backup.sql" | docker run --rm -i postgres:18 psql $PROD_DB_URL 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "[ERROR] Failed to restore to production!" -ForegroundColor Red
    Write-Host ""
    Write-Host $restoreOutput
    Write-Host ""
    Write-Host "The backup files are saved:"
    Write-Host "  - preprod_backup.sql (source data)"
    Write-Host "  - $prodBackup (production backup)"
    Write-Host ""
    Write-Host "You can manually restore later using:"
    Write-Host '  Get-Content preprod_backup.sql | docker run --rm -i postgres:18 psql "connection_string"'
    Write-Host ""
    exit 1
}

# Success
Write-Host ""
Write-Host "========================================" -ForegroundColor Green
Write-Host "[SUCCESS] Database synced successfully!" -ForegroundColor Green
Write-Host "========================================" -ForegroundColor Green
Write-Host ""
Write-Host "Preprod data has been copied to production (sakura)"
Write-Host ""
Write-Host "Files created:"
Write-Host "  - preprod_backup.sql (source data - keep this!)"
if (Test-Path $prodBackup) {
    Write-Host "  - $prodBackup (old production backup)"
}
Write-Host ""
Write-Host "Next steps:"
Write-Host "1. Verify data in production database"
Write-Host "2. Test critical functionality"
Write-Host "3. Monitor Railway logs for any errors"
Write-Host "4. Notify team that production data was updated"
Write-Host ""
Write-Host "If something went wrong, you can restore from:"
Write-Host "  $prodBackup"
Write-Host ""
