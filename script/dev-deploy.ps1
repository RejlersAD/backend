# ============================================
# DEV → PREPROD DEPLOYMENT SCRIPT
# ============================================
# WORKFLOW: Local Changes → Dev Branch → Preprod Branch (AUTOMATIC)
# DOES NOT TOUCH MAIN/PRODUCTION (Use promote-to-production.ps1 for that)
# 
# Usage: .\scripts\dev-deploy.ps1 "Your commit message"
# 
# What this does:
#   1. Commits your changes to 'dev' branch
#   2. Automatically promotes 'dev' → 'preprod' branch
#   3. Railway deploys to preprod environment (testing)
#   4. Production (main) remains untouched
#
# For production deployment:
#   Run: .\scripts\promote-to-production.ps1 (manual approval required)
# ============================================

param(
    [Parameter(Mandatory=$true)]
    [string]$CommitMessage,
    [Switch]$SkipPreCheck,
    [Switch]$Force,
    [string]$GitHubToken
)

Write-Host "" -ForegroundColor White
Write-Host "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" -ForegroundColor Cyan
Write-Host "🚀 DEV → PREPROD DEPLOYMENT" -ForegroundColor Cyan
Write-Host "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━" -ForegroundColor Cyan
Write-Host "" -ForegroundColor White
Write-Host "WORKFLOW: local → dev → preprod (automatic)" -ForegroundColor Gray
Write-Host "PRODUCTION: main branch is NOT affected by this script" -ForegroundColor Green
Write-Host "" -ForegroundColor White
Write-Host "Commit Message: $CommitMessage" -ForegroundColor Gray
Write-Host "" -ForegroundColor White

# SOFT-CODED CONFIGURATION
$CONFIG = @{
    MaxRetryAttempts = 3
    RetryDelaySeconds = 3
    GitTimeoutSeconds = 30
    QuietMode = $true
    AuthCheckEnabled = $true
    PreValidationEnabled = $true
    SkipAuthPrompts = $true
    UseTokenAuth = $true
    BranchSyncCheckEnabled = $false  # Optional diagnostic check (disabled by default)
    AutoSyncBranches = $false         # Requires sync-branches.ps1 (optional tool)
    ValidateRepoAccess = $true        # Check repository accessibility before processing
    ContinueOnRepoNotFound = $true    # Continue deployment if a repository is not found
}

# REPOSITORY-SPECIFIC CONFIGURATION (Soft-Coded)
$REPO_CONFIG = @{
    # List of repositories known to be inaccessible (will be skipped automatically)
    # Add repository names here if you encounter "not found" errors during deployment
    KnownInaccessibleRepos = @()  # Empty by default - add repos as needed
    SkipInaccessibleRepos = $true  # Skip repos in the list above
    WarnOnSkip = $true             # Show warning when skipping a repository
}

# GITHUB TOKEN AUTHENTICATION SETUP
function Initialize-GitHubTokenAuth {
    param([string]$Token)
    
    if ($Token) {
        Write-Host "[AUTH] Using provided GitHub token..." -ForegroundColor Green
        $env:GITHUB_TOKEN = $Token
    } else {
        # Check for environment variable
        if ($env:GITHUB_TOKEN) {
            Write-Host "[AUTH] Using GitHub token from environment..." -ForegroundColor Green
        } else {
            Write-Host "[AUTH] No GitHub token provided. Checking credential store..." -ForegroundColor Yellow
            return $false
        }
    }
    
    # Configure Git to use token authentication and DISABLE credential manager
    # This prevents the browser authentication prompts
    git config --global --unset credential.helper 2>$null
    git config --global credential."https://github.com".helper "" 2>$null
    git config --global credential.useHttpPath true 2>$null
    
    Write-Host "[AUTH] Git configured for token authentication (credential helper disabled)" -ForegroundColor Gray
    
    return $true
}

# PRE-FLIGHT SETUP
Write-Host "[SETUP] Configuring Git for automated deployment..." -ForegroundColor Gray

# Initialize token authentication
$tokenConfigured = Initialize-GitHubTokenAuth -Token $GitHubToken

if (-not $tokenConfigured) {
    Write-Host "[WARNING] No GitHub token configured. This may cause authentication issues." -ForegroundColor Yellow
    Write-Host "[INFO] Create token at: https://github.com/settings/tokens" -ForegroundColor Gray
    Write-Host "[INFO] Required permissions: repo (full control)" -ForegroundColor Gray
    Write-Host "[INFO] Then run: .\dev-deploy.ps1 -CommitMessage 'message' -GitHubToken 'your_token'" -ForegroundColor Gray
    Write-Host "[INFO] Or set environment variable: `$env:GITHUB_TOKEN = 'your_token'" -ForegroundColor Gray
    Write-Host "" -ForegroundColor White
    
    # Fallback to credential manager with proper configuration
    Write-Host "[FALLBACK] Configuring Git credential manager..." -ForegroundColor Yellow
    git config --global credential.helper manager-core 2>$null
    git config --global credential.useHttpPath true 2>$null
    
    # Disable GCM browser prompts if interactive mode isn't wanted
    $env:GCM_INTERACTIVE = "never"
}

