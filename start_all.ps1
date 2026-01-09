# Start Both Backend and Frontend Services
# Run this script to start the full stack application

Write-Host "`n==================================" -ForegroundColor Cyan
Write-Host "STARTING FULL STACK APPLICATION" -ForegroundColor Cyan
Write-Host "==================================" -ForegroundColor Cyan

# Start Backend Containers
Write-Host "`n[1/2] Starting Backend Containers..." -ForegroundColor Yellow
Set-Location "c:\Users\Abdullah.Khan\RAD_AI"

$backendRunning = docker ps --filter "name=aiflow_backend" --format "{{.Names}}"
if ($backendRunning -match "aiflow_backend") {
    Write-Host "Backend is already running" -ForegroundColor Green
} else {
    Write-Host "Starting backend containers..." -ForegroundColor Yellow
    docker-compose up -d
    Start-Sleep -Seconds 5
    Write-Host "Backend started" -ForegroundColor Green
}

# Check backend health
Write-Host "`nChecking backend health..." -ForegroundColor Yellow
try {
    $health = Invoke-RestMethod -Uri "http://localhost:8000/api/v1/health/" -UseBasicParsing
    Write-Host "✓ Backend is healthy" -ForegroundColor Green
} catch {
    Write-Host "⚠ Backend health check failed. It may still be starting up." -ForegroundColor Yellow
}

# Start Frontend
Write-Host "`n[2/2] Starting Frontend..." -ForegroundColor Yellow
Write-Host "==================================" -ForegroundColor Cyan
Write-Host "`nFrontend needs to run in a separate terminal." -ForegroundColor Yellow
Write-Host "Please run this command in a new PowerShell window:" -ForegroundColor Cyan
Write-Host "  cd c:\Users\Abdullah.Khan\airflow_frontend; npm run dev" -ForegroundColor White
Write-Host "`nOr run this script:" -ForegroundColor Cyan
Write-Host "  .\start_frontend.ps1" -ForegroundColor White

Write-Host "`n==================================" -ForegroundColor Cyan
Write-Host "APPLICATION URLS" -ForegroundColor Cyan
Write-Host "==================================" -ForegroundColor Cyan
Write-Host "Backend API:  http://localhost:8000" -ForegroundColor White
Write-Host "API Docs:     http://localhost:8000/api/docs/" -ForegroundColor White
Write-Host "Frontend:     http://localhost:5173/ (after starting)" -ForegroundColor White
Write-Host "`nTest Login:" -ForegroundColor Yellow
Write-Host "  Email: test@radai.ae" -ForegroundColor Gray
Write-Host "  Password: testpass123" -ForegroundColor Gray
Write-Host "==================================" -ForegroundColor Cyan
