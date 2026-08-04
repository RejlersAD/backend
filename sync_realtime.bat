@echo off
REM Real-time Database Synchronization Script
echo ============================================================
echo   Database Synchronization - Real-time Mode
echo   Remote (preprod) to Local PostgreSQL
echo ============================================================
echo.
echo This will continuously sync the database at regular intervals.
echo Press Ctrl+C to stop the synchronization.
echo.

REM Activate virtual environment if exists
if exist venv\Scripts\activate.bat (
    call venv\Scripts\activate.bat
)

REM Run real-time synchronization
python db_sync_realtime.py

pause