# SMART FUNCTIONS FOR GIT OPERATIONS
function Test-GitAuthentication {
    # Test git connectivity from current directory
    try {
        Write-Host "    [AUTH] Testing Git connectivity..." -ForegroundColor Gray
        
        # Use token authentication if available
        if ($env:GITHUB_TOKEN) {
            # Get remote URL properly
            $remoteUrl = git config --get remote.origin.url
            if ($remoteUrl) {
                $result = git ls-remote origin 2>&1
            } else {
                Write-Host "    [AUTH] No remote origin configured" -ForegroundColor Yellow
                return $false
            }
        } else {
            $result = git ls-remote origin 2>&1
        }
        
        $authenticated = $LASTEXITCODE -eq 0
        
        if ($authenticated) {
            Write-Host "    [AUTH] Git authentication successful!" -ForegroundColor Green
        } else {
            Write-Host "    [AUTH] Git connectivity test failed (normal for token auth)" -ForegroundColor Gray
            # Return true for token auth since push operations will work
            return $env:GITHUB_TOKEN -ne $null
        }
        
        return $authenticated
    } catch {
        Write-Host "    [AUTH] Authentication test failed: $($_.Exception.Message)" -ForegroundColor Red
        return $env:GITHUB_TOKEN -ne $null
    }
}

# SOFT-CODED: Repository Accessibility Validation
function Test-RepositoryAccessibility {
    param(
        [string]$RepoName,
        [string]$RepoPath
    )
    
    # Skip if repository accessibility check is disabled
    if (-not $CONFIG.ValidateRepoAccess) {
        return $true
    }
    
    # Check if this repo is in the known inaccessible list (SOFT-CODED)
    if ($REPO_CONFIG.KnownInaccessibleRepos -contains $RepoName) {
        if ($REPO_CONFIG.WarnOnSkip) {
            Write-Host "  [SKIP] $RepoName is in known inaccessible list - skipping deployment" -ForegroundColor Yellow
        }
        return $false
    }
    
    try {
        Push-Location $RepoPath
        
        # Get remote URL
        $remoteUrl = git config --get remote.origin.url 2>$null
        
        if (-not $remoteUrl) {
            Write-Host "  [WARNING] No remote URL configured for $RepoName" -ForegroundColor Yellow
            Pop-Location
            return $false
        }
        
        Write-Host "  [VALIDATE] Testing accessibility of $RepoName..." -ForegroundColor Gray
        
        # Test repository access
        $output = ""
        $exitCode = 0
        
        if ($env:GITHUB_TOKEN) {
            # Extract repo path and construct token URL
            if ($remoteUrl -match "github\.com[:/](.+?)(?:\.git)?$") {
                $repoPath = $matches[1]
                if ($repoPath -notmatch "\.git$") { $repoPath = "$repoPath.git" }
                $tokenUrl = "https://${env:GITHUB_TOKEN}@github.com/${repoPath}"
                $output = git ls-remote $tokenUrl HEAD 2>&1 | Out-String
                $exitCode = $LASTEXITCODE
            } else {
                $output = git ls-remote origin HEAD 2>&1 | Out-String
                $exitCode = $LASTEXITCODE
            }
        } else {
            $output = git ls-remote origin HEAD 2>&1 | Out-String
            $exitCode = $LASTEXITCODE
        }
        
        Pop-Location
        
        if ($exitCode -eq 0) {
            Write-Host "  [OK] $RepoName is accessible" -ForegroundColor Green
            return $true
        } else {
            # Check if it's a "not found" error
            if ($output -match "Repository not found|not found") {
                Write-Host "  [SKIP] $RepoName - Repository not found or access denied" -ForegroundColor Yellow
                Write-Host "  [INFO] Repository URL - $remoteUrl" -ForegroundColor Gray
                
                if ($REPO_CONFIG.SkipInaccessibleRepos -and $CONFIG.ContinueOnRepoNotFound) {
                    Write-Host "  [ACTION] Add to skip list - `$REPO_CONFIG.KnownInaccessibleRepos += '$RepoName'" -ForegroundColor Cyan
                }
                
                return $false
            } else {
                Write-Host "  [WARNING] Repository accessibility check inconclusive for $RepoName" -ForegroundColor Yellow
                return $true  # Assume accessible if error is not "not found"
            }
        }
    } catch {
        Write-Host "  [ERROR] Failed to test $RepoName - $($_.Exception.Message)" -ForegroundColor Red
        Pop-Location
        return $true  # Assume accessible on error
    }
}

