@echo off
REM ============================================================================
REM RAD AI Sync Agent - Auto Deploy to Preprod and Main
REM ============================================================================
REM This script:
REM 1. Commits all sync agent files
REM 2. Pushes to current branch (development)
REM 3. Merges to preprod branch
REM 4. Merges to main branch
REM 5. Pushes all changes
REM ============================================================================

setlocal enabledelayedexpansion

echo.
echo ============================================================================
echo   RAD AI Sync Agent - Deployment Script
echo ============================================================================
echo.

REM Store current directory
set "SCRIPT_DIR=%~dp0"
cd /d "%SCRIPT_DIR%"

REM Check if git is available
git --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Git is not installed or not in PATH
    pause
    exit /b 1
)

REM Get current branch
for /f "tokens=*" %%i in ('git branch --show-current') do set CURRENT_BRANCH=%%i
echo [INFO] Current branch: %CURRENT_BRANCH%
echo.

REM Check if there are sync agent files to commit
echo [INFO] Checking for sync agent files...
git status --short *sync*.py *sync*.ps1 *sync*.md *sync*.txt *sync*.bat >nul 2>&1
if errorlevel 1 (
    echo [WARNING] No sync agent files found or no changes
)

REM Show what will be committed
echo.
echo Files to be deployed:
echo -------------------
git status --short timesheet_mirror_sync.py 2>nul
git status --short requirements-sync-agent.txt 2>nul
git status --short .env.sync-agent.example 2>nul
git status --short SYNC_AGENT_SETUP.md 2>nul
git status --short README_SYNC_AGENT.md 2>nul
git status --short QUICK_FIX_SYNC_AGENT.md 2>nul
git status --short create_sync_task.ps1 2>nul
git status --short check_sync_agent.ps1 2>nul
git status --short restart_sync_agent.ps1 2>nul
git status --short test_sync_config.py 2>nul
echo.

REM Ask for confirmation
set /p CONFIRM="Deploy sync agent files to preprod and main? (yes/no): "
if /i not "%CONFIRM%"=="yes" (
    echo [INFO] Deployment cancelled by user
    pause
    exit /b 0
)

echo.
echo ============================================================================
echo STEP 1: Committing sync agent files
echo ============================================================================
echo.

REM Add all sync agent files
git add timesheet_mirror_sync.py
git add requirements-sync-agent.txt
git add .env.sync-agent.example
git add SYNC_AGENT_SETUP.md
git add README_SYNC_AGENT.md
git add QUICK_FIX_SYNC_AGENT.md
git add create_sync_task.ps1
git add check_sync_agent.ps1
git add restart_sync_agent.ps1
git add test_sync_config.py

REM Commit with descriptive message
git commit -m "feat: Add attendance sync agent package - Fixes 27-day data gap" -m "- Created timesheet_mirror_sync.py (main sync agent)" -m "- Added complete setup documentation" -m "- Included automation scripts (Task Scheduler)" -m "- Added configuration templates and validators" -m "- Fixed restart_sync_agent.ps1 Unicode errors" -m "- Comprehensive troubleshooting guides"

if errorlevel 1 (
    echo [WARNING] Commit failed or nothing to commit
    echo [INFO] Continuing with merge anyway...
)

echo.
echo ============================================================================
echo STEP 2: Pulling and pushing to current branch (%CURRENT_BRANCH%)
echo ============================================================================
echo.

REM Pull latest changes first
echo [INFO] Pulling latest changes from %CURRENT_BRANCH%...
git pull origin %CURRENT_BRANCH% --rebase
if errorlevel 1 (
    echo [WARNING] Pull failed or conflicts detected
    echo [INFO] Attempting to continue...
)

REM Now push
echo [INFO] Pushing to %CURRENT_BRANCH%...
git push origin %CURRENT_BRANCH%
if errorlevel 1 (
    echo [ERROR] Failed to push to %CURRENT_BRANCH%
    echo [INFO] Try: git pull origin %CURRENT_BRANCH% --rebase
    pause
    exit /b 1
)

echo [OK] Pulled and pushed to %CURRENT_BRANCH%

