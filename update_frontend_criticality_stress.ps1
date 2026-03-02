# PowerShell script to update frontend with Criticality Stress column

Write-Host "🔄 Updating Frontend with Criticality Stress Column..." -ForegroundColor Cyan

# Define paths
$backendFile = "C:\Users\Abdullah.Khan\RAD_AI\DesignIQLists.jsx"
$frontendFile = "C:\Users\Abdullah.Khan\airflow_frontend\src\pages\DesignIQ\DesignIQLists.jsx"
$frontendDir = "C:\Users\Abdullah.Khan\airflow_frontend"

# Step 1: Check if files exist
if (-not (Test-Path $backendFile)) {
    Write-Host "❌ Backend file not found: $backendFile" -ForegroundColor Red
    exit 1
}

if (-not (Test-Path $frontendDir)) {
    Write-Host "❌ Frontend directory not found: $frontendDir" -ForegroundColor Red
    Write-Host "   Please provide the correct frontend path" -ForegroundColor Yellow
    exit 1
}

# Step 2: Copy updated file to frontend
Write-Host "📋 Copying updated DesignIQLists.jsx to frontend..." -ForegroundColor Yellow
Copy-Item -Path $backendFile -Destination $frontendFile -Force

if ($?) {
    Write-Host "✅ File copied successfully!" -ForegroundColor Green
} else {
    Write-Host "❌ Failed to copy file" -ForegroundColor Red
    exit 1
}

# Step 3: Verify the change is in the file
Write-Host "🔍 Verifying 'Criticality Stress' column in frontend file..." -ForegroundColor Yellow
$content = Get-Content $frontendFile -Raw
if ($content -match "Criticality Stress") {
    Write-Host "✅ 'Criticality Stress' column found in frontend file!" -ForegroundColor Green
} else {
    Write-Host "❌ 'Criticality Stress' column NOT found in frontend file!" -ForegroundColor Red
    exit 1
}

# Step 4: Restart backend container
Write-Host "🔄 Restarting backend container..." -ForegroundColor Yellow
Set-Location "C:\Users\Abdullah.Khan\RAD_AI"
docker-compose restart backend

if ($?) {
    Write-Host "✅ Backend container restarted!" -ForegroundColor Green
} else {
    Write-Host "⚠️ Failed to restart backend container" -ForegroundColor Yellow
}

# Step 5: Instructions for frontend rebuild
Write-Host ""
Write-Host "=" * 60 -ForegroundColor Cyan
Write-Host "🎯 NEXT STEPS:" -ForegroundColor Cyan
Write-Host "=" * 60 -ForegroundColor Cyan
Write-Host ""
Write-Host "The frontend file has been updated with 'Criticality Stress' column!" -ForegroundColor Green
Write-Host ""
Write-Host "If frontend is running, it should auto-reload." -ForegroundColor Yellow
Write-Host "If not, manually restart it:" -ForegroundColor Yellow
Write-Host ""
Write-Host "  cd C:\Users\Abdullah.Khan\airflow_frontend" -ForegroundColor White
Write-Host "  npm run dev" -ForegroundColor White
Write-Host ""
Write-Host "Then test by uploading all 5 documents again." -ForegroundColor Cyan
Write-Host ""
Write-Host "=" * 60 -ForegroundColor Cyan
