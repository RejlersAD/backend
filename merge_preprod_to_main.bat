@echo off
setlocal enabledelayedexpansion
REM ================================================================
REM  MERGE PREPROD TO MAIN (PRODUCTION DEPLOYMENT)
REM ================================================================
REM Purpose: Merge preprod branch into main and push to GitHub
REM Usage: Double-click this file or run from command line
REM Target: Updates main branch with preprod changes for Railway deployment
REM ================================================================

echo.
echo ================================================================
echo   MERGE PREPROD TO MAIN (PRODUCTION)
echo ================================================================
echo.
echo This will merge your preprod branch into main and push to GitHub.
echo Railway will automatically deploy main to production.
echo.
echo Current directory: %CD%
echo.

REM Check if git is available
where git >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Git is not installed or not in PATH
    pause
    exit /b 1
)

REM Get current branch
for /f "tokens=*" %%i in ('git rev-parse --abbrev-ref HEAD 2^>nul') do set CURRENT_BRANCH=%%i
echo Current branch: !CURRENT_BRANCH!
echo.

REM Check for uncommitted changes
git diff --quiet
if errorlevel 1 (
    echo [WARNING] You have uncommitted changes!
    echo.
    git status --short
    echo.
    echo Options:
    echo   1. Commit them now (recommended)
    echo   2. Stash them temporarily
    echo   3. Cancel and commit manually
    echo.
    set /p UNCOMMITTED="Choose (1/2/3): "
    if "!UNCOMMITTED!"=="1" (
        set /p COMMIT_MSG="Enter commit message: "
        git add .
        git commit -m "!COMMIT_MSG!"
    ) else if "!UNCOMMITTED!"=="2" (
        git stash push -m "Auto-stash before merge preprod to main"
        echo [OK] Changes stashed
    ) else (
        echo [CANCELLED] Please commit your changes first
        pause
        exit /b 0
    )
)
echo.

REM Confirm action
set /p CONFIRM="Type YES to proceed with merge to PRODUCTION: "
if /i not "%CONFIRM%"=="YES" (
    echo.
    echo [CANCELLED] Operation cancelled.
    echo.
    pause
    exit /b 0
)

echo.
echo ================================================================
echo Starting preprod to main merge...
echo ================================================================
echo.

REM Step 1: Switch to main branch
echo [1/7] Switching to main branch...
git checkout main
if errorlevel 1 (
    echo [ERROR] Failed to switch to main branch
    echo.
    echo Possible reasons:
    echo   - Branch does not exist
    echo   - Uncommitted changes blocking checkout
    echo   - Merge conflict in progress
    echo.
    pause
    exit /b 1
)
echo [OK] On main branch
echo.

REM Step 2: Fetch latest changes
echo [2/7] Fetching latest changes from remote...
git fetch origin
if errorlevel 1 (
    echo [ERROR] Failed to fetch from remote
    echo Check your network connection and GitHub credentials
    pause
    exit /b 1
)
echo [OK] Remote changes fetched
echo.

REM Step 3: Pull latest main with rebase
echo [3/7] Pulling latest main branch...
git pull origin main --rebase
if errorlevel 1 (
    echo [WARNING] Rebase failed, attempting regular merge...
    git rebase --abort 2>nul
    git pull origin main
    if errorlevel 1 (
        echo [ERROR] Cannot update main branch
        echo You may need to resolve conflicts manually
        pause
        exit /b 1
    )
)
echo [OK] Main branch updated
echo.

REM Step 4: Check if preprod branch exists
echo [4/7] Verifying preprod branch exists...
git rev-parse --verify origin/preprod >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Preprod branch does not exist on remote
    echo.
    echo Available branches:
    git branch -r
    echo.
    pause
    exit /b 1
)
echo [OK] Preprod branch found
echo.

REM Step 5: Merge preprod into main
echo [5/7] Merging preprod into main...
git merge origin/preprod -m "Merge preprod into main for production deployment"
if errorlevel 1 (
    echo.
    echo [ERROR] MERGE CONFLICTS DETECTED!
    echo.
    git status
    echo.
    echo To resolve:
    echo   1. Open conflicted files in VS Code
    echo   2. Resolve conflicts manually
    echo   3. Run: git add .
    echo   4. Run: git commit -m "Resolve merge conflicts"
    echo   5. Run: git push origin main
    echo.
    echo OR accept all preprod changes (USE WITH CAUTION):
    echo   git checkout --theirs .
    echo   git add .
    echo   git commit --no-edit
    echo   git push origin main
    echo.
    pause
    exit /b 1
)
echo [OK] Preprod merged into main successfully
echo.

REM Step 6: Push to GitHub
echo [6/7] Pushing to GitHub...
git push origin main
if errorlevel 1 (
    echo [ERROR] Failed to push to GitHub
    echo.
    echo Possible reasons:
    echo   - Network connection issue
    echo   - GitHub authentication failed
    echo   - Protected branch rules
    echo   - Remote has changes you don't have (need pull first)
    echo.
    echo Try: git pull origin main --rebase, then git push origin main
    echo.
    pause
    exit /b 1
)
echo [OK] Pushed to GitHub successfully
echo.

REM Step 7: Verify status and return to original branch
echo [7/7] Verifying final status...
for /f "tokens=*" %%i in ('git rev-parse HEAD') do set MAIN_COMMIT=%%i
echo Main branch is now at commit: !MAIN_COMMIT:~0,7!
echo.
git log -1 --oneline
echo.

REM Return to original branch if it wasn't main
if not "!CURRENT_BRANCH!"=="main" (
    echo Returning to original branch: !CURRENT_BRANCH!
    git checkout !CURRENT_BRANCH!
    echo.
)

echo ================================================================
echo [SUCCESS] Preprod merged into main and pushed to GitHub!
echo ================================================================
echo.
echo Railway will now automatically deploy main to production.
echo.
echo Next steps:
echo   1. Monitor Railway deployment: https://railway.app
echo   2. Check production backend: https://aiflowbackend-production.up.railway.app
echo   3. Verify frontend: https://frontend-cyan-eta-q169h70uw0.vercel.app
echo   4. Monitor for errors in Railway logs
echo.
echo Deployment typically takes 2-3 minutes.
echo.
echo Latest commit deployed: !MAIN_COMMIT:~0,7!
echo.
pause
