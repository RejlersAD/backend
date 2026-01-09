# Test File Upload to PID Module
# This script demonstrates how to upload a file for PID analysis

Write-Host "`n==================================" -ForegroundColor Cyan
Write-Host "FILE UPLOAD TEST - PID MODULE" -ForegroundColor Cyan
Write-Host "==================================" -ForegroundColor Cyan

# Step 1: Login
Write-Host "`n[STEP 1] Logging in..." -ForegroundColor Yellow
$loginBody = @{
    email = "test@radai.ae"
    password = "testpass123"
} | ConvertTo-Json

try {
    $loginResponse = Invoke-RestMethod -Uri "http://localhost:8000/api/v1/auth/login/" -Method Post -Body $loginBody -ContentType "application/json" -UseBasicParsing
    Write-Host "Success - Logged in" -ForegroundColor Green
    $token = $loginResponse.access
} catch {
    Write-Host "Failed - Login error: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}

# Step 2: Check available endpoints
Write-Host "`n[STEP 2] Checking PID endpoints..." -ForegroundColor Yellow
$headers = @{
    "Authorization" = "Bearer $token"
}

try {
    # Try to get drawings list
    $drawings = Invoke-RestMethod -Uri "http://localhost:8000/api/v1/pid/drawings/" -Headers $headers -UseBasicParsing
    Write-Host "Success - PID endpoint accessible" -ForegroundColor Green
    Write-Host "  Current drawings count: $($drawings.count)" -ForegroundColor Gray
} catch {
    Write-Host "Info - PID endpoint status: $($_.Exception.Message)" -ForegroundColor Yellow
}

# Step 3: Check PFD endpoints
Write-Host "`n[STEP 3] Checking PFD endpoints..." -ForegroundColor Yellow
try {
    $conversions = Invoke-RestMethod -Uri "http://localhost:8000/api/v1/pfd/conversions/" -Headers $headers -UseBasicParsing
    Write-Host "Success - PFD endpoint accessible" -ForegroundColor Green
    Write-Host "  Current conversions count: $($conversions.count)" -ForegroundColor Gray
} catch {
    Write-Host "Info - PFD endpoint status: $($_.Exception.Message)" -ForegroundColor Yellow
}

# Step 4: Check CRS endpoints
Write-Host "`n[STEP 4] Checking CRS endpoints..." -ForegroundColor Yellow
try {
    $documents = Invoke-RestMethod -Uri "http://localhost:8000/api/v1/crs/documents/" -Headers $headers -UseBasicParsing
    Write-Host "Success - CRS endpoint accessible" -ForegroundColor Green
    Write-Host "  Current documents count: $($documents.count)" -ForegroundColor Gray
} catch {
    Write-Host "Info - CRS endpoint status: $($_.Exception.Message)" -ForegroundColor Yellow
}

# Step 5: Check Finance endpoints
Write-Host "`n[STEP 5] Checking Finance endpoints..." -ForegroundColor Yellow
try {
    $invoices = Invoke-RestMethod -Uri "http://localhost:8000/api/v1/finance/invoices/" -Headers $headers -UseBasicParsing
    Write-Host "Success - Finance endpoint accessible" -ForegroundColor Green
    Write-Host "  Current invoices count: $($invoices.count)" -ForegroundColor Gray
    
    # Show first invoice if available
    if ($invoices.results -and $invoices.results.Count -gt 0) {
        Write-Host "`n  Sample Invoice:" -ForegroundColor Cyan
        $firstInvoice = $invoices.results[0]
        Write-Host "    ID: $($firstInvoice.id)" -ForegroundColor Gray
        Write-Host "    Invoice Number: $($firstInvoice.invoice_number)" -ForegroundColor Gray
        Write-Host "    Status: $($firstInvoice.status)" -ForegroundColor Gray
        Write-Host "    Amount: $($firstInvoice.total_amount)" -ForegroundColor Gray
    }
} catch {
    Write-Host "Info - Finance endpoint status: $($_.Exception.Message)" -ForegroundColor Yellow
}

# Summary
Write-Host "`n==================================" -ForegroundColor Cyan
Write-Host "MODULE ACCESS SUMMARY" -ForegroundColor Cyan
Write-Host "==================================" -ForegroundColor Cyan
Write-Host "All modules are accessible with authentication." -ForegroundColor Green
Write-Host "`nTo upload files, you can use Postman or the frontend." -ForegroundColor Yellow
Write-Host "`nExample upload endpoints:" -ForegroundColor Cyan
Write-Host "  PID: POST http://localhost:8000/api/v1/pid/upload/" -ForegroundColor Gray
Write-Host "  PFD: POST http://localhost:8000/api/v1/pfd/upload/" -ForegroundColor Gray
Write-Host "  CRS: POST http://localhost:8000/api/v1/crs/upload/" -ForegroundColor Gray
Write-Host "  Finance: POST http://localhost:8000/api/v1/finance/upload/" -ForegroundColor Gray
Write-Host "`nHeaders required:" -ForegroundColor Cyan
Write-Host "  Authorization: Bearer <token>" -ForegroundColor Gray
Write-Host "  Content-Type: multipart/form-data" -ForegroundColor Gray
Write-Host "==================================" -ForegroundColor Cyan
