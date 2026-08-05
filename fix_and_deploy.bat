@echo off
REM Quick Fix for Non-Fast-Forward Error
REM Run this to resolve the current issue

echo.
echo ============================================================================
echo   Quick Fix: Sync with Remote and Deploy
echo ============================================================================
echo.

REM Get current branch
for /f "tokens=*" %%i in ('git branch --show-current') do set CURRENT_BRANCH=%%i
echo [INFO] Current branch: %CURRENT_BRANCH%
echo.

echo [STEP 1] Pulling latest changes from remote...
git pull origin %CURRENT_BRANCH% --rebase

if errorlevel 1 (
    echo.
    echo [ERROR] Pull failed! You may have conflicts.
    echo.
    echo To resolve:
    echo   1. Fix conflicts in marked files
    echo   2. git add .
    echo   3. git rebase --continue
    echo   4. Run this script again
    echo.
    pause
    exit /b 1
)

echo [OK] Successfully pulled and rebased
echo.

echo [STEP 2] Now deploying with updated script...
echo.

call deploy_sync_agent.bat

exit /b 0
