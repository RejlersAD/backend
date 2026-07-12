# ============================================================
# FIX CUSTOM ROLE PERMISSIONS - PowerShell Script
# ============================================================
# Purpose: Fix users who have Django admin flags but non-admin RBAC roles
# Users: kiran.ingale@rejlers.ae, ravikumar.naickar@rejlers.ae
# ============================================================

param(
    [switch]$DryRun = $false,
    [switch]$ApplyFix = $false,
    [string[]]$AdditionalEmails = @()
)

# Configuration
$TARGET_USERS = @(
    "kiran.ingale@rejlers.ae",
    "ravikumar.naickar@rejlers.ae"
) + $AdditionalEmails

function Write-Header {
    param([string]$Message)
    Write-Host ""
    Write-Host "=" * 70 -ForegroundColor Cyan
    Write-Host $Message -ForegroundColor Cyan
    Write-Host "=" * 70 -ForegroundColor Cyan
    Write-Host ""
}

function Write-Step {
    param([string]$Message)
    Write-Host ""
    Write-Host $Message -ForegroundColor Yellow
    Write-Host ""
}

function Test-DockerRunning {
    try {
        $result = docker ps 2>&1
        return $true
    } catch {
        return $false
    }
}

function Get-BackendContainerName {
    $containers = docker ps --filter "name=backend" --format "{{.Names}}"
    if ($containers) {
        return $containers[0]
    }
    return $null
}

# ============================================================
# MAIN EXECUTION
# ============================================================

Write-Header "🔧 FIX CUSTOM ROLE PERMISSIONS ISSUE"

Write-Host "Target Users:" -ForegroundColor White
foreach ($email in $TARGET_USERS) {
    Write-Host "  - $email" -ForegroundColor Gray
}
Write-Host ""

# Check if Docker is running
if (-not (Test-DockerRunning)) {
    Write-Host "❌ Error: Docker is not running" -ForegroundColor Red
    Write-Host "   Please start Docker Desktop and try again" -ForegroundColor Yellow
    exit 1
}

# Find backend container
$containerName = Get-BackendContainerName
if (-not $containerName) {
    Write-Host "❌ Error: Backend container not found" -ForegroundColor Red
    Write-Host "   Make sure Docker Compose is running:" -ForegroundColor Yellow
    Write-Host "   docker-compose --profile local up -d" -ForegroundColor Gray
    exit 1
}

Write-Host "✅ Found backend container: $containerName" -ForegroundColor Green
Write-Host ""

# ============================================================
# OPTION 1: Python Script (Recommended)
# ============================================================

Write-Step "📋 OPTION 1: Using Python Script"

if ($DryRun -or -not $ApplyFix) {
    Write-Host "Running dry run (no changes will be made)..." -ForegroundColor Cyan
    Write-Host ""
    
    docker exec -it $containerName python fix_custom_role_permissions.py
    
    Write-Host ""
    Write-Host "⚠️  This was a DRY RUN - no changes were made" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "To apply the fix:" -ForegroundColor White
    Write-Host "  1. Open fix_custom_role_permissions.py" -ForegroundColor Gray
    Write-Host "  2. Uncomment the 'STEP 3: Applying fixes' section at the bottom" -ForegroundColor Gray
    Write-Host "  3. Run this script again" -ForegroundColor Gray
    Write-Host ""
    Write-Host "OR run with -ApplyFix flag:" -ForegroundColor White
    Write-Host "  .\fix_custom_role_permissions.ps1 -ApplyFix" -ForegroundColor Gray
    
} elseif ($ApplyFix) {
    Write-Host "⚠️  WARNING: This will modify the database!" -ForegroundColor Red
    Write-Host "Users affected: $($TARGET_USERS -join ', ')" -ForegroundColor Yellow
    Write-Host ""
    
    $confirmation = Read-Host "Type 'YES' to proceed with the fix"
    
    if ($confirmation -eq 'YES') {
        Write-Host ""
        Write-Host "Applying fix via Python script..." -ForegroundColor Green
        Write-Host ""
        
        # Modify the Python script to apply fixes
        $pythonScript = Get-Content "backend\fix_custom_role_permissions.py" -Raw
        
        if ($pythonScript -match '# Uncomment the section below to apply fixes automatically') {
            # Create temporary script with fixes enabled
            $tempScript = $pythonScript -replace 
                '(?ms)# Uncomment the section below.*?"""',
                ''
            
            $tempScript | Set-Content "backend\fix_custom_role_permissions_APPLY.py"
            
            docker exec -it $containerName python fix_custom_role_permissions_APPLY.py
            
            Remove-Item "backend\fix_custom_role_permissions_APPLY.py" -ErrorAction SilentlyContinue
            
            Write-Host ""
            Write-Host "✅ Fix applied successfully!" -ForegroundColor Green
            Write-Host ""
            Write-Host "⚠️  IMPORTANT NEXT STEPS:" -ForegroundColor Yellow
            Write-Host "  1. Users must LOGOUT from https://www.radai.ae" -ForegroundColor White
            Write-Host "  2. Users should clear browser cache/cookies" -ForegroundColor White
            Write-Host "  3. Users LOGIN again - permissions will be correct" -ForegroundColor White
            
        } else {
            Write-Host "❌ Error: Could not modify Python script" -ForegroundColor Red
            Write-Host "   Please edit fix_custom_role_permissions.py manually" -ForegroundColor Yellow
        }
        
    } else {
        Write-Host ""
        Write-Host "❌ Fix cancelled" -ForegroundColor Yellow
    }
}

