@echo off
REM One-time Database Synchronization Script
echo ============================================================
echo   Database Synchronization - One-time Sync
echo   Remote (preprod) to Local PostgreSQL
echo ============================================================
echo.

REM Activate virtual environment if exists
if exist venv\Scripts\activate.bat (
    call venv\Scripts\activate.bat
)

REM Run synchronization
python db_sync.py

pause
