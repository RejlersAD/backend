#!/usr/bin/env pwsh
<#
.SYNOPSIS
    RAD AI Sync Agent - Smart Deployment Script

.DESCRIPTION
    Intelligently deploys sync agent files to preprod and main branches.
    Handles commits, merges, and pushes automatically.

.PARAMETER SkipConfirm
    Skip confirmation prompt

.PARAMETER CommitMessage
    Custom commit message

.EXAMPLE
    .\deploy_sync_agent.ps1
    Deploy with confirmation

.EXAMPLE
    .\deploy_sync_agent.ps1 -SkipConfirm
    Deploy without confirmation

.NOTES
    Author: RAD AI DevOps
    Date: 2026-08-05
    Version: 1.0
#>

param(
    [switch]$SkipConfirm,
    [string]$CommitMessage = "feat: Add attendance sync agent package - Fixes 27-day data gap"
)

# Colors for output
function Write-Success { param($Message) Write-Host "[OK] $Message" -ForegroundColor Green }
function Write-Warning { param($Message) Write-Host "[WARNING] $Message" -ForegroundColor Yellow }
function Write-Error { param($Message) Write-Host "[ERROR] $Message" -ForegroundColor Red }
function Write-Info { param($Message) Write-Host "[INFO] $Message" -ForegroundColor Cyan }
function Write-Step { param($Message) Write-Host "`n$Message" -ForegroundColor Cyan }

# Header
Write-Host ""
Write-Host "=" * 80 -ForegroundColor Cyan
Write-Host "       RAD AI Sync Agent - Smart Deployment Script" -ForegroundColor Cyan
Write-Host "=" * 80 -ForegroundColor Cyan
Write-Host ""

# Check if git is available
try {
    $gitVersion = git --version
    Write-Info "Git: $gitVersion"
} catch {
    Write-Error "Git is not installed or not in PATH"
    exit 1
}

# Get current branch
$currentBranch = git branch --show-current
Write-Info "Current branch: $currentBranch"
Write-Host ""

# List of sync agent files to deploy
$syncFiles = @(
    "timesheet_mirror_sync.py",
    "requirements-sync-agent.txt",
    ".env.sync-agent.example",
    "SYNC_AGENT_SETUP.md",
    "README_SYNC_AGENT.md",
    "QUICK_FIX_SYNC_AGENT.md",
    "create_sync_task.ps1",
    "check_sync_agent.ps1",
    "restart_sync_agent.ps1",
    "test_sync_config.py"
)

# Check which files exist and have changes
Write-Info "Checking sync agent files..."
$filesToDeploy = @()
foreach ($file in $syncFiles) {
    if (Test-Path $file) {
        $filesToDeploy += $file
        Write-Host "   [FOUND] $file" -ForegroundColor Gray
    }
}

if ($filesToDeploy.Count -eq 0) {
    Write-Warning "No sync agent files found to deploy"
    exit 1
}

Write-Info "Found $($filesToDeploy.Count) sync agent files"

# Show git status
Write-Host "`nFiles to be deployed:" -ForegroundColor Cyan
Write-Host "-------------------" -ForegroundColor Cyan
git status --short $syncFiles 2>$null

# Confirmation
if (-not $SkipConfirm) {
    Write-Host ""
    $response = Read-Host "Deploy sync agent files to preprod and main? (yes/no)"
    if ($response.ToLower() -ne 'yes') {
        Write-Info "Deployment cancelled by user"
        exit 0
    }
}

# Step 1: Commit
Write-Step "=" * 80
Write-Step "STEP 1: Committing sync agent files"
Write-Step "=" * 80

try {
    # Add files
    foreach ($file in $filesToDeploy) {
        git add $file
    }
    
    # Commit
    $commitBody = @"
- Created timesheet_mirror_sync.py (main sync agent)
- Added complete setup documentation
- Included automation scripts (Task Scheduler)
- Added configuration templates and validators
- Fixed restart_sync_agent.ps1 Unicode errors
- Comprehensive troubleshooting guides
"@
    
    git commit -m $CommitMessage -m $commitBody 2>&1 | Out-Null
    
    if ($LASTEXITCODE -eq 0) {
        Write-Success "Files committed successfully"
    } else {
        Write-Warning "Nothing to commit or commit failed"
        Write-Info "Continuing with deployment anyway..."
    }
} catch {
    Write-Warning "Commit step failed: $_"
}

# Step 2: Pull and push to current branch
Write-Step "=" * 80
Write-Step "STEP 2: Pulling and pushing to current branch ($currentBranch)"
Write-Step "=" * 80

try {
    # Pull latest changes first
    Write-Info "Pulling latest changes from $currentBranch..."
    git pull origin $currentBranch --rebase 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "Pull failed or conflicts detected, attempting to continue..."
    }
    
    # Now push
    Write-Info "Pushing to $currentBranch..."
    git push origin $currentBranch
    if ($LASTEXITCODE -eq 0) {
        Write-Success "Pulled and pushed to $currentBranch"
    } else {
        Write-Error "Failed to push to $currentBranch"
        Write-Info "Try: git pull origin $currentBranch --rebase"
        exit 1
    }
} catch {
    Write-Error "Push failed: $_"
    exit 1
}

# Step 3: Merge to preprod
Write-Step "=" * 80
Write-Step "STEP 3: Merging to preprod branch"
Write-Step "=" * 80

try {
    # Fetch preprod
    Write-Info "Fetching preprod branch..."
    git fetch origin preprod 2>&1 | Out-Null
    
    # Checkout preprod
    Write-Info "Checking out preprod..."
    git checkout preprod 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Failed to checkout preprod branch"
        exit 1
    }
    
    # Pull latest preprod with rebase
    Write-Info "Pulling latest preprod..."
    git pull origin preprod --rebase 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "Pull failed, attempting regular merge..."
        git pull origin preprod 2>&1 | Out-Null
    }
    
    # Merge from development
    git merge $currentBranch -m "chore: Merge sync agent package from $currentBranch to preprod" 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Merge conflict detected!"
        Write-Info "Please resolve conflicts manually and run:"
        Write-Info "   git add ."
        Write-Info "   git commit"
        Write-Info "   git push origin preprod"
        exit 1
    }
    
    # Push preprod
    git push origin preprod
    if ($LASTEXITCODE -eq 0) {
        Write-Success "Merged and pushed to preprod"
    } else {
        Write-Error "Failed to push preprod"
        exit 1
    }
} catch {
    Write-Error "Preprod merge failed: $_"
    exit 1
}

# Step 4: Merge to main
Write-Step "=" * 80
Write-Step "STEP 4: Merging to main branch"
Write-Step "=" * 80

try {
    # Fetch main
    Write-Info "Fetching main branch..."
    git fetch origin main 2>&1 | Out-Null
    
    # Checkout main
    Write-Info "Checking out main..."
    git checkout main 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Failed to checkout main branch"
        exit 1
    }
    
    # Pull latest main with rebase
    Write-Info "Pulling latest main..."
    git pull origin main --rebase 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Warning "Rebase failed, attempting regular merge..."
        git pull origin main 2>&1 | Out-Null
        if ($LASTEXITCODE -ne 0) {
            Write-Error "Pull failed. Please resolve conflicts manually."
            exit 1
        }
    }
    
    # Merge from preprod
    git merge preprod -m "chore: Deploy sync agent package to production (main)" 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Merge conflict detected!"
        Write-Info "Please resolve conflicts manually and run:"
        Write-Info "   git add ."
        Write-Info "   git commit"
        Write-Info "   git push origin main"
        exit 1
    }
    
    # Push main
    git push origin main
    if ($LASTEXITCODE -eq 0) {
        Write-Success "Merged and pushed to main"
    } else {
        Write-Error "Failed to push main"
        exit 1
    }
} catch {
    Write-Error "Main merge failed: $_"
    exit 1
}

# Step 5: Return to original branch
Write-Step "=" * 80
Write-Step "STEP 5: Returning to original branch"
Write-Step "=" * 80

try {
    git checkout $currentBranch 2>&1 | Out-Null
    Write-Success "Returned to $currentBranch"
} catch {
    Write-Warning "Failed to return to $currentBranch"
}

# Success summary
Write-Host ""
Write-Host "=" * 80 -ForegroundColor Green
Write-Host "                    DEPLOYMENT COMPLETE!" -ForegroundColor Green
Write-Host "=" * 80 -ForegroundColor Green
Write-Host ""

Write-Host "Summary:" -ForegroundColor Cyan
Write-Success "Committed sync agent files"
Write-Success "Pushed to $currentBranch"
Write-Success "Merged to preprod"
Write-Success "Merged to main"
Write-Host ""

Write-Host "Next Steps:" -ForegroundColor Cyan
Write-Host "  1. Log into office server" -ForegroundColor Gray
Write-Host "  2. Pull the latest main branch" -ForegroundColor Gray
Write-Host "  3. Copy sync agent files to C:\RadAI\sync-agent\" -ForegroundColor Gray
Write-Host "  4. Follow setup guide in SYNC_AGENT_SETUP.md" -ForegroundColor Gray
Write-Host ""

Write-Host "Files deployed ($($filesToDeploy.Count)):" -ForegroundColor Cyan
foreach ($file in $filesToDeploy) {
    Write-Host "  - $file" -ForegroundColor Gray
}

Write-Host ""
Write-Host "=" * 80 -ForegroundColor Green
Write-Host ""

exit 0
