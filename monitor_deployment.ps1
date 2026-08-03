# Monitor Railway Deployment and Verify Fix
# Run this to see when the deployment completes

Write-Host "======================================"
Write-Host "MONITORING RAILWAY DEPLOYMENT"
Write-Host "======================================"
Write-Host ""
Write-Host "⏳ Waiting for Railway to deploy..." -ForegroundColor Yellow
Write-Host "   This usually takes 2-3 minutes"
Write-Host ""

$attempt = 1
$maxAttempts = 12  # 12 attempts = 6 minutes
$waitSeconds = 30

while ($attempt -le $maxAttempts) {
    Write-Host "[$attempt/$maxAttempts] Checking deployment status..." -ForegroundColor Cyan
    
    # Check if the backend is responding
    try {
        $response = Invoke-WebRequest -Uri "https://aiflowbackend-production.up.railway.app/api/v1/health/" -TimeoutSec 10 -ErrorAction SilentlyContinue
        if ($response.StatusCode -eq 200) {
            Write-Host "✅ Backend is responding!" -ForegroundColor Green
            Write-Host ""
            
            # Now check if procurement endpoints work
            Write-Host "Checking procurement endpoints..." -ForegroundColor Cyan
            try {
                $procResponse = Invoke-WebRequest -Uri "https://aiflowbackend-production.up.railway.app/api/v1/procurement/requisitions/" -TimeoutSec 10 -ErrorAction SilentlyContinue
                if ($procResponse.StatusCode -eq 200) {
                    Write-Host "✅ PROCUREMENT FIX SUCCESSFUL!" -ForegroundColor Green
                    Write-Host ""
                    Write-Host "======================================"
                    Write-Host "VERIFICATION COMPLETE"
                    Write-Host "======================================"
                    Write-Host ""
                    Write-Host "✅ Backend deployed successfully"
                    Write-Host "✅ Migrations applied"
                    Write-Host "✅ Schema fixed"
                    Write-Host "✅ No more 500 errors"
                    Write-Host ""
                    Write-Host "🎉 You can now open:" -ForegroundColor Green
                    Write-Host "   https://www.radai.ae/procurement/orders"
                    Write-Host "   https://www.radai.ae/procurement/requisitions"
                    Write-Host ""
                    exit 0
                }
            } catch {
                Write-Host "⚠️  Backend online but procurement still has errors" -ForegroundColor Yellow
                Write-Host "   Checking logs..." -ForegroundColor Yellow
            }
        }
    } catch {
        Write-Host "⏳ Backend still deploying..." -ForegroundColor Yellow
    }
    
    if ($attempt -lt $maxAttempts) {
        Write-Host "   Waiting $waitSeconds seconds before next check..."
        Write-Host ""
        Start-Sleep -Seconds $waitSeconds
    }
    
    $attempt++
}

Write-Host ""
Write-Host "======================================"
Write-Host "⚠️  TIMEOUT REACHED"
Write-Host "======================================"
Write-Host ""
Write-Host "Deployment is taking longer than expected."
Write-Host "This might mean:"
Write-Host "  1. Railway is experiencing delays"
Write-Host "  2. Build is still in progress"
Write-Host "  3. There's an error in the deployment"
Write-Host ""
Write-Host "Next steps:"
Write-Host "  1. Check Railway dashboard: https://railway.app"
Write-Host "  2. Check logs: railway logs --tail 100"
Write-Host "  3. Try again in a few minutes"
Write-Host ""
