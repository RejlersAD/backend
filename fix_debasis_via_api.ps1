# Emergency fix for Debasis.Sana@rejlers.ae admin access
# This script calls the production API to remove Django superuser/staff flags

$API_BASE = "https://aiflowbackend-production.up.railway.app/api"
$EMAIL_TO_FIX = "Debasis.Sana@rejlers.ae"

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "Emergency User Permission Fix" -ForegroundColor Cyan  
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""

# Step 1: Get your admin credentials
Write-Host "You need to login with YOUR super admin credentials" -ForegroundColor Yellow
Write-Host "(NOT Debasis's credentials)" -ForegroundColor Yellow
Write-Host ""
$adminEmail = Read-Host "Enter your admin email"
$adminPassword = Read-Host "Enter your admin password" -AsSecureString
$plainPassword = [Runtime.InteropServices.Marshal]::PtrToStringAuto(
    [Runtime.InteropServices.Marshal]::SecureStringToBSTR($adminPassword)
)

Write-Host ""
Write-Host "Step 1: Authenticating..." -ForegroundColor Cyan

# Login to get JWT token
$loginBody = @{
    email = $adminEmail
    password = $plainPassword
} | ConvertTo-Json

try {
    $loginResponse = Invoke-RestMethod -Uri "$API_BASE/v1/login/" `
        -Method POST `
        -Body $loginBody `
        -ContentType "application/json"
    
    $token = $loginResponse.access
    Write-Host "✅ Authentication successful" -ForegroundColor Green
}
catch {
    Write-Host "❌ Authentication failed: $_" -ForegroundColor Red
    exit 1
}

Write-Host ""
Write-Host "Step 2: Fixing user permissions for $EMAIL_TO_FIX..." -ForegroundColor Cyan

# Call the fix endpoint
$fixBody = @{
    email = $EMAIL_TO_FIX
} | ConvertTo-Json

try {
    $response = Invoke-RestMethod -Uri "$API_BASE/rbac/admin/fix-user-flags/" `
        -Method POST `
        -Headers @{
            "Authorization" = "Bearer $token"
            "Content-Type" = "application/json"
        } `
        -Body $fixBody
    
    Write-Host ""
    Write-Host "============================================================" -ForegroundColor Green
    Write-Host "✅ SUCCESS" -ForegroundColor Green
    Write-Host "============================================================" -ForegroundColor Green
    Write-Host ""
    Write-Host "Status: $($response.status)" -ForegroundColor Green
    Write-Host "Message: $($response.message)" -ForegroundColor Green
    Write-Host ""
    
    if ($response.before) {
        Write-Host "BEFORE:" -ForegroundColor Yellow
        Write-Host "  is_superuser: $($response.before.is_superuser)" -ForegroundColor Yellow
        Write-Host "  is_staff: $($response.before.is_staff)" -ForegroundColor Yellow
        Write-Host "  RBAC roles: $($response.before.rbac_roles -join ', ')" -ForegroundColor Yellow
        Write-Host ""
    }
    
    if ($response.after) {
        Write-Host "AFTER:" -ForegroundColor Green
        Write-Host "  is_superuser: $($response.after.is_superuser)" -ForegroundColor Green
        Write-Host "  is_staff: $($response.after.is_staff)" -ForegroundColor Green  
        Write-Host "  RBAC roles: $($response.after.rbac_roles -join ', ')" -ForegroundColor Green
        Write-Host ""
    }
    
    Write-Host "============================================================" -ForegroundColor Cyan
    Write-Host "NEXT STEPS:" -ForegroundColor Cyan
    Write-Host "1. Ask $EMAIL_TO_FIX to logout from https://www.radai.ae" -ForegroundColor White
    Write-Host "2. Clear browser cache/cookies" -ForegroundColor White
    Write-Host "3. Login again" -ForegroundColor White
    Write-Host "4. Verify they CANNOT access /admin/users or /admin" -ForegroundColor White
    Write-Host "============================================================" -ForegroundColor Cyan
}
catch {
    $errorDetails = $_.ErrorDetails.Message | ConvertFrom-Json
    Write-Host ""
    Write-Host "❌ Fix failed" -ForegroundColor Red
    Write-Host "Error: $($errorDetails.error)" -ForegroundColor Red
    Write-Host ""
    Write-Host "Full response: $_" -ForegroundColor Red
    exit 1
}
