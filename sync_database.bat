@echo off
chcp 65001 >nul 2>&1
REM ============================================================================
REM Database Synchronization Script
REM ============================================================================
REM This script syncs the local database with production (100% alignment)
REM 
REM Prerequisites:
REM 1. Add PROD_DATABASE_URL to backend/.env file
REM 2. Ensure virtual environment is set up
REM
REM Usage: Just double-click this file or run: sync_database.bat
REM ============================================================================

echo.
echo ============================================================================
echo   RAD AI - Database Synchronization Script
echo ============================================================================
echo.
echo This will sync your local database with production (FULL REPLACEMENT MODE)
echo.
echo WARNING: This will overwrite local data with production data!
echo.
echo Press Ctrl+C to cancel, or
pause

echo.
echo [1/4] Checking environment...

REM Check if .env file exists
if not exist ".env" (
    echo ERROR: .env file not found in backend directory
    echo Please create .env file and add PROD_DATABASE_URL
    echo.
    echo Example:
    echo   PROD_DATABASE_URL=postgresql://postgres:password@host:port/railway
    echo.
    pause
    exit /b 1
)

REM Check if manage.py exists
if not exist "manage.py" (
    echo ERROR: manage.py not found. Are you in the backend directory?
    pause
    exit /b 1
)

echo [2/4] Activating virtual environment...

REM Try to activate virtual environment (common locations)
if exist "venv\Scripts\activate.bat" (
    call venv\Scripts\activate.bat
    echo Virtual environment activated: venv
) else if exist ".venv\Scripts\activate.bat" (
    call .venv\Scripts\activate.bat
    echo Virtual environment activated: .venv
) else if exist "env\Scripts\activate.bat" (
    call env\Scripts\activate.bat
    echo Virtual environment activated: env
) else (
    echo WARNING: No virtual environment found, using system Python
    echo Consider creating one with: python -m venv venv
    echo.
)

echo [3/4] Checking Django installation...
python -c "import django" 2>nul
if errorlevel 1 (
    echo ERROR: Django not installed. Please run: pip install -r requirements.txt
    pause
    exit /b 1
)

echo [4/4] Starting database synchronization...
echo.
echo ============================================================================
echo   Syncing database in FULL mode (100%% alignment)
echo ============================================================================
echo.

REM Run the sync command in full mode
python manage.py sync_from_production --mode full

if errorlevel 1 (
    echo.
    echo ============================================================================
    echo   SYNC FAILED!
    echo ============================================================================
    echo.
    echo Common issues:
    echo   1. PROD_DATABASE_URL not set in .env file
    echo   2. Cannot connect to production database (check network/VPN)
    echo   3. Database credentials are incorrect
    echo.
    echo Please check the error message above and try again.
    echo.
    pause
    exit /b 1
)

echo.
echo ============================================================================
echo   SYNC COMPLETED SUCCESSFULLY!
echo ============================================================================
echo.
echo Your local database is now 100%% aligned with production.
echo.
pause