echo.
echo ============================================================================
echo STEP 3: Merging to preprod branch
echo ============================================================================
echo.

REM Fetch latest changes
echo [INFO] Fetching preprod branch...
git fetch origin preprod
if errorlevel 1 (
    echo [WARNING] Failed to fetch preprod (may not exist remotely yet)
)

REM Checkout preprod
echo [INFO] Checking out preprod...
git checkout preprod
if errorlevel 1 (
    echo [ERROR] Failed to checkout preprod branch
    pause
    exit /b 1
)

REM Pull latest preprod with rebase
echo [INFO] Pulling latest preprod...
git pull origin preprod --rebase
if errorlevel 1 (
    echo [WARNING] Failed to pull preprod (may not exist remotely yet)
)

REM Merge from development
git merge %CURRENT_BRANCH% -m "chore: Merge sync agent package from %CURRENT_BRANCH% to preprod"
if errorlevel 1 (
    echo [ERROR] Merge conflict detected!
    echo [INFO] Please resolve conflicts manually and run:
    echo        git add .
    echo        git commit
    echo        git push origin preprod
    pause
    exit /b 1
)

REM Push preprod
git push origin preprod
if errorlevel 1 (
    echo [ERROR] Failed to push preprod
    pause
    exit /b 1
)

echo [OK] Merged and pushed to preprod

echo.
echo ============================================================================
echo STEP 4: Merging to main branch
echo ============================================================================
echo.

REM Fetch latest changes
echo [INFO] Fetching main branch...
git fetch origin main
if errorlevel 1 (
    echo [ERROR] Failed to fetch main branch
    pause
    exit /b 1
)

REM Checkout main
echo [INFO] Checking out main...
git checkout main
if errorlevel 1 (
    echo [ERROR] Failed to checkout main branch
    pause
    exit /b 1
)

REM Pull latest main with rebase
echo [INFO] Pulling latest main...
git pull origin main --rebase
if errorlevel 1 (
    echo [WARNING] Failed to pull main, attempting merge strategy...
    git pull origin main
    if errorlevel 1 (
        echo [ERROR] Pull failed. Please resolve conflicts manually.
        pause
        exit /b 1
    )
)

REM Merge from preprod
git merge preprod -m "chore: Deploy sync agent package to production (main)"
if errorlevel 1 (
    echo [ERROR] Merge conflict detected!
    echo [INFO] Please resolve conflicts manually and run:
    echo        git add .
    echo        git commit
    echo        git push origin main
    pause
    exit /b 1
)

REM Push main
git push origin main
if errorlevel 1 (
    echo [ERROR] Failed to push main
    pause
    exit /b 1
)

echo [OK] Merged and pushed to main

echo.
echo ============================================================================
echo STEP 5: Returning to original branch
echo ============================================================================
echo.

git checkout %CURRENT_BRANCH%
if errorlevel 1 (
    echo [WARNING] Failed to return to %CURRENT_BRANCH%
)

echo [OK] Returned to %CURRENT_BRANCH%

echo.
echo ============================================================================
echo                          DEPLOYMENT COMPLETE!
echo ============================================================================
echo.
echo Summary:
echo   [OK] Committed sync agent files
echo   [OK] Pushed to %CURRENT_BRANCH%
echo   [OK] Merged to preprod
echo   [OK] Merged to main
echo.
echo Next Steps:
echo   1. Log into office server
echo   2. Pull the latest main branch
echo   3. Copy sync agent files to C:\RadAI\sync-agent\
echo   4. Follow setup guide in SYNC_AGENT_SETUP.md
echo.
echo Files deployed:
echo   - timesheet_mirror_sync.py
echo   - requirements-sync-agent.txt
echo   - .env.sync-agent.example
echo   - SYNC_AGENT_SETUP.md
echo   - README_SYNC_AGENT.md
echo   - QUICK_FIX_SYNC_AGENT.md
echo   - create_sync_task.ps1
echo   - check_sync_agent.ps1
echo   - restart_sync_agent.ps1 (FIXED)
echo   - test_sync_config.py
echo.
echo ============================================================================
echo.

pause
exit /b 0
