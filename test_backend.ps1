# Backend Testing Script
# Tests the Docker container and key API endpoints

Write-Host "`n==================================" -ForegroundColor Cyan
Write-Host "BACKEND CONTAINER TEST" -ForegroundColor Cyan
Write-Host "==================================" -ForegroundColor Cyan

# Test 1: Health Check
Write-Host "`n[TEST 1] Health Check..." -ForegroundColor Yellow
try {
    $health = Invoke-RestMethod -Uri "http://localhost:8000/api/v1/health/" -UseBasicParsing
    Write-Host "Success - Backend is healthy" -ForegroundColor Green
    Write-Host "  Status: $($health.status)" -ForegroundColor Gray
    Write-Host "  Service: $($health.service)" -ForegroundColor Gray
    Write-Host "  Timestamp: $($health.timestamp)" -ForegroundColor Gray
} catch {
    Write-Host "Failed - Health check error: $($_.Exception.Message)" -ForegroundColor Red
}

# Test 2: Diagnostic Check
Write-Host "`n[TEST 2] Diagnostic Check..." -ForegroundColor Yellow
try {
    $diagnostic = Invoke-RestMethod -Uri "http://localhost:8000/api/v1/health/diagnostic/" -UseBasicParsing
    Write-Host "Success - Diagnostic check passed" -ForegroundColor Green
    Write-Host "  Database: $($diagnostic.database)" -ForegroundColor Gray
    Write-Host "  Redis: $($diagnostic.redis)" -ForegroundColor Gray
    if ($diagnostic.errors -and $diagnostic.errors.Count -gt 0) {
        Write-Host "  Errors: $($diagnostic.errors -join ', ')" -ForegroundColor Red
    }
} catch {
    Write-Host "Failed - Diagnostic error: $($_.Exception.Message)" -ForegroundColor Red
}

# Test 3: CORS Test
Write-Host "`n[TEST 3] CORS Configuration..." -ForegroundColor Yellow
try {
    $cors = Invoke-RestMethod -Uri "http://localhost:8000/api/v1/cors-test/" -UseBasicParsing
    Write-Host "Success - CORS test passed" -ForegroundColor Green
    Write-Host "  CORS Enabled: $($cors.cors_enabled)" -ForegroundColor Gray
} catch {
    Write-Host "Failed - CORS test error: $($_.Exception.Message)" -ForegroundColor Red
}

# Test 4: API Documentation
Write-Host "`n[TEST 4] API Documentation..." -ForegroundColor Yellow
try {
    $response = Invoke-WebRequest -Uri "http://localhost:8000/api/docs/" -UseBasicParsing
    if ($response.StatusCode -eq 200) {
        Write-Host "Success - API documentation is accessible" -ForegroundColor Green
        Write-Host "  URL: http://localhost:8000/api/docs/" -ForegroundColor Gray
    }
} catch {
    Write-Host "Failed - API documentation error: $($_.Exception.Message)" -ForegroundColor Red
}

# Test 5: Authentication Endpoints
Write-Host "`n[TEST 5] Authentication Endpoints..." -ForegroundColor Yellow
try {
    $response = Invoke-WebRequest -Uri "http://localhost:8000/api/v1/users/login/" -Method Post -UseBasicParsing -ErrorAction Stop
} catch {
    if ($_.Exception.Response.StatusCode.value__ -eq 400 -or $_.Exception.Response.StatusCode.value__ -eq 401) {
        Write-Host "Success - Login endpoint exists (requires credentials)" -ForegroundColor Green
    } else {
        Write-Host "Failed - Login endpoint error: $($_.Exception.Message)" -ForegroundColor Red
    }
}

# Test 6: Features Endpoint (should require auth)
Write-Host "`n[TEST 6] Protected Endpoints..." -ForegroundColor Yellow
try {
    $response = Invoke-RestMethod -Uri "http://localhost:8000/api/v1/features/" -UseBasicParsing
    Write-Host "Warning - Features endpoint accessible without auth (security issue)" -ForegroundColor Yellow
} catch {
    if ($_.Exception.Message -match "Authentication credentials") {
        Write-Host "Success - Features endpoint properly protected" -ForegroundColor Green
    } else {
        Write-Host "Failed - Unexpected error: $($_.Exception.Message)" -ForegroundColor Red
    }
}

# Test 7: Check Container Status
Write-Host "`n[TEST 7] Container Status..." -ForegroundColor Yellow
try {
    $containers = docker ps --filter "name=aiflow" --format "{{.Names}}: {{.Status}}"
    Write-Host "Success - Running containers:" -ForegroundColor Green
    $containers | ForEach-Object { Write-Host "  $_" -ForegroundColor Gray }
} catch {
    Write-Host "Failed - Container status error: $($_.Exception.Message)" -ForegroundColor Red
}

# Summary
Write-Host "`n==================================" -ForegroundColor Cyan
Write-Host "TEST SUMMARY" -ForegroundColor Cyan
Write-Host "==================================" -ForegroundColor Cyan
Write-Host "Backend URL: http://localhost:8000" -ForegroundColor White
Write-Host "API Docs: http://localhost:8000/api/docs/" -ForegroundColor White
Write-Host "Health Check: http://localhost:8000/api/v1/health/" -ForegroundColor White
Write-Host "`nTo test with authentication, you will need to:" -ForegroundColor Yellow
Write-Host "1. Create a user account or use existing credentials" -ForegroundColor Gray
Write-Host "2. Login at: POST http://localhost:8000/api/v1/users/login/" -ForegroundColor Gray
Write-Host "3. Use the returned token in Authorization header" -ForegroundColor Gray
Write-Host "==================================" -ForegroundColor Cyan
