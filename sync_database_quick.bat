@echo off
chcp 65001 >nul 2>&1
REM ============================================================================
REM Quick Database Sync (Incremental Mode)
REM ============================================================================
REM This script performs an incremental sync (only new/updated records)
REM Much faster than full sync, good for daily updates
REM ============================================================================

echo.
echo ============================================================================
echo   RAD AI - Quick Database Sync (Incremental)
echo ============================================================================
echo.

REM Check if .env file exists
if not exist ".env" (
    echo ERROR: .env file not found
    pause
    exit /b 1
)

REM Check if manage.py exists
if not exist "manage.py" (
    echo ERROR: manage.py not found. Run from backend directory
    pause
    exit /b 1
)

REM Activate virtual environment
if exist "venv\Scripts\activate.bat" (
    call venv\Scripts\activate.bat
) else if exist ".venv\Scripts\activate.bat" (
    call .venv\Scripts\activate.bat
) else if exist "env\Scripts\activate.bat" (
    call env\Scripts\activate.bat
)

echo Starting incremental sync...
echo.

REM Run incremental sync (default mode)
python manage.py sync_from_production

if errorlevel 1 (
    echo.
    echo SYNC FAILED! Check error above.
    pause
    exit /b 1
)

echo.
echo ============================================================================
echo   Incremental sync completed!
echo ============================================================================
echo.
pause
