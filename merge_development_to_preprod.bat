@echo off
REM ================================================================
REM  MERGE DEVELOPMENT TO PREPROD (PRE-PRODUCTION TESTING)
REM ================================================================
REM Purpose: Merge development branch into preprod for testing
REM Usage: Double-click this file or run from command line
REM Workflow: development → preprod → main
REM ================================================================

echo.
echo ================================================================
echo   MERGE DEVELOPMENT TO PREPROD (TESTING)
echo ================================================================
echo.
echo This will merge your development branch into preprod for testing.
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
echo Starting development to preprod merge...
echo ================================================================
echo.

REM Step 1: Switch to preprod branch
echo [1/6] Switching to preprod branch...
git checkout preprod
if errorlevel 1 (
    echo ❌ Failed to switch to preprod branch
    pause
    exit /b 1
)
echo ✅ On preprod branch
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

REM Step 3: Pull latest preprod
echo [3/6] Pulling latest preprod branch...
git pull origin preprod
if errorlevel 1 (
    echo ⚠️  Pull had issues, continuing with merge...
)
echo ✅ Preprod branch updated
echo.

REM Step 4: Merge development into preprod
echo [4/6] Merging development into preprod...
git merge origin/development -m "Merge development into preprod for testing"
if errorlevel 1 (
    echo.
    echo ⚠️  MERGE CONFLICTS DETECTED!
    echo.
    echo To resolve:
    echo   1. Open conflicted files in VS Code
    echo   2. Resolve conflicts manually
    echo   3. Run: git add .
    echo   4. Run: git commit -m "Resolve merge conflicts"
    echo   5. Run: git push origin preprod
    echo.
    echo OR accept all development changes:
    echo   git checkout --theirs .
    echo   git add .
    echo   git commit -m "Merge development into preprod"
    echo   git push origin preprod
    echo.
    pause
    exit /b 1
)
echo ✅ Development merged into preprod
echo.

REM Step 5: Push to GitHub
echo [5/6] Pushing to GitHub...
git push origin preprod
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
echo ✅ SUCCESS: Development merged into preprod!
echo ================================================================
echo.
echo 🧪 Railway will now automatically deploy preprod for testing.
echo.
echo Next steps:
echo   1. Test preprod deployment thoroughly
echo   2. If tests pass, run: merge_preprod_to_main.bat
echo   3. If issues found, fix in development and repeat
echo.
echo Check Railway dashboard to monitor deployment:
echo   https://railway.app
echo.
pause