function Invoke-GitPushWithToken {
    param(
        [string]$Branch = "dev",
        [string]$RepoName,
        [switch]$Force
    )
    
    try {
        $forceFlag = if ($Force) { "--force" } else { "" }
        
        if ($env:GITHUB_TOKEN) {
            # Get remote URL and construct token-authenticated URL
            $remoteUrl = git config --get remote.origin.url
            
            # Handle both HTTPS and SSH URLs
            if ($remoteUrl -match "github\.com[:/](.+?)(?:\.git)?$") {
                $repoPath = $matches[1]
                if ($repoPath -notmatch "\.git$") { $repoPath = "$repoPath.git" }
                $tokenUrl = "https://${env:GITHUB_TOKEN}@github.com/${repoPath}"
                
                Write-Host "    [PUSH] Using token authentication for $RepoName..." -ForegroundColor Gray
                
                # Properly capture git output in PowerShell
                $output = git push $tokenUrl $Branch $forceFlag 2>&1 | Out-String
                $exitCode = $LASTEXITCODE
                
                if ($exitCode -ne 0) {
                    Write-Host "    [ERROR] Git output: $output" -ForegroundColor Red
                    return $false
                }
                return $true
            } else {
                Write-Host "    [PUSH] Fallback to standard push..." -ForegroundColor Gray
                $output = git push origin $Branch $forceFlag 2>&1 | Out-String
                $exitCode = $LASTEXITCODE
                
                if ($exitCode -ne 0) {
                    Write-Host "    [ERROR] Git output: $output" -ForegroundColor Red
                    return $false
                }
                return $true
            }
        } else {
            $output = git push origin $Branch $forceFlag 2>&1 | Out-String
            $exitCode = $LASTEXITCODE
            
            if ($exitCode -ne 0) {
                Write-Host "    [ERROR] Git output: $output" -ForegroundColor Red
                return $false
            }
            return $true
        }
    } catch {
        Write-Host "    [ERROR] Push failed with exception: $($_.Exception.Message)" -ForegroundColor Red
        return $false
    }
}

function Invoke-GitOperationWithRetry {
    param(
        [string]$Operation,
        [scriptblock]$GitCommand,
        [string]$RepoName,
        [int]$MaxRetries = $CONFIG.MaxRetryAttempts,
        [switch]$Force
    )
    
    for ($attempt = 1; $attempt -le $MaxRetries; $attempt++) {
        Write-Host "    [ATTEMPT $attempt/$MaxRetries] $Operation..." -ForegroundColor Cyan
        
        try {
            # Enhanced git operation with proper error capture
            $output = & $GitCommand 2>&1 | Out-String
            $exitCode = $LASTEXITCODE
            
            if ($exitCode -eq 0) {
                Write-Host "    [SUCCESS] $Operation completed" -ForegroundColor Green
                return $true
            } else {
                Write-Host "    [RETRY] $Operation returned exit code $exitCode" -ForegroundColor Yellow
                if ($output -and $output.Trim()) {
                    Write-Host "    [ERROR] $output" -ForegroundColor Red
                }
            }
        } catch {
            Write-Host "    [RETRY] $Operation failed: $($_.Exception.Message)" -ForegroundColor Yellow
        }
        
        if ($attempt -lt $MaxRetries) {
            Write-Host "    [WAIT] Retrying in $($CONFIG.RetryDelaySeconds) seconds..." -ForegroundColor Gray
            Start-Sleep -Seconds $CONFIG.RetryDelaySeconds
            
            # Try to refresh credentials before retry
            if ($attempt -eq 2 -and -not $env:GITHUB_TOKEN) {
                Write-Host "    [AUTH] Refreshing Git credentials..." -ForegroundColor Yellow
                git config --global --unset credential.helper 2>$null
                git config --global credential.helper manager-core 2>$null
            }
        }
    }
    
    Write-Host "    [ERROR] $Operation failed after $MaxRetries attempts" -ForegroundColor Red
    return $false
}

