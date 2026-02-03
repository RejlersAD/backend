# Backend Push
Write-Host "`n=== BACKEND (RAD_AI) ===" -ForegroundColor Green
cd C:\Users\Abdullah.Khan\RAD_AI

Write-Host "Checking status..." -ForegroundColor Yellow
$status = git status --porcelain
if ($status) {
    Write-Host "Changes found:" -ForegroundColor Cyan
    git status --short
    
    Write-Host "`nAdding files..." -ForegroundColor Yellow
    git add .
    
    Write-Host "Committing..." -ForegroundColor Yellow
    git commit -m "feat: Restore three-tier FROM-TO detection (spatial + vision + geometric)"
    
    Write-Host "Pushing to main..." -ForegroundColor Yellow
    git push origin main
    
    Write-Host "`nLatest commit:" -ForegroundColor Green
    git log -1 --oneline
} else {
    Write-Host "No changes to commit" -ForegroundColor Yellow
    Write-Host "Latest commit:" -ForegroundColor Green
    git log -1 --format="%h - %s (%ar)"
}

# Frontend Push
Write-Host "`n`n=== FRONTEND (airflow_frontend) ===" -ForegroundColor Cyan
cd C:\Users\Abdullah.Khan\airflow_frontend

Write-Host "Checking status..." -ForegroundColor Yellow
$status = git status --porcelain
if ($status) {
    Write-Host "Changes found:" -ForegroundColor Cyan
    git status --short
    
    Write-Host "`nAdding files..." -ForegroundColor Yellow
    git add .
    
    Write-Host "Committing..." -ForegroundColor Yellow
    git commit -m "feat: Add FROM-TO columns to line list display"
    
    Write-Host "Pushing to main..." -ForegroundColor Yellow
    git push origin main
    
    Write-Host "`nLatest commit:" -ForegroundColor Green
    git log -1 --oneline
} else {
    Write-Host "No changes to commit" -ForegroundColor Yellow
    Write-Host "Latest commit:" -ForegroundColor Green
    git log -1 --format="%h - %s (%ar)"
}

Write-Host "`n`n=== DONE ===" -ForegroundColor Green
Write-Host "Press Enter to close..."
Read-Host
