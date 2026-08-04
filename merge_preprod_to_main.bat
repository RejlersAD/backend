@echo off
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

REM Confirm action
set /p CONFIRM="Type YES to proceed with merge: "
if /i not "%CONFIRM%"=="YES" (
    echo.
    echo ❌ Operation cancelled.
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
echo [1/6] Switching to main branch...
git checkout main
if errorlevel 1 (
    echo ❌ Failed to switch to main branch
    pause
    exit /b 1
)
echo ✅ On main branch
echo.

REM Step 2: Fetch latest changes
echo [2/6] Fetching latest changes from remote...
git fetch origin
if errorlevel 1 (
    echo ❌ Failed to fetch from remote
    pause
    exit /b 1
)
echo ✅ Remote changes fetched
echo.

REM Step 3: Pull latest main
echo [3/6] Pulling latest main branch...
git pull origin main
if errorlevel 1 (
    echo ⚠️  Pull had issues, continuing with merge...
)
echo ✅ Main branch updated
echo.

REM Step 4: Merge preprod into main
echo [4/6] Merging preprod into main...
git merge origin/preprod -m "Merge preprod into main for production deployment"
if errorlevel 1 (
    echo.
    echo ⚠️  MERGE CONFLICTS DETECTED!
    echo.
    echo To resolve:
    echo   1. Open conflicted files in VS Code
    echo   2. Resolve conflicts manually
    echo   3. Run: git add .
    echo   4. Run: git commit -m "Resolve merge conflicts"
    echo   5. Run: git push origin main
    echo.
    echo OR accept all preprod changes:
    echo   git checkout --theirs .
    echo   git add .
    echo   git commit -m "Merge preprod into main"
    echo   git push origin main
    echo.
    pause
    exit /b 1
)
echo ✅ Preprod merged into main
echo.

REM Step 5: Push to GitHub
echo [5/6] Pushing to GitHub...
git push origin main
if errorlevel 1 (
    echo ❌ Failed to push to GitHub
    echo Please check your credentials and network connection
    pause
    exit /b 1
)
echo ✅ Pushed to GitHub
echo.

REM Step 6: Verify status
echo [6/6] Verifying final status...
git status
echo.

echo ================================================================
echo ✅ SUCCESS: Preprod merged into main!
echo ================================================================
echo.
echo 🚀 Railway will now automatically deploy main to production.
echo.
echo Check Railway dashboard to monitor deployment:
echo   https://railway.app
echo.
echo Your production backend will be updated in a few minutes.
echo.
pause
