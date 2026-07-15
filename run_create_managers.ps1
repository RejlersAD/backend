# Automated Manager Creation Script
# Calls the production API to create RadAI managers

$BACKEND_URL = "https://aiflowbackend-production.up.railway.app"

Write-Host ""
Write-Host "================================================================================" -ForegroundColor Cyan
Write-Host "AUTOMATED RADAI MANAGER CREATION" -ForegroundColor Cyan
Write-Host "================================================================================" -ForegroundColor Cyan
Write-Host ""

# Wait for deployment
Write-Host "Waiting 30 seconds for Railway to redeploy..." -ForegroundColor Yellow
Start-Sleep -Seconds 30

# Check health
Write-Host "Checking backend health..." -ForegroundColor Yellow
try {
    $null = Invoke-RestMethod -Uri "$BACKEND_URL/api/v1/health/" -Method Get -ErrorAction Stop
    Write-Host "Backend is online" -ForegroundColor Green
} catch {
    Write-Host "Backend not ready - waiting 60 more seconds..." -ForegroundColor Yellow
    Start-Sleep -Seconds 60
}

# Get credentials
Write-Host ""
Write-Host "Please enter your admin credentials:" -ForegroundColor Cyan
$email = Read-Host "Email"
$password = Read-Host "Password" -AsSecureString
$passwordPlain = [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($password))

# Login
Write-Host "Logging in..." -ForegroundColor Yellow
try {
    $loginBody = @{ email = $email; password = $passwordPlain } | ConvertTo-Json
    $loginResponse = Invoke-RestMethod -Uri "$BACKEND_URL/api/v1/users/login/" -Method Post -Body $loginBody -ContentType "application/json" -ErrorAction Stop
    $token = $loginResponse.access
    Write-Host "Login successful" -ForegroundColor Green
} catch {
    Write-Host "Login failed: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}

# Create managers
Write-Host "Creating RadAI managers..." -ForegroundColor Yellow
try {
    $headers = @{ "Authorization" = "Bearer $token"; "Content-Type" = "application/json" }
    $response = Invoke-RestMethod -Uri "$BACKEND_URL/api/v1/rbac/admin/create-radai-managers/" -Method Post -Headers $headers -ErrorAction Stop

    Write-Host ""
    Write-Host "================================================================================" -ForegroundColor Green
    Write-Host "SUCCESS!" -ForegroundColor Green
    Write-Host "================================================================================" -ForegroundColor Green
    Write-Host ""
    Write-Host "Organization: $($response.organization.name)" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "Created/Updated:" -ForegroundColor Green
    foreach ($mgr in ($response.created + $response.updated)) {
        Write-Host "  $($mgr.name) - $($mgr.email)" -ForegroundColor Green
        Write-Host "    Department: $($mgr.department) | Job Title: $($mgr.job_title)" -ForegroundColor Gray
    }
    Write-Host ""
    Write-Host "Summary:" -ForegroundColor Cyan
    Write-Host "  Total:   $($response.summary.total)" -ForegroundColor White
    Write-Host "  Created: $($response.summary.created)" -ForegroundColor Green
    Write-Host "  Updated: $($response.summary.updated)" -ForegroundColor Yellow
    Write-Host "  Failed:  $($response.summary.failed)" -ForegroundColor Red
    Write-Host ""
    Write-Host "Next Steps:" -ForegroundColor Cyan
    Write-Host "  1. Go to https://www.radai.ae/profile" -ForegroundColor White
    Write-Host "  2. Clear browser cache (Ctrl+Shift+R)" -ForegroundColor White
    Write-Host "  3. Check 'Reporting Manager' dropdown" -ForegroundColor White
    Write-Host ""
    Write-Host "================================================================================" -ForegroundColor Green
    Write-Host ""

} catch {
    Write-Host ""
    Write-Host "FAILED TO CREATE MANAGERS" -ForegroundColor Red
    Write-Host "================================================================================" -ForegroundColor Red
    Write-Host "Error: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host ""
    Write-Host "This may mean:" -ForegroundColor Yellow
    Write-Host "  1. Railway is still deploying (wait and retry)" -ForegroundColor Yellow
    Write-Host "  2. You don't have admin privileges" -ForegroundColor Yellow
    Write-Host "  3. The endpoint hasn't been deployed yet" -ForegroundColor Yellow
    Write-Host ""
    exit 1
}
