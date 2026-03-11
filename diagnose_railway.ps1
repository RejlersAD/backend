# Railway Backend Diagnostic Script
# ==================================
Write-Host "`n==================================" -ForegroundColor Cyan
Write-Host "🔍 RAILWAY BACKEND DIAGNOSTICS" -ForegroundColor Cyan
Write-Host "==================================`n" -ForegroundColor Cyan

# Test 1: Check if backend is responding
Write-Host "1️⃣  Testing backend endpoint..." -ForegroundColor Yellow
try {
    $response = Invoke-WebRequest -Uri "https://aiflow-backend-production.up.railway.app/admin/" `
        -Method HEAD -TimeoutSec 10 -UseBasicParsing -ErrorAction Stop
    Write-Host "   ✅ Backend responding! Status: $($response.StatusCode)" -ForegroundColor Green
} catch {
    Write-Host "   ❌ Backend not responding: $($_.Exception.Message)" -ForegroundColor Red
    $backendDown = $true
}

# Test 2: Check latest commit
Write-Host "`n2️⃣  Checking latest deployment..." -ForegroundColor Yellow
$latestCommit = git log -1 --oneline
Write-Host "   Latest: $latestCommit" -ForegroundColor Gray

# Test 3: Check Railway service status (if CLI available)
Write-Host "`n3️⃣  Checking Railway CLI..." -ForegroundColor Yellow
try {
    $railwayVersion = railway --version 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-Host "   ✅ Railway CLI available: $railwayVersion" -ForegroundColor Green
        
        Write-Host "`n   📋 Getting Railway logs (last 50 lines)..." -ForegroundColor Cyan
        Write-Host "   " -NoNewline
        railway logs --service aiflow-backend-production 2>&1 | Select-Object -Last 50
    } else {
        Write-Host "   ⚠️  Railway CLI not installed" -ForegroundColor Yellow
        Write-Host "   Install: npm i -g @railway/cli" -ForegroundColor Gray
    }
} catch {
    Write-Host "   ⚠️  Railway CLI not available" -ForegroundColor Yellow
    Write-Host "   Install with: npm i -g @railway/cli" -ForegroundColor Gray
    Write-Host "   Then run: railway login" -ForegroundColor Gray
}

# Test 4: Check database connection from local
Write-Host "`n4️⃣  Testing database connectivity..." -ForegroundColor Yellow
$dbHost = "shinkansen.proxy.rlwy.net"
$dbPort = 38534
try {
    $tcpClient = New-Object System.Net.Sockets.TcpClient
    $tcpClient.Connect($dbHost, $dbPort)
    Write-Host "   ✅ Database endpoint reachable ($dbHost:$dbPort)" -ForegroundColor Green
    $tcpClient.Close()
} catch {
    Write-Host "   ❌ Cannot reach database: $($_.Exception.Message)" -ForegroundColor Red
}

# Test 5: Check frontend status
Write-Host "`n5️⃣  Testing frontend..." -ForegroundColor Yellow
try {
    $frontendResponse = Invoke-WebRequest -Uri "https://www.radai.ae" `
        -Method HEAD -TimeoutSec 10 -UseBasicParsing -ErrorAction Stop
    Write-Host "   ✅ Frontend responding! Status: $($frontendResponse.StatusCode)" -ForegroundColor Green
} catch {
    Write-Host "   ❌ Frontend issue: $($_.Exception.Message)" -ForegroundColor Red
}

# Summary and recommendations
Write-Host "`n==================================`n" -ForegroundColor Cyan
if ($backendDown) {
    Write-Host "🚨 BACKEND IS DOWN - Next Steps:" -ForegroundColor Red
    Write-Host "`n1. Check Railway logs:" -ForegroundColor Yellow
    Write-Host "   - Go to https://railway.app" -ForegroundColor Gray
    Write-Host "   - Select 'aiflow-backend-production' service" -ForegroundColor Gray
    Write-Host "   - Check 'Deploy Logs' tab for errors`n" -ForegroundColor Gray
    
    Write-Host "2. Look for these errors in logs:" -ForegroundColor Yellow
    Write-Host "   - Database connection errors" -ForegroundColor Gray
    Write-Host "   - Migration failures" -ForegroundColor Gray
    Write-Host "   - Module import errors" -ForegroundColor Gray
    Write-Host "   - Gunicorn startup failures`n" -ForegroundColor Gray
    
    Write-Host "3. Common fixes:" -ForegroundColor Yellow
    Write-Host "   - Verify DATABASE_URL environment variable" -ForegroundColor Gray
    Write-Host "   - Check if migration fixer ran successfully" -ForegroundColor Gray
    Write-Host "   - Ensure all required files are committed`n" -ForegroundColor Gray
} else {
    Write-Host "✅ BACKEND IS ONLINE!" -ForegroundColor Green
    Write-Host "Try logging in at: https://www.radai.ae/login`n" -ForegroundColor Cyan
}