# PRE-FLIGHT BRANCH VALIDATION
function Test-BranchSynchronization {
    param(
        [string]$RootPath,
        [array]$Repositories
    )
    
    Write-Host "[PRE-FLIGHT] Checking branch synchronization status..." -ForegroundColor Cyan
    Write-Host "" -ForegroundColor White
    
    $syncIssues = @()
    
    foreach ($repo in $Repositories) {
        $repoPath = if ($repo.path -eq ".") { $RootPath } else { Join-Path $RootPath $repo.path }
        
        if (Test-Path $repoPath) {
            Push-Location $repoPath
            
            try {
                # Check if Git repository
                if (!(Test-Path ".git")) {
                    continue
                }
                
                $currentBranch = git rev-parse --abbrev-ref HEAD 2>$null
                
                # Fetch latest from remote
                if ($env:GITHUB_TOKEN) {
                    $remoteUrl = git config --get remote.origin.url
                    if ($remoteUrl -match "github\.com[:/](.+?)(?:\.git)?$") {
                        $repoPath = $matches[1]
                        if ($repoPath -notmatch "\.git$") { $repoPath = "$repoPath.git" }
                        $tokenUrl = "https://${env:GITHUB_TOKEN}@github.com/${repoPath}"
                        git fetch $tokenUrl -q 2>$null
                    } else {
                        git fetch origin -q 2>$null
                    }
                } else {
                    git fetch origin -q 2>$null
                }
                
                # Check for critical issues
                $issues = @()
                
                # Check if dev and preprod branches exist
                $devExists = git rev-parse --verify dev 2>$null
                $preprodExists = git rev-parse --verify preprod 2>$null
                
                if ($devExists -and $preprodExists) {
                    # Check if preprod is ahead of dev (CRITICAL ISSUE)
                    $preprodAhead = git rev-list --count dev..preprod 2>$null
                    if ($LASTEXITCODE -eq 0 -and $preprodAhead -and [int]$preprodAhead -gt 0) {
                        $issues += "Preprod is $preprodAhead commits ahead of dev"
                    }
                    
                    # Check for unpushed commits
                    $devUnpushed = git rev-list --count origin/dev..dev 2>$null
                    if ($LASTEXITCODE -eq 0 -and $devUnpushed -and [int]$devUnpushed -gt 0) {
                        $issues += "$devUnpushed unpushed commits on dev"
                    }
                    
                    $preprodUnpushed = git rev-list --count origin/preprod..preprod 2>$null
                    if ($LASTEXITCODE -eq 0 -and $preprodUnpushed -and [int]$preprodUnpushed -gt 0) {
                        $issues += "$preprodUnpushed unpushed commits on preprod"
                    }
                }
                
                # Check if not on dev branch
                if ($currentBranch -ne "dev") {
                    $issues += "Currently on '$currentBranch' instead of 'dev'"
                }
                
                if ($issues.Count -gt 0) {
                    $syncIssues += @{
                        Repo = $repo.name
                        Issues = $issues
                    }
                    
                    Write-Host "  [WARNING] $($repo.name):" -ForegroundColor Yellow
                    foreach ($issue in $issues) {
                        Write-Host "    - $issue" -ForegroundColor Red
                    }
                }
                
            } catch {
                Write-Host "  [ERROR] Failed to check $($repo.name): $($_.Exception.Message)" -ForegroundColor Red
            } finally {
                Pop-Location
            }
        }
    }
    
    Write-Host "" -ForegroundColor White
    
    if ($syncIssues.Count -gt 0) {
        Write-Host "[SYNC ISSUES DETECTED] $($syncIssues.Count) repositories need attention" -ForegroundColor Red
        return $false
    }
    
    Write-Host "[PRE-FLIGHT] All branches properly synchronized!" -ForegroundColor Green
    Write-Host "" -ForegroundColor White
    return $true
}

# Set working directory
$RootPath = "C:\Users\Mohammed.Agra\OneDrive - Rejlers AB\Desktop\AIFlow"
Set-Location $RootPath

# Repository paths to process (SOFT-CODED)
# NOTE: Main/Production branch is NOT included here - it's handled by a separate preprod->main promotion script
# This script only handles: Local -> Dev -> Preprod pipeline
$repositories = @(
    @{ name = "Backend"; path = "backend" },
    @{ name = "Frontend"; path = "frontend" },
    @{ name = "Data-Management"; path = "data-management" }
)

# PRE-FLIGHT BRANCH SYNCHRONIZATION CHECK
if ($CONFIG.BranchSyncCheckEnabled -and -not $SkipPreCheck) {
    $syncOk = Test-BranchSynchronization -RootPath $RootPath -Repositories $repositories
    
    if (-not $syncOk) {
        Write-Host "[ACTION REQUIRED] Branch synchronization issues detected!" -ForegroundColor Red
        Write-Host "" -ForegroundColor White
        
        if ($CONFIG.AutoSyncBranches) {
            Write-Host "[AUTO-SYNC] Attempting automatic branch synchronization..." -ForegroundColor Yellow
            Write-Host "" -ForegroundColor White
            
            # Call sync-branches.ps1 script
            $syncScriptPath = Join-Path $RootPath "scripts\sync-branches.ps1"
            
            if (Test-Path $syncScriptPath) {
                & $syncScriptPath -AutoFix -GitHubToken $env:GITHUB_TOKEN
                
                if ($LASTEXITCODE -eq 0) {
                    Write-Host "" -ForegroundColor White
                    Write-Host "[SUCCESS] Branches synchronized! Proceeding with deployment..." -ForegroundColor Green
                    Write-Host "" -ForegroundColor White
                } else {
                    Write-Host "" -ForegroundColor White
                    Write-Host "[ERROR] Automatic synchronization failed. Please run:" -ForegroundColor Red
                    Write-Host "  .\scripts\sync-branches.ps1 -AutoFix" -ForegroundColor Gray
                    Write-Host "" -ForegroundColor White
                    exit 1
                }
            } else {
                Write-Host "[ERROR] sync-branches.ps1 not found at: $syncScriptPath" -ForegroundColor Red
                Write-Host "[INFO] Please manually synchronize branches before deployment" -ForegroundColor Yellow
                Write-Host "" -ForegroundColor White
                exit 1
            }
        } else {
            Write-Host "[INFO] Run the following command to fix synchronization:" -ForegroundColor Yellow
            Write-Host "  .\scripts\sync-branches.ps1 -AutoFix" -ForegroundColor Gray
            Write-Host "" -ForegroundColor White
            Write-Host "[INFO] Or skip this check with -SkipPreCheck flag (not recommended)" -ForegroundColor Gray
            Write-Host "" -ForegroundColor White
            exit 1
        }
    }
}

