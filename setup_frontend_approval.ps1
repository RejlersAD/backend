# ============================================================================
# SMART FRONTEND APPROVAL PAGE SETUP SCRIPT
# ============================================================================
# This script automatically sets up the approval page in your frontend
# Uses smart intelligence to detect and update files safely
# ============================================================================

Write-Host "`n========================================" -ForegroundColor Cyan
Write-Host "RAD AI Approval Page Setup Script" -ForegroundColor Cyan
Write-Host "========================================`n" -ForegroundColor Cyan

$frontendPath = "C:\Users\Abdullah.Khan\airflow_frontend"
$sourcePath = "C:\Users\Abdullah.Khan\RAD_AI"

# Check if frontend directory exists
if (-not (Test-Path $frontendPath)) {
    Write-Host "❌ Error: Frontend directory not found at $frontendPath" -ForegroundColor Red
    exit 1
}

Write-Host "✓ Frontend directory found" -ForegroundColor Green

# ============================================================================
# STEP 1: Create Finance Pages Directory (if not exists)
# ============================================================================
Write-Host "`n[1/4] Checking Finance pages directory..." -ForegroundColor Yellow

$financeDir = Join-Path $frontendPath "src\pages\Finance"
if (-not (Test-Path $financeDir)) {
    Write-Host "Creating Finance pages directory..." -ForegroundColor Yellow
    New-Item -ItemType Directory -Path $financeDir -Force | Out-Null
}
Write-Host "✓ Finance pages directory ready" -ForegroundColor Green

# ============================================================================
# STEP 2: Copy Approval Page Component
# ============================================================================
Write-Host "`n[2/4] Installing approval page component..." -ForegroundColor Yellow

$sourceFile = Join-Path $sourcePath "FRONTEND_APPROVAL_PAGE.jsx"
$destFile = Join-Path $financeDir "InvoiceApproval.jsx"

if (-not (Test-Path $sourceFile)) {
    Write-Host "❌ Error: Source file not found at $sourceFile" -ForegroundColor Red
    exit 1
}

Copy-Item -Path $sourceFile -Destination $destFile -Force
Write-Host "✓ Approval page component installed" -ForegroundColor Green
Write-Host "  Location: $destFile" -ForegroundColor Gray

# ============================================================================
# STEP 3: Smart App.jsx Route Update
# ============================================================================
Write-Host "`n[3/4] Updating App.jsx routes..." -ForegroundColor Yellow

$appJsxPath = Join-Path $frontendPath "src\App.jsx"

if (-not (Test-Path $appJsxPath)) {
    Write-Host "❌ Error: App.jsx not found at $appJsxPath" -ForegroundColor Red
    exit 1
}

# Read current content
$content = Get-Content $appJsxPath -Raw

# Check if import already exists
if ($content -notmatch "InvoiceApproval") {
    Write-Host "  → Adding InvoiceApproval import..." -ForegroundColor Cyan
    
    # Find the last import from pages/Finance
    if ($content -match "(?s)(import .+ from ['\`"]\.\/pages\/Finance\/.+['\`"];?)") {
        $lastFinanceImport = $matches[1]
        $newImport = "$lastFinanceImport`nimport InvoiceApproval from './pages/Finance/InvoiceApproval';"
        $content = $content -replace [regex]::Escape($lastFinanceImport), $newImport
        Write-Host "  ✓ Import added after existing Finance imports" -ForegroundColor Green
    } else {
        # No Finance imports found, add after react-router-dom imports
        if ($content -match "(?s)(import .+ from ['\`"]react-router-dom['\`"];?)") {
            $lastRouterImport = $matches[1]
            $newImport = "$lastRouterImport`nimport InvoiceApproval from './pages/Finance/InvoiceApproval';"
            $content = $content -replace [regex]::Escape($lastRouterImport), $newImport
            Write-Host "  ✓ Import added after router imports" -ForegroundColor Green
        } else {
            Write-Host "  ⚠ Warning: Could not find suitable import location" -ForegroundColor Yellow
            Write-Host "  Please manually add: import InvoiceApproval from './pages/Finance/InvoiceApproval';" -ForegroundColor Yellow
        }
    }
} else {
    Write-Host "  ✓ InvoiceApproval import already exists" -ForegroundColor Green
}

