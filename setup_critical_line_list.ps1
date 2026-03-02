# PowerShell script to set up Critical Line List page

$frontendPath = "C:\Users\Abdullah.Khan\airflow_frontend"

Write-Host "🔧 Setting up Critical Line List page..." -ForegroundColor Cyan

# 1. Add import to App.jsx
Write-Host "📝 Adding import to App.jsx..." -ForegroundColor Yellow
$appJsxPath = "$frontendPath\src\App.jsx"
$appContent = Get-Content $appJsxPath -Raw

# Add import after LineList import
if ($appContent -notmatch "CriticalLineList") {
    $appContent = $appContent -replace "(import LineList from.*?\r?\n)", "`$1const CriticalLineList = React.lazy(() => import('./pages/Engineering/Piping/CriticalLineList'));`n"
    Set-Content -Path $appJsxPath -Value $appContent -NoNewline
    Write-Host "✅ Import added" -ForegroundColor Green
} else {
    Write-Host "⏭️ Import already exists" -ForegroundColor Gray
}

# 2. Add route to App.jsx
Write-Host "📝 Adding route to App.jsx..." -ForegroundColor Yellow
$appContent = Get-Content $appJsxPath -Raw

if ($appContent -notmatch "/engineering/piping/critical-line-list") {
    # Find the engineering routes section and add our route
    $appContent = $appContent -replace "(<Route path=`"/engineering/process/line-list`" element=\{<LineList />\} />)", "`$1`n                <Route path=`"/engineering/piping/critical-line-list`" element={<CriticalLineList />} />"
    Set-Content -Path $appJsxPath -Value $appContent -NoNewline
    Write-Host "✅ Route added" -ForegroundColor Green
} else {
    Write-Host "⏭️ Route already exists" -ForegroundColor Gray
}

# 3. Update engineeringStructure.config.js
Write-Host "📝 Updating engineering structure config..." -ForegroundColor Yellow
$configPath = "$frontendPath\src\config\engineeringStructure.config.js"
$configContent = Get-Content $configPath -Raw

if ($configContent -notmatch "critical-line-list") {
    # Add Piping section after Process section
    $pipingSection = @"
  {
    id: 'piping',
    name: 'Piping',
    path: '/engineering/piping',
    icon: '🔧',
    description: 'Piping engineering and critical line management',
    subFeatures: [
      {
        id: 'critical-line-list',
        name: 'Critical Line List',
        path: '/engineering/piping/critical-line-list',
        icon: '📋',
        description: 'Upload P&ID + enrichment docs for full 35-column extraction',
        tags: ['piping', 'critical', 'enrichment', '5-documents']
      }
    ]
  },
"@
    
    # Insert after Process section
    $configContent = $configContent -replace "(\s+\},\s+\{\s+id: 'utilities',)", "$pipingSection`n  },`n  {`n    id: 'utilities',"
    Set-Content -Path $configPath -Value $configContent -NoNewline
    Write-Host "✅ Config updated" -ForegroundColor Green
} else {
    Write-Host "⏭️ Config already has critical-line-list" -ForegroundColor Gray
}

Write-Host "`n✨ Setup complete!" -ForegroundColor Green
Write-Host "📍 New page available at: http://localhost:5173/engineering/piping/critical-line-list" -ForegroundColor Cyan