Write-Host "[STEP 1]: LOCAL -> DEV BRANCH (SMART PROCESSING)" -ForegroundColor Yellow
Write-Host "" -ForegroundColor White

# SUCCESS/FAILURE TRACKING
$deploymentResults = @{
    DevSuccess = @()
    DevFailed = @()
    PreprodSuccess = @()
    PreprodFailed = @()
}

foreach ($repo in $repositories) {
    if (Test-Path $repo.path) {
        # SOFT-CODED: Check repository accessibility before processing
        $absolutePath = if ($repo.path -eq ".") { $RootPath } else { Join-Path $RootPath $repo.path }
        
        if (-not (Test-RepositoryAccessibility -RepoName $repo.name -RepoPath $absolutePath)) {
            # Repository is not accessible and should be skipped
            $deploymentResults.DevFailed += $repo.name
            continue
        }
        
        Write-Host "  Processing $($repo.name) with smart retry logic..." -ForegroundColor Cyan
        Write-Host "    [INFO] Repository path: $absolutePath" -ForegroundColor Gray
        
        Push-Location $repo.path
        $repoSuccess = $false
        
        try {
            # SMART PRE-VALIDATION
            if ($CONFIG.PreValidationEnabled -and -not $SkipPreCheck) {
                Write-Host "    [VALIDATE] Checking repository state..." -ForegroundColor Gray
                
                # Verify we're in a Git repository
                if (-not (Test-Path ".git")) {
                    Write-Host "    [ERROR] Not a Git repository: $($repo.path)" -ForegroundColor Red
                    continue
                }
                
                # Check if we're in a clean state
                $gitStatus = git status --porcelain
                if ($gitStatus -match "^UU|^DD|^AA") {
                    Write-Host "    [ERROR] Repository has merge conflicts - resolve manually" -ForegroundColor Red
                    continue
                }
            }
            
            # AUTHENTICATION CHECK
            if ($CONFIG.AuthCheckEnabled) {
                $authResult = Test-GitAuthentication
                if (-not $authResult) {
                    Write-Host "    [AUTH] Setting up authentication for $($repo.name)..." -ForegroundColor Yellow
                }
            }
            
            # Switch to dev branch
            git checkout dev -q 2>$null
            if ($LASTEXITCODE -ne 0) {
                git checkout -b dev -q 2>$null
            }
            
            # Check for changes
            $status = git status --porcelain
            if ($status) {
                # Add and commit changes
                git add .
                git commit -m $CommitMessage -q
                Write-Host "    [SUCCESS] Changes committed" -ForegroundColor Green
            } else {
                Write-Host "    [INFO] No changes to commit" -ForegroundColor Gray
            }
            
            # Check if local is behind remote
            Write-Host "    [VALIDATE] Checking remote sync status..." -ForegroundColor Gray
            git fetch origin dev -q 2>$null
            $localCommit = git rev-parse dev 2>$null
            $remoteCommit = git rev-parse origin/dev 2>$null
            
            if ($localCommit -and $remoteCommit -and $localCommit -ne $remoteCommit) {
                $behind = git rev-list --count ${localCommit}..${remoteCommit} 2>$null
                if ($behind -and [int]$behind -gt 0) {
                    Write-Host "    [WARNING] Local branch is $behind commits behind remote" -ForegroundColor Yellow
                    Write-Host "    [SYNC] Pulling latest changes..." -ForegroundColor Gray
                    git pull origin dev --rebase -q 2>$null
                    if ($LASTEXITCODE -ne 0) {
                        Write-Host "    [ERROR] Failed to sync with remote. Manual intervention required." -ForegroundColor Red
                        Write-Host "    [INFO] Run: git pull origin dev --rebase" -ForegroundColor Gray
                        $deploymentResults.DevFailed += $repo.name
                        continue
                    }
                }
            }
            
            # SMART PUSH WITH TOKEN
            if ($env:GITHUB_TOKEN) {
                Write-Host "    [PUSH] Using GitHub token authentication..." -ForegroundColor Cyan
                $pushSuccess = Invoke-GitPushWithToken -Branch "dev" -RepoName $repo.name
            } else {
                Write-Host "    [PUSH] Using standard authentication (may prompt)..." -ForegroundColor Yellow
                $pushSuccess = Invoke-GitOperationWithRetry -Operation "Push to dev branch" -RepoName $repo.name -GitCommand {
                    git push origin dev
                }
            }
            
            if ($pushSuccess) {
                $deploymentResults.DevSuccess += $repo.name
                $repoSuccess = $true
                Write-Host "    [SUCCESS] $($repo.name) pushed to dev branch" -ForegroundColor Green
            } else {
                $deploymentResults.DevFailed += $repo.name
                Write-Host "    [ERROR] Failed to push dev branch for $($repo.name)" -ForegroundColor Red
            }
            
        } catch {
            Write-Host "    [ERROR] Unexpected error in $($repo.name): $($_.Exception.Message)" -ForegroundColor Red
            $deploymentResults.DevFailed += $repo.name
        } finally {
            Pop-Location
        }
    } else {
        Write-Host "  [WARNING] Repository path not found: $($repo.path)" -ForegroundColor Yellow
        Write-Host "    [INFO] Expected: $(Join-Path $RootPath $repo.path)" -ForegroundColor Gray
        Write-Host "    [SKIP] Skipping $($repo.name) repository" -ForegroundColor Yellow
        $deploymentResults.DevFailed += $repo.name
    }
}

