# Simple API test for fixing user permissions
$API_BASE = "https://aiflowbackend-production.up.railway.app/api/v1"

Write-Host "Testing authentication..." -ForegroundColor Cyan

# Test 1: Login
$loginBody = @{
    email = "tanzeem.agra@rejlers.ae"
    password = "Tanzeem@786"
} | ConvertTo-Json

Write-Host "Calling: POST $API_BASE/auth/login/" -ForegroundColor Gray

try {
    $response = Invoke-WebRequest -Uri "$API_BASE/auth/login/" `
        -Method POST `
        -Body $loginBody `
        -ContentType "application/json" `
        -UseBasicParsing
    
    $loginData = $response.Content | ConvertFrom-Json
    $token = $loginData.access
    
    Write-Host "✅ Login successful" -ForegroundColor Green
    Write-Host "Token: $($token.Substring(0, 20))..." -ForegroundColor Gray
    Write-Host ""
    
    # Test 2: Fix user permissions
    Write-Host "Fixing Debasis.Sana@rejlers.ae permissions..." -ForegroundColor Cyan
    
    $fixBody = @{
        email = "Debasis.Sana@rejlers.ae"
    } | ConvertTo-Json
    
    Write-Host "Calling: POST $API_BASE/rbac/admin/fix-user-flags/" -ForegroundColor Gray
    
    $fixResponse = Invoke-WebRequest -Uri "$API_BASE/rbac/admin/fix-user-flags/" `
        -Method POST `
        -Headers @{
            "Authorization" = "Bearer $token"
            "Content-Type" = "application/json"
        } `
        -Body $fixBody `
        -UseBasicParsing
    
    $fixData = $fixResponse.Content | ConvertFrom-Json
    
    Write-Host ""
    Write-Host "============================================================" -ForegroundColor Green
    Write-Host "✅ SUCCESS" -ForegroundColor Green
    Write-Host "============================================================" -ForegroundColor Green
    Write-Host ""
    Write-Host "Status: $($fixData.status)"
    Write-Host "Message: $($fixData.message)"
    Write-Host ""
    
    if ($fixData.before) {
        Write-Host "BEFORE FIX:" -ForegroundColor Yellow
        Write-Host "  Email: $($fixData.before.email)"
        Write-Host "  is_superuser: $($fixData.before.is_superuser)" -ForegroundColor Red
        Write-Host "  is_staff: $($fixData.before.is_staff)" -ForegroundColor Red
        Write-Host "  RBAC roles: $($fixData.before.rbac_roles -join ', ')"
        Write-Host ""
    }
    
    if ($fixData.after) {
        Write-Host "AFTER FIX:" -ForegroundColor Green
        Write-Host "  Email: $($fixData.after.email)"
        Write-Host "  is_superuser: $($fixData.after.is_superuser)" -ForegroundColor Green
        Write-Host "  is_staff: $($fixData.after.is_staff)" -ForegroundColor Green
        Write-Host "  RBAC roles: $($fixData.after.rbac_roles -join ', ')"
        Write-Host ""
    }
    
    if ($fixData.changes_applied) {
        Write-Host "CHANGES:" -ForegroundColor Cyan
        foreach ($key in $fixData.changes_applied.PSObject.Properties.Name) {
            Write-Host "  $key : $($fixData.changes_applied.$key)"
        }
        Write-Host ""
    }
    
    Write-Host "============================================================" -ForegroundColor Cyan
    Write-Host "NEXT STEPS:" -ForegroundColor Cyan
    Write-Host "1. Ask Debasis.Sana@rejlers.ae to LOGOUT" -ForegroundColor White
    Write-Host "2. Clear browser cache" -ForegroundColor White
    Write-Host "3. LOGIN again" -ForegroundColor White
    Write-Host "4. Verify NO ACCESS to /admin/users" -ForegroundColor White
    Write-Host "============================================================" -ForegroundColor Cyan
    
} catch {
    Write-Host ""
    Write-Host "❌ ERROR" -ForegroundColor Red
    Write-Host "Status Code: $($_.Exception.Response.StatusCode.value__)" -ForegroundColor Red
    Write-Host "Status Description: $($_.Exception.Response.StatusDescription)" -ForegroundColor Red
    Write-Host ""
    
    if ($_.ErrorDetails.Message) {
        Write-Host "Error Details:" -ForegroundColor Red
        Write-Host $_.ErrorDetails.Message -ForegroundColor Red
    }
    
    Write-Host ""
    Write-Host "Full Error:" -ForegroundColor Yellow
    Write-Host $_ -ForegroundColor Yellow
    exit 1
}
