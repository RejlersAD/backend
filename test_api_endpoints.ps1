# API Testing Guide - Quick Reference

## Authentication Test
# Login and get token
$loginBody = @{
    email = "test@radai.ae"
    password = "testpass123"
} | ConvertTo-Json

$response = Invoke-RestMethod -Uri "http://localhost:8000/api/v1/auth/login/" -Method Post -Body $loginBody -ContentType "application/json" -UseBasicParsing
$token = $response.access
Write-Host "Token: $token" -ForegroundColor Green

## Test Protected Endpoints with Token
$headers = @{
    "Authorization" = "Bearer $token"
    "Accept" = "application/json"
}

# Get features
Write-Host "`n=== Features ===" -ForegroundColor Cyan
$features = Invoke-RestMethod -Uri "http://localhost:8000/api/v1/features/" -Headers $headers -UseBasicParsing
$features | ConvertTo-Json -Depth 3

# Get user profile
Write-Host "`n=== User Profile ===" -ForegroundColor Cyan
$profile = Invoke-RestMethod -Uri "http://localhost:8000/api/v1/users/" -Headers $headers -UseBasicParsing
$profile | ConvertTo-Json -Depth 3

# Test Finance endpoints
Write-Host "`n=== Finance Module ===" -ForegroundColor Cyan
try {
    $invoices = Invoke-RestMethod -Uri "http://localhost:8000/api/v1/finance/invoices/" -Headers $headers -UseBasicParsing
    Write-Host "Invoices count: $($invoices.count)" -ForegroundColor Green
} catch {
    Write-Host "Finance endpoint: $($_.Exception.Message)" -ForegroundColor Yellow
}

# Test PID Analysis endpoints
Write-Host "`n=== PID Analysis Module ===" -ForegroundColor Cyan
try {
    $drawings = Invoke-RestMethod -Uri "http://localhost:8000/api/v1/pid/drawings/" -Headers $headers -UseBasicParsing
    Write-Host "Drawings count: $($drawings.count)" -ForegroundColor Green
} catch {
    Write-Host "PID endpoint: $($_.Exception.Message)" -ForegroundColor Yellow
}

# Test PFD Converter endpoints
Write-Host "`n=== PFD Converter Module ===" -ForegroundColor Cyan
try {
    $conversions = Invoke-RestMethod -Uri "http://localhost:8000/api/v1/pfd/conversions/" -Headers $headers -UseBasicParsing
    Write-Host "Conversions count: $($conversions.count)" -ForegroundColor Green
} catch {
    Write-Host "PFD endpoint: $($_.Exception.Message)" -ForegroundColor Yellow
}

# Test CRS endpoints
Write-Host "`n=== CRS Module ===" -ForegroundColor Cyan
try {
    $crs = Invoke-RestMethod -Uri "http://localhost:8000/api/v1/crs/documents/" -Headers $headers -UseBasicParsing
    Write-Host "CRS documents count: $($crs.count)" -ForegroundColor Green
} catch {
    Write-Host "CRS endpoint: $($_.Exception.Message)" -ForegroundColor Yellow
}

Write-Host "`n=== Test Complete ===" -ForegroundColor Green