Write-Host "" -ForegroundColor White
Write-Host "[STEP 2]: DEV -> PREPROD (SMART PROMOTION)" -ForegroundColor Yellow
Write-Host "" -ForegroundColor White

# Only proceed with preprod if we have some dev successes
if ($deploymentResults.DevSuccess.Count -eq 0) {
    Write-Host "[ABORT] No repositories successfully pushed to dev - aborting preprod promotion" -ForegroundColor Red
    exit 1
}

Write-Host "[INFO] Promoting $($deploymentResults.DevSuccess.Count) successful repositories to preprod..." -ForegroundColor Gray

# Wait for git operations to settle
Start-Sleep -Seconds $CONFIG.RetryDelaySeconds

foreach ($repo in $repositories) {
    # Only process repos that succeeded in dev push or use Force flag
    if (($deploymentResults.DevSuccess -contains $repo.name) -or $Force) {
        if (Test-Path $repo.path) {
            Write-Host "  Promoting $($repo.name) to preprod with smart retry..." -ForegroundColor Cyan
            
            Push-Location $repo.path
            
            try {
                # ENHANCED PRE-VALIDATION FOR PREPROD
                Write-Host "    [VALIDATE] Checking dev branch status..." -ForegroundColor Gray
                
                # Ensure we have the latest dev branch changes
                Write-Host "    [SYNC] Ensuring latest dev branch changes..." -ForegroundColor Gray
                git checkout dev -q 2>$null
                
                # Fetch and pull latest dev changes with token authentication
                if ($env:GITHUB_TOKEN) {
                    $remoteUrl = git config --get remote.origin.url
                    if ($remoteUrl -match "github\.com[:/](.+)\.git$") {
                        $repoPath = $matches[1]
                        $tokenUrl = "https://${env:GITHUB_TOKEN}@github.com/${repoPath}.git"
                        Write-Host "    [FETCH] Using token authentication to fetch dev..." -ForegroundColor Gray
                        git fetch $tokenUrl dev -q 2>$null
                        git pull $tokenUrl dev -q 2>$null
                    } else {
                        git fetch origin dev -q 2>$null  
                        git pull origin dev -q 2>$null
                    }
                } else {
                    git fetch origin dev -q 2>$null
                    git pull origin dev -q 2>$null
                }
                
                # Switch to preprod branch and ensure it exists
                Write-Host "    [BRANCH] Switching to preprod branch..." -ForegroundColor Gray
                git checkout preprod -q 2>$null
                if ($LASTEXITCODE -ne 0) {
                    # Create preprod branch from dev if it doesn't exist
                    git checkout -b preprod dev -q 2>$null
                    Write-Host "    [INFO] Created new preprod branch from dev" -ForegroundColor Gray
                } else {
                    # Fetch latest preprod changes if branch exists
                    if ($env:GITHUB_TOKEN) {
                        git fetch $tokenUrl preprod -q 2>$null
                    } else {
                        git fetch origin preprod -q 2>$null  
                    }
                }
                
                # ENHANCED MERGE LOGIC
                Write-Host "    [MERGE] Merging latest dev changes into preprod..." -ForegroundColor Gray
                
                # Reset preprod to match dev exactly (this ensures clean merge)
                git reset --hard dev -q 2>$null
                $mergeSuccess = $LASTEXITCODE -eq 0
                
                if ($mergeSuccess) {
                    Write-Host "    [SUCCESS] Preprod updated with dev changes" -ForegroundColor Green
                    
                    # Verify the merge by checking commit differences
                    $commitDiff = git rev-list --count dev...preprod
                    if ($commitDiff -eq 0) {
                        Write-Host "    [VERIFY] Preprod is now in sync with dev" -ForegroundColor Green
                    } else {
                        Write-Host "    [WARNING] Preprod might not be fully synced with dev" -ForegroundColor Yellow
                    }
                } else {
                    Write-Host "    [ERROR] Failed to update preprod with dev changes" -ForegroundColor Red
                }
                
                if ($mergeSuccess) {
                    # SMART PUSH TO PREPROD WITH TOKEN (FORCE PUSH REQUIRED)
                    # Force push is necessary because we use 'git reset --hard dev'
                    Write-Host "    [INFO] Force push required due to reset --hard operation" -ForegroundColor Gray
                    
                    if ($env:GITHUB_TOKEN) {
                        Write-Host "    [PUSH] Using GitHub token for preprod push..." -ForegroundColor Cyan
                        $pushSuccess = Invoke-GitPushWithToken -Branch "preprod" -RepoName $repo.name -Force
                    } else {
                        Write-Host "    [PUSH] Using standard auth for preprod push..." -ForegroundColor Yellow
                        $pushSuccess = Invoke-GitOperationWithRetry -Operation "Push to preprod" -RepoName $repo.name -GitCommand {
                            git push origin preprod --force
                        }
                    }
                    
                    if ($pushSuccess) {
                        $deploymentResults.PreprodSuccess += $repo.name
                        Write-Host "    [SUCCESS] $($repo.name) promoted to preprod" -ForegroundColor Green
                    } else {
                        $deploymentResults.PreprodFailed += $repo.name
                        Write-Host "    [ERROR] Failed to push $($repo.name) to preprod" -ForegroundColor Red
                    }
                } else {
                    $deploymentResults.PreprodFailed += $repo.name
                    Write-Host "    [ERROR] Failed to merge dev into preprod for $($repo.name)" -ForegroundColor Red
                }
                
            } catch {
                Write-Host "    [ERROR] Unexpected error promoting $($repo.name): $($_.Exception.Message)" -ForegroundColor Red
                $deploymentResults.PreprodFailed += $repo.name
            } finally {
                Pop-Location
            }
        }
    } else {
        Write-Host "  [SKIP] $($repo.name) - not successfully pushed to dev" -ForegroundColor Yellow
    }
}

