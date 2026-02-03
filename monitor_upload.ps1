# Real-time upload monitor
Write-Host "`n========================================" -ForegroundColor Cyan
Write-Host "  REAL-TIME FROM-TO VISION MONITOR" -ForegroundColor Green
Write-Host "========================================`n" -ForegroundColor Cyan

Write-Host "Waiting for P&ID upload..." -ForegroundColor Yellow
Write-Host "Monitoring logs for:" -ForegroundColor White
Write-Host "  - PHASE 3A (Vision)" -ForegroundColor Gray
Write-Host "  - OpenAI API calls" -ForegroundColor Gray
Write-Host "  - FROM-TO results" -ForegroundColor Gray
Write-Host "  - Errors/failures`n" -ForegroundColor Gray

Write-Host "Press Ctrl+C to stop`n" -ForegroundColor Red

# Start log monitoring
docker logs -f aiflow_backend 2>&1 | Select-String "PHASE|Vision|FROM-TO|gpt-4|ERROR|❌|✅"