# ============================================================
# OPTION 2: SQL Script (Alternative)
# ============================================================

Write-Step "📋 OPTION 2: Using SQL Script (Alternative)"

Write-Host "If Python script doesn't work, you can use the SQL script:" -ForegroundColor White
Write-Host ""
Write-Host "Railway Dashboard Method:" -ForegroundColor Cyan
Write-Host "  1. Go to https://railway.app/dashboard" -ForegroundColor Gray
Write-Host "  2. Select your project > Postgres service > Data tab" -ForegroundColor Gray
Write-Host "  3. Open: backend\fix_custom_role_permissions.sql" -ForegroundColor Gray
Write-Host "  4. Copy and paste each STEP, run one at a time" -ForegroundColor Gray
Write-Host ""
Write-Host "Local Docker Method:" -ForegroundColor Cyan
Write-Host "  1. docker exec -it aiflow_postgres_local psql -U postgres -d aiflow" -ForegroundColor Gray
Write-Host "  2. Copy and paste queries from fix_custom_role_permissions.sql" -ForegroundColor Gray
Write-Host ""

# ============================================================
# VERIFICATION
# ============================================================

Write-Step "📋 VERIFICATION QUERY"

Write-Host "To verify the fix, run this query:" -ForegroundColor White
Write-Host ""

$verifyQuery = @"
SELECT 
    email,
    is_superuser,
    is_staff,
    is_active
FROM auth_user
WHERE email IN (
    '$($TARGET_USERS -join "', '")'
);
"@

Write-Host $verifyQuery -ForegroundColor Gray
Write-Host ""
Write-Host "Expected result:" -ForegroundColor White
Write-Host "  is_superuser = false" -ForegroundColor Green
Write-Host "  is_staff = false" -ForegroundColor Green
Write-Host "  is_active = true" -ForegroundColor Green
Write-Host ""

# ============================================================
# USAGE EXAMPLES
# ============================================================

Write-Header "📖 USAGE EXAMPLES"

Write-Host "Dry run (check status only):" -ForegroundColor White
Write-Host "  .\fix_custom_role_permissions.ps1" -ForegroundColor Gray
Write-Host "  .\fix_custom_role_permissions.ps1 -DryRun" -ForegroundColor Gray
Write-Host ""

Write-Host "Apply fix:" -ForegroundColor White
Write-Host "  .\fix_custom_role_permissions.ps1 -ApplyFix" -ForegroundColor Gray
Write-Host ""

Write-Host "Add more users to fix:" -ForegroundColor White
Write-Host '  .\fix_custom_role_permissions.ps1 -ApplyFix -AdditionalEmails @("user1@example.com", "user2@example.com")' -ForegroundColor Gray
Write-Host ""

Write-Header "✅ SCRIPT COMPLETE"
