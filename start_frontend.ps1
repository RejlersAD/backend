# Start Frontend Development Server
# Run this script to start the React frontend

Write-Host "`n==================================" -ForegroundColor Cyan
Write-Host "STARTING FRONTEND DEV SERVER" -ForegroundColor Cyan
Write-Host "==================================" -ForegroundColor Cyan

$frontendPath = "c:\Users\Abdullah.Khan\airflow_frontend"

# Check if directory exists
if (-not (Test-Path $frontendPath)) {
    Write-Host "Error: Frontend directory not found at $frontendPath" -ForegroundColor Red
    exit 1
}

# Change to frontend directory
Set-Location $frontendPath
Write-Host "Working directory: $(Get-Location)" -ForegroundColor Green

# Check if node_modules exists
if (-not (Test-Path "node_modules")) {
    Write-Host "`nInstalling dependencies..." -ForegroundColor Yellow
    npm install
}

# Check if .env exists
if (-not (Test-Path ".env")) {
    Write-Host "`nCreating .env file from example..." -ForegroundColor Yellow
    Copy-Item .env.example .env
    Write-Host "Created .env file" -ForegroundColor Green
}

Write-Host "`nStarting Vite development server..." -ForegroundColor Yellow
Write-Host "Frontend will be available at: http://localhost:5173/" -ForegroundColor Cyan
Write-Host "Press Ctrl+C to stop the server" -ForegroundColor Gray
Write-Host "==================================" -ForegroundColor Cyan

# Start the dev server
npm run dev