# Check if route already exists
$routePattern = 'path="/finance/approve/:token"'
if ($content -notmatch [regex]::Escape($routePattern)) {
    Write-Host "  → Adding approval route..." -ForegroundColor Cyan
    
    # Find Finance routes section closing
    if ($content -match '(?s)path="/finance"[^>]*>.*?</Route>') {
        $financeRoutesBlock = $matches[0]
        
        # Create new route string
        $approvalComment = "`n`n      {/* Approval Page - Token-based access */}"
        $approvalRoute = "`n      " + '<Route path="/finance/approve/:token" element={<InvoiceApproval />} />'
        $newRoute = $financeRoutesBlock + $approvalComment + $approvalRoute
        
        $content = $content -replace [regex]::Escape($financeRoutesBlock), $newRoute
        Write-Host "  ✓ Route added after Finance section" -ForegroundColor Green
    } else {
        Write-Host "  ⚠ Warning: Could not find Finance routes section" -ForegroundColor Yellow
        Write-Host '  Please manually add: <Route path="/finance/approve/:token" element={<InvoiceApproval />} />' -ForegroundColor Yellow
    }
} else {
    Write-Host "  ✓ Approval route already exists" -ForegroundColor Green
}

# Save updated content
Set-Content -Path $appJsxPath -Value $content -NoNewline
Write-Host "✓ App.jsx updated successfully" -ForegroundColor Green

# ============================================================================
# STEP 4: Verify Installation
# ============================================================================
Write-Host "`n[4/4] Verifying installation..." -ForegroundColor Yellow

$verifyChecks = @{
    "Approval component file" = Test-Path $destFile
    "InvoiceApproval import" = $content -match "InvoiceApproval"
    "Approval route" = $content -match "approve/:token"
}

$allPassed = $true
foreach ($check in $verifyChecks.GetEnumerator()) {
    if ($check.Value) {
        Write-Host "  ✓ $($check.Key)" -ForegroundColor Green
    } else {
        Write-Host "  ❌ $($check.Key)" -ForegroundColor Red
        $allPassed = $false
    }
}

# ============================================================================
# FINAL SUMMARY
# ============================================================================
Write-Host "`n========================================" -ForegroundColor Cyan
Write-Host "SETUP COMPLETE!" -ForegroundColor Cyan
Write-Host "========================================`n" -ForegroundColor Cyan

if ($allPassed) {
    Write-Host "✅ All components installed successfully!`n" -ForegroundColor Green
    
    Write-Host "📋 What was done:" -ForegroundColor Yellow
    Write-Host "  1. Created/verified Finance pages directory" -ForegroundColor White
    Write-Host "  2. Copied InvoiceApproval.jsx component" -ForegroundColor White
    Write-Host "  3. Added import to App.jsx" -ForegroundColor White
    Write-Host "  4. Added route to App.jsx" -ForegroundColor White
    
    Write-Host "`n🚀 Next Steps:" -ForegroundColor Yellow
    Write-Host "  1. Start frontend: cd ..\airflow_frontend && npm run dev" -ForegroundColor Cyan
    Write-Host "  2. Upload a test invoice" -ForegroundColor Cyan
    Write-Host "  3. Check email and click 'Review & Approve in RAD AI'" -ForegroundColor Cyan
    Write-Host "  4. Approval form will open at: http://localhost:5173/finance/approve/{token}`n" -ForegroundColor Cyan
    
    Write-Host "✨ System is ready for testing!" -ForegroundColor Green
} else {
    Write-Host "⚠️ Some checks failed. Please review the output above.`n" -ForegroundColor Yellow
    Write-Host "You may need to manually add missing components." -ForegroundColor Yellow
}

Write-Host "`n========================================`n" -ForegroundColor Cyan
