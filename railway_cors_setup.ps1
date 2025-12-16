# Railway Environment Variables Setup Script (PowerShell)
# This script helps you verify and set required environment variables for CORS configuration

Write-Host "======================================" -ForegroundColor Cyan
Write-Host "AIFlow - Railway CORS Setup" -ForegroundColor Cyan
Write-Host "======================================" -ForegroundColor Cyan
Write-Host ""

Write-Host "✅ Required Environment Variables:" -ForegroundColor Green
Write-Host "-----------------------------------"
Write-Host ""

Write-Host "1. FRONTEND_URL" -ForegroundColor Yellow
Write-Host "   Description: Your Vercel frontend deployment URL"
Write-Host "   Example: https://airflow-frontend.vercel.app"
Write-Host "   How to set in Railway Dashboard:"
Write-Host "   - Go to Railway project → Backend service → Variables tab"
Write-Host "   - Add: FRONTEND_URL = https://airflow-frontend.vercel.app"
Write-Host ""

Write-Host "2. BACKEND_URL" -ForegroundColor Yellow
Write-Host "   Description: Your Railway backend deployment URL"
Write-Host "   Example: https://aiflowbackend-production.up.railway.app"
Write-Host "   How to set in Railway Dashboard:"
Write-Host "   - Add: BACKEND_URL = https://aiflowbackend-production.up.railway.app"
Write-Host ""

Write-Host "======================================" -ForegroundColor Cyan
Write-Host "📋 Optional Environment Variables:" -ForegroundColor Cyan
Write-Host "-----------------------------------"
Write-Host ""

Write-Host "3. CORS_ALLOW_VERCEL (Default: true)" -ForegroundColor Yellow
Write-Host "   Allows all *.vercel.app domains automatically"
Write-Host ""

Write-Host "4. CORS_ALLOW_LOCALHOST (Default: true)" -ForegroundColor Yellow
Write-Host "   Allows localhost and 127.0.0.1 for development"
Write-Host ""

Write-Host "5. CORS_ALLOW_ALL_ORIGINS (Default: false)" -ForegroundColor Yellow
Write-Host "   ⚠️  WARNING: NEVER set to true in production!" -ForegroundColor Red
Write-Host ""

Write-Host "======================================" -ForegroundColor Cyan
Write-Host "🚀 How to Set Variables in Railway:" -ForegroundColor Cyan
Write-Host "-----------------------------------"
Write-Host ""

Write-Host "Step 1: Go to Railway Dashboard" -ForegroundColor Green
Write-Host "   https://railway.app/project/your-project-id"
Write-Host ""

Write-Host "Step 2: Select Backend Service" -ForegroundColor Green
Write-Host "   Click on 'aiflow_backend' or your backend service"
Write-Host ""

Write-Host "Step 3: Add Variables" -ForegroundColor Green
Write-Host "   Click 'Variables' tab → 'New Variable'"
Write-Host ""

Write-Host "   Add these variables:" -ForegroundColor Yellow
Write-Host "   ┌────────────────────┬──────────────────────────────────────────────┐"
Write-Host "   │ Variable Name      │ Value                                        │"
Write-Host "   ├────────────────────┼──────────────────────────────────────────────┤"
Write-Host "   │ FRONTEND_URL       │ https://airflow-frontend.vercel.app          │"
Write-Host "   │ BACKEND_URL        │ https://aiflowbackend-production.up.railway  │"
Write-Host "   └────────────────────┴──────────────────────────────────────────────┘"
Write-Host ""

Write-Host "Step 4: Deploy" -ForegroundColor Green
Write-Host "   Railway will automatically redeploy after adding variables"
Write-Host ""

Write-Host "======================================" -ForegroundColor Cyan
Write-Host "🔍 Verify CORS Configuration:" -ForegroundColor Cyan
Write-Host "-----------------------------------"
Write-Host ""

Write-Host "Test OPTIONS request (preflight):" -ForegroundColor Yellow
Write-Host ""
Write-Host 'Invoke-WebRequest -Uri "https://aiflowbackend-production.up.railway.app/api/v1/pid/drawings/upload/" `' -ForegroundColor White
Write-Host '  -Method OPTIONS `' -ForegroundColor White
Write-Host '  -Headers @{' -ForegroundColor White
Write-Host '    "Origin" = "https://airflow-frontend.vercel.app"' -ForegroundColor White
Write-Host '    "Access-Control-Request-Method" = "POST"' -ForegroundColor White
Write-Host '  }' -ForegroundColor White
Write-Host ""

Write-Host "Expected headers in response:" -ForegroundColor Green
Write-Host "  ✓ Access-Control-Allow-Origin: https://airflow-frontend.vercel.app"
Write-Host "  ✓ Access-Control-Allow-Methods: GET, POST, PUT, PATCH, DELETE, OPTIONS, HEAD"
Write-Host "  ✓ Access-Control-Allow-Credentials: true"
Write-Host ""

Write-Host "======================================" -ForegroundColor Cyan
Write-Host "📝 Recommended Settings:" -ForegroundColor Cyan
Write-Host "-----------------------------------"
Write-Host ""

$recommendedSettings = @"
FRONTEND_URL=https://airflow-frontend.vercel.app
BACKEND_URL=https://aiflowbackend-production.up.railway.app
CORS_ALLOW_VERCEL=true
CORS_ALLOW_LOCALHOST=false  (disable in production)
CORS_ALLOW_ALL_ORIGINS=false  (NEVER enable in production)
"@

Write-Host $recommendedSettings -ForegroundColor Yellow
Write-Host ""

Write-Host "======================================" -ForegroundColor Cyan
Write-Host "🐛 Troubleshooting:" -ForegroundColor Cyan
Write-Host "-----------------------------------"
Write-Host ""

Write-Host "If CORS errors persist:" -ForegroundColor Yellow
Write-Host ""

Write-Host "1. Check Railway Logs" -ForegroundColor Green
Write-Host "   - Go to Railway Dashboard > Deployments > Latest deployment > Logs"
Write-Host "   - Look for: [CorsMiddleware] Loaded allowed origins: [...]"
Write-Host "   - Look for: [CorsMiddleware] OPTIONS request from ... - ALLOWED"
Write-Host ""

Write-Host "2. Verify Frontend URL in Vercel" -ForegroundColor Green
Write-Host "   - Go to Vercel Dashboard > Settings > Environment Variables"
Write-Host "   - Ensure: VITE_API_URL = https://aiflowbackend-production.up.railway.app/api/v1"
Write-Host ""

Write-Host "3. Clear Browser Cache" -ForegroundColor Green
Write-Host "   - Hard refresh: Ctrl + Shift + R (Chrome/Edge)"
Write-Host "   - Or: F12 > Network tab > Disable cache checkbox"
Write-Host ""

Write-Host "4. Check Browser Console" -ForegroundColor Green
Write-Host "   - F12 > Console tab"
Write-Host "   - Look for CORS errors or network failures"
Write-Host "   - Check Network tab for failed OPTIONS/POST requests"
Write-Host ""

Write-Host "======================================" -ForegroundColor Cyan
Write-Host "📖 Full Documentation:" -ForegroundColor Cyan
Write-Host "   See: backend/CORS_ENV_SETUP.md"
Write-Host "======================================" -ForegroundColor Cyan
Write-Host ""

Write-Host "✅ Setup complete! Deploy to Railway to apply changes." -ForegroundColor Green
