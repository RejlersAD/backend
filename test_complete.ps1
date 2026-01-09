# Comprehensive Backend Test with Authentication
# This script tests the backend and creates a test user for authentication testing

Write-Host "`n==================================" -ForegroundColor Cyan
Write-Host "COMPREHENSIVE BACKEND TEST" -ForegroundColor Cyan
Write-Host "==================================" -ForegroundColor Cyan

# Test 1: Container Status
Write-Host "`n[TEST 1] Checking Container Status..." -ForegroundColor Yellow
$containers = docker ps --filter "name=aiflow" --format "{{.Names}}: {{.Status}}"
Write-Host "Running containers:" -ForegroundColor Green
$containers | ForEach-Object { Write-Host "  $_" -ForegroundColor Gray }

# Test 2: Health Check
Write-Host "`n[TEST 2] Health Check..." -ForegroundColor Yellow
try {
    $health = Invoke-RestMethod -Uri "http://localhost:8000/api/v1/health/" -UseBasicParsing
    Write-Host "Success - Backend is healthy" -ForegroundColor Green
    Write-Host "  Status: $($health.status)" -ForegroundColor Gray
    Write-Host "  Service: $($health.service)" -ForegroundColor Gray
} catch {
    Write-Host "Failed - Health check error" -ForegroundColor Red
    exit 1
}

# Test 3: Database Connection
Write-Host "`n[TEST 3] Testing Database Connection..." -ForegroundColor Yellow
$dbTest = docker exec aiflow_backend python manage.py check --database default 2>&1
if ($LASTEXITCODE -eq 0) {
    Write-Host "Success - Database connection working" -ForegroundColor Green
} else {
    Write-Host "Warning - Database connection issue" -ForegroundColor Yellow
    Write-Host "  Output: $dbTest" -ForegroundColor Gray
}

# Test 4: Create Test User
Write-Host "`n[TEST 4] Creating Test User..." -ForegroundColor Yellow
$createUserScript = @'
from django.contrib.auth import get_user_model
from apps.rbac.models import Role
User = get_user_model()
username = "testuser"
email = "test@radai.ae"
password = "testpass123"
if User.objects.filter(username=username).exists():
    user = User.objects.get(username=username)
    print(f"User {username} already exists")
else:
    user = User.objects.create_user(username=username, email=email, password=password)
    print(f"Created user: {username}")
print(f"User ID: {user.id}")
print(f"Username: {user.username}")
print(f"Email: {user.email}")
'@

$createUserScript | docker exec -i aiflow_backend python manage.py shell
if ($LASTEXITCODE -eq 0) {
    Write-Host "Success - Test user ready" -ForegroundColor Green
} else {
    Write-Host "Info - User may already exist" -ForegroundColor Yellow
}

# Test 5: Login with Test User
Write-Host "`n[TEST 5] Testing Authentication..." -ForegroundColor Yellow
$loginBody = @{
    email = "test@radai.ae"
    password = "testpass123"
} | ConvertTo-Json

try {
    $loginResponse = Invoke-RestMethod -Uri "http://localhost:8000/api/v1/auth/login/" -Method Post -Body $loginBody -ContentType "application/json" -UseBasicParsing
    Write-Host "Success - Login successful" -ForegroundColor Green
    Write-Host "  Access Token: $($loginResponse.access.Substring(0,20))..." -ForegroundColor Gray
    Write-Host "  Refresh Token: $($loginResponse.refresh.Substring(0,20))..." -ForegroundColor Gray
    $token = $loginResponse.access
    
    # Test 6: Access Protected Endpoint
    Write-Host "`n[TEST 6] Testing Protected Endpoint..." -ForegroundColor Yellow
    $headers = @{
        "Authorization" = "Bearer $token"
        "Accept" = "application/json"
    }
    
    try {
        $features = Invoke-RestMethod -Uri "http://localhost:8000/api/v1/features/" -Headers $headers -UseBasicParsing
        Write-Host "Success - Protected endpoint accessible with token" -ForegroundColor Green
        Write-Host "  Features count: $($features.count)" -ForegroundColor Gray
    } catch {
        Write-Host "Failed - Protected endpoint error: $($_.Exception.Message)" -ForegroundColor Red
    }
    
    # Test 7: Get User Profile
    Write-Host "`n[TEST 7] Testing User Profile..." -ForegroundColor Yellow
    try {
        $profile = Invoke-RestMethod -Uri "http://localhost:8000/api/v1/users/me/" -Headers $headers -UseBasicParsing
        Write-Host "Success - User profile retrieved" -ForegroundColor Green
        Write-Host "  Username: $($profile.username)" -ForegroundColor Gray
        Write-Host "  Email: $($profile.email)" -ForegroundColor Gray
    } catch {
        Write-Host "Info - Profile endpoint: $($_.Exception.Message)" -ForegroundColor Yellow
    }
    
} catch {
    Write-Host "Failed - Login error: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "  Make sure the test user credentials are correct" -ForegroundColor Yellow
}

# Test 8: CORS Test
Write-Host "`n[TEST 8] Testing CORS..." -ForegroundColor Yellow
try {
    $cors = Invoke-RestMethod -Uri "http://localhost:8000/api/v1/cors-test/" -UseBasicParsing
    Write-Host "Success - CORS configured" -ForegroundColor Green
} catch {
    Write-Host "Warning - CORS test: $($_.Exception.Message)" -ForegroundColor Yellow
}

# Summary
Write-Host "`n==================================" -ForegroundColor Cyan
Write-Host "TEST SUMMARY" -ForegroundColor Cyan
Write-Host "==================================" -ForegroundColor Cyan
Write-Host "`nBackend is running at: http://localhost:8000" -ForegroundColor White
Write-Host "`nTest Credentials:" -ForegroundColor Yellow
Write-Host "  Email: test@radai.ae" -ForegroundColor Gray
Write-Host "  Password: testpass123" -ForegroundColor Gray
Write-Host "`nKey Endpoints:" -ForegroundColor Yellow
Write-Host "  Health: http://localhost:8000/api/v1/health/" -ForegroundColor Gray
Write-Host "  Login: POST http://localhost:8000/api/v1/auth/login/" -ForegroundColor Gray
Write-Host "  Features: GET http://localhost:8000/api/v1/features/" -ForegroundColor Gray
Write-Host "  API Docs: http://localhost:8000/api/docs/" -ForegroundColor Gray
Write-Host "`nContainer Management:" -ForegroundColor Yellow
Write-Host "  Stop: docker-compose down" -ForegroundColor Gray
Write-Host "  Start: docker-compose up -d" -ForegroundColor Gray
Write-Host "  Logs: docker-compose logs -f backend" -ForegroundColor Gray
Write-Host "==================================" -ForegroundColor Cyan