# Switch back to dev branch for continued development
Set-Location $RootPath
git checkout dev -q 2>$null

# INTELLIGENT DEPLOYMENT SUMMARY
Write-Host "" -ForegroundColor White
Write-Host "=====================================" -ForegroundColor Cyan
Write-Host "[DEPLOYMENT ANALYSIS & SUMMARY]" -ForegroundColor Cyan
Write-Host "=====================================" -ForegroundColor Cyan
Write-Host "" -ForegroundColor White

# Calculate success rates
$devSuccessRate = if ($repositories.Count -gt 0) { [math]::Round(($deploymentResults.DevSuccess.Count / $repositories.Count) * 100, 1) } else { 0 }
$preprodSuccessRate = if ($repositories.Count -gt 0) { [math]::Round(($deploymentResults.PreprodSuccess.Count / $repositories.Count) * 100, 1) } else { 0 }

Write-Host "[DEV BRANCH RESULTS] ($devSuccessRate% Success Rate)" -ForegroundColor Yellow
if ($deploymentResults.DevSuccess.Count -gt 0) {
    Write-Host "  [SUCCESS] Repositories: $($deploymentResults.DevSuccess -join ', ')" -ForegroundColor Green
}
if ($deploymentResults.DevFailed.Count -gt 0) {
    Write-Host "  [FAILED] Repositories: $($deploymentResults.DevFailed -join ', ')" -ForegroundColor Red
}

Write-Host "" -ForegroundColor White
Write-Host "[PREPROD BRANCH RESULTS] ($preprodSuccessRate% Success Rate)" -ForegroundColor Yellow
if ($deploymentResults.PreprodSuccess.Count -gt 0) {
    Write-Host "  [SUCCESS] Repositories: $($deploymentResults.PreprodSuccess -join ', ')" -ForegroundColor Green
}
if ($deploymentResults.PreprodFailed.Count -gt 0) {
    Write-Host "  [FAILED] Repositories: $($deploymentResults.PreprodFailed -join ', ')" -ForegroundColor Red
}

