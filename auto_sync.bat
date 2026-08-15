@echo off
chcp 65001 >nul 2>&1
REM ============================================================================
REM AUTO-SYNC SERVICE - Automatic Database Synchronization
REM ============================================================================
REM This script runs the database sync automatically at regular intervals
REM 
REM Usage: 
REM   1. Double-click to start the auto-sync service
REM   2. Press Ctrl+C to stop
REM
REM Configuration:
REM   - SYNC_INTERVAL: How often to sync (in seconds, default: 300 = 5 minutes)
REM   - SYNC_MODE: full or incremental (default: incremental)
REM ============================================================================

echo.
echo ============================================================================
echo   RAD AI - Auto-Sync Service
echo ============================================================================
echo.
echo This will automatically sync your database every 5 minutes
echo.
echo Press Ctrl+C to stop at any time
echo.
pause

REM Configuration
set SYNC_INTERVAL=300
set SYNC_MODE=incremental

REM Check if .env file exists
if not exist ".env" (
    echo ERROR: .env file not found
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

echo.
echo ============================================================================
echo   Auto-Sync Started
echo ============================================================================
echo   Mode: %SYNC_MODE%
echo   Interval: %SYNC_INTERVAL% seconds (~5 minutes)
echo ============================================================================
echo.

:loop
    echo.
    echo [%date% %time%] Starting sync...
    echo.
    
    python manage.py sync_from_production --mode %SYNC_MODE%
    
    if errorlevel 1 (
        echo.
        echo [%date% %time%] ⚠️  Sync failed! Will retry in %SYNC_INTERVAL% seconds...
    ) else (
        echo.
        echo [%date% %time%] ✅ Sync completed successfully!
    )
    
    echo.
    echo Waiting %SYNC_INTERVAL% seconds before next sync...
    echo Press Ctrl+C to stop
    echo.
    
    timeout /t %SYNC_INTERVAL% /nobreak
    
    goto loop
