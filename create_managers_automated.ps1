# ============================================================================
# Automated Manager Creation Script
# ============================================================================
# This script calls the production API to create the three RadAI managers
# ============================================================================

$BACKEND_URL = "https://aiflowbackend-production.up.railway.app"
$ENDPOINT = "/api/v1/rbac/admin/create-radai-managers/"

Write-Host "`n================================================================================" -ForegroundColor Cyan
Write-Host "🚀 AUTOMATED RADAI MANAGER CREATION" -ForegroundColor Cyan
Write-Host "================================================================================" -ForegroundColor Cyan

# Step 1: Wait for Railway deployment
Write-Host "`n⏳ Waiting 30 seconds for Railway to redeploy..." -ForegroundColor Yellow
Write-Host "   (Railway auto-deploys on git push, usually takes 2-5 minutes)" -ForegroundColor Gray
Start-Sleep -Seconds 30

# Step 2: Check if backend is up
Write-Host "`n🔍 Checking backend health..." -ForegroundColor Yellow
try {
    $healthCheck = Invoke-RestMethod -Uri "$BACKEND_URL/api/v1/health/" -Method Get -ErrorAction Stop
    Write-Host "   ✅ Backend is online" -ForegroundColor Green
} catch {
    Write-Host "   ⚠️  Backend not responding yet - may still be deploying" -ForegroundColor Yellow
    Write-Host "   Waiting another 60 seconds..." -ForegroundColor Yellow
    Start-Sleep -Seconds 60
}

# Step 3: Prompt for credentials
Write-Host "`n🔑 Please enter your admin credentials:" -ForegroundColor Cyan
$email = Read-Host "   Email"
$password = Read-Host "   Password" -AsSecureString
$passwordPlain = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
    [Runtime.InteropServices.Marshal]::SecureStringToBSTR($password)
)

# Step 4: Login to get token
Write-Host "`n🔐 Logging in..." -ForegroundColor Yellow
try {
    $loginBody = @{
        email = $email
        password = $passwordPlain
    } | ConvertTo-Json

    $loginResponse = Invoke-RestMethod `
        -Uri "$BACKEND_URL/api/v1/users/login/" `
        -Method Post `
        -Body $loginBody `
        -ContentType "application/json" `
        -ErrorAction Stop

    $token = $loginResponse.access
    Write-Host "   ✅ Login successful" -ForegroundColor Green
} catch {
    Write-Host "   ❌ Login failed: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "`n   Please ensure:" -ForegroundColor Yellow
    Write-Host "   1. You're using admin/superuser credentials" -ForegroundColor Yellow
    Write-Host "   2. Your account is active" -ForegroundColor Yellow
    Write-Host "   3. Backend is fully deployed`n" -ForegroundColor Yellow
    exit 1
}

# Step 5: Call the manager creation endpoint
Write-Host "`n👥 Creating RadAI managers..." -ForegroundColor Yellow
try {
    $headers = @{
        "Authorization" = "Bearer $token"
        "Content-Type" = "application/json"
    }

    $response = Invoke-RestMethod `
        -Uri "$BACKEND_URL$ENDPOINT" `
        -Method Post `
        -Headers $headers `
        -ErrorAction Stop

    Write-Host "`n" + "="*80 -ForegroundColor Green
    Write-Host "✅ SUCCESS!" -ForegroundColor Green
    Write-Host "="*80 -ForegroundColor Green

    Write-Host "`nOrganization: $($response.organization.name)" -ForegroundColor Cyan
    
    if ($response.created.Count -gt 0) {
        Write-Host "`n📝 Created Managers:" -ForegroundColor Green
        foreach ($mgr in $response.created) {
            Write-Host "   ✅ $($mgr.name) - $($mgr.email)" -ForegroundColor Green
            Write-Host "      Department: $($mgr.department) | Job Title: $($mgr.job_title)" -ForegroundColor Gray
        }
    }

    if ($response.updated.Count -gt 0) {
        Write-Host "`n🔄 Updated Managers:" -ForegroundColor Yellow
        foreach ($mgr in $response.updated) {
            Write-Host "   ✅ $($mgr.name) - $($mgr.email)" -ForegroundColor Yellow
            Write-Host "      Department: $($mgr.department) | Job Title: $($mgr.job_title)" -ForegroundColor Gray
        }
    }

    Write-Host "`n📊 Summary:" -ForegroundColor Cyan
    Write-Host "   Total Processed:  $($response.summary.total)" -ForegroundColor White
    Write-Host "   Created:          $($response.summary.created)" -ForegroundColor Green
    Write-Host "   Updated:          $($response.summary.updated)" -ForegroundColor Yellow
    Write-Host "   Failed:           $($response.summary.failed)" -ForegroundColor Red

    Write-Host "`n🎯 Next Steps:" -ForegroundColor Cyan
    foreach ($step in $response.next_steps) {
        Write-Host "   • $step" -ForegroundColor White
    }

    Write-Host "`n" + "="*80 -ForegroundColor Green
    Write-Host "🎉 All managers are now available in the Profile dropdown!" -ForegroundColor Green
    Write-Host "="*80 -ForegroundColor Green
    Write-Host ""

} catch {
    $errorDetails = $_.ErrorDetails.Message | ConvertFrom-Json -ErrorAction SilentlyContinue
    
    Write-Host "`n❌ FAILED TO CREATE MANAGERS" -ForegroundColor Red
    Write-Host "="*80 -ForegroundColor Red
    
    if ($errorDetails) {
        Write-Host "Error: $($errorDetails.error)" -ForegroundColor Red
        if ($errorDetails.detail) {
            Write-Host "Details: $($errorDetails.detail)" -ForegroundColor Red
        }
    } else {
        Write-Host "Error: $($_.Exception.Message)" -ForegroundColor Red
    }
    Write-Host ""
    Write-Host "   This may mean:" -ForegroundColor Yellow
    Write-Host "   1. Railway is still deploying (wait 2-3 minutes and retry)" -ForegroundColor Yellow
    Write-Host "   2. You don't have admin privileges" -ForegroundColor Yellow
    Write-Host "   3. The endpoint hasn't been deployed yet" -ForegroundColor Yellow
    Write-Host ""
    exit 1
}