Write-Host "" -ForegroundColor White
Write-Host "[DEPLOYMENT STATUS]:" -ForegroundColor Yellow

if ($deploymentResults.PreprodSuccess.Count -eq $repositories.Count) {
    Write-Host "  >> COMPLETE SUCCESS: All repositories deployed to preprod!" -ForegroundColor Green
    Write-Host "  >> Railway will auto-deploy preprod environment" -ForegroundColor Cyan
    $exitCode = 0
} elseif ($deploymentResults.PreprodSuccess.Count -gt 0) {
    Write-Host "  >> PARTIAL SUCCESS: $($deploymentResults.PreprodSuccess.Count)/$($repositories.Count) repositories deployed" -ForegroundColor Yellow
    Write-Host "  >> Railway will deploy successful repositories to preprod" -ForegroundColor Cyan
    $exitCode = 0
} else {
    Write-Host "  >> DEPLOYMENT FAILED: No repositories successfully deployed to preprod" -ForegroundColor Red
    Write-Host "  >> Check authentication and repository status" -ForegroundColor Yellow
    $exitCode = 1
}

Write-Host "" -ForegroundColor White
Write-Host "[NEXT STEPS]:" -ForegroundColor Yellow
if ($deploymentResults.PreprodSuccess.Count -gt 0) {
    Write-Host "  1. Test your changes in preprod environment" -ForegroundColor White
    Write-Host "  2. When ready for production, run: .\scripts\prod-deploy.ps1" -ForegroundColor White
}
if ($deploymentResults.DevFailed.Count -gt 0 -or $deploymentResults.PreprodFailed.Count -gt 0) {
    Write-Host "  3. [TROUBLESHOOTING] Failed repositories detected:" -ForegroundColor Yellow
    Write-Host "" -ForegroundColor White
    
    if ($deploymentResults.DevFailed.Count -gt 0) {
        Write-Host "     DEV PUSH FAILURES:" -ForegroundColor Red
        foreach ($failedRepo in $deploymentResults.DevFailed) {
            Write-Host "       - $failedRepo" -ForegroundColor Red
        }
        Write-Host "" -ForegroundColor White
        Write-Host "     SOLUTIONS:" -ForegroundColor Yellow
        Write-Host "       A. Use GitHub Token (RECOMMENDED):" -ForegroundColor Cyan
        Write-Host "          1. Create token: https://github.com/settings/tokens" -ForegroundColor Gray
        Write-Host "          2. Select 'repo' scope (full control)" -ForegroundColor Gray
        Write-Host "          3. Run: .\dev-deploy.ps1 -CommitMessage 'msg' -GitHubToken 'YOUR_TOKEN'" -ForegroundColor Gray
        Write-Host "" -ForegroundColor White
        Write-Host "       B. Fix Git Credentials:" -ForegroundColor Cyan
        Write-Host "          1. Run: git config --global credential.helper manager-core" -ForegroundColor Gray
        Write-Host "          2. Run: git push origin dev (in failed repo directory)" -ForegroundColor Gray
        Write-Host "          3. Complete authentication in browser" -ForegroundColor Gray
        Write-Host "          4. Retry: .\dev-deploy.ps1 -CommitMessage 'msg'" -ForegroundColor Gray
        Write-Host "" -ForegroundColor White
        Write-Host "       C. Manual Check:" -ForegroundColor Cyan
        Write-Host "          1. cd into failed repository" -ForegroundColor Gray
        Write-Host "          2. Run: git status" -ForegroundColor Gray
        Write-Host "          3. Run: git pull origin dev --rebase" -ForegroundColor Gray
        Write-Host "          4. Run: git push origin dev" -ForegroundColor Gray
    }
    
    if ($deploymentResults.PreprodFailed.Count -gt 0) {
        Write-Host "" -ForegroundColor White
        Write-Host "     PREPROD PUSH FAILURES:" -ForegroundColor Red
        foreach ($failedRepo in $deploymentResults.PreprodFailed) {
            Write-Host "       - $failedRepo" -ForegroundColor Red
        }
        Write-Host "" -ForegroundColor White
        Write-Host "     SOLUTIONS:" -ForegroundColor Yellow
        Write-Host "       - Preprod uses force push due to reset operation" -ForegroundColor Gray
        Write-Host "       - If authentication issue: Follow Dev solutions above" -ForegroundColor Gray
        Write-Host "       - Manual fix: cd to repo, then:" -ForegroundColor Gray
        Write-Host "         git checkout preprod" -ForegroundColor Gray
        Write-Host "         git reset --hard dev" -ForegroundColor Gray
        Write-Host "         git push origin preprod --force" -ForegroundColor Gray
    }
}

Write-Host "" -ForegroundColor White
Write-Host "=====================================" -ForegroundColor Cyan

exit $exitCode