@echo off
setlocal enabledelayedexpansion
echo ========================================
echo DATABASE SYNC: Preprod to Production (Docker Method)
echo ========================================
echo.
echo This script uses Docker to avoid PostgreSQL version mismatch issues.
echo.
echo Source (Preprod):  tokaido.proxy.rlwy.net:59798
echo Target (Production): sakura.proxy.rlwy.net:31281
echo.
echo WARNING: This will OVERWRITE ALL data in production database!
echo          All existing production data will be DELETED!
echo.
echo Make sure you have:
echo   1. Docker Desktop installed and running
echo   2. Backed up production database (if needed)
echo   3. Verified preprod data is correct
echo   4. Notified team members
echo.
set /p CONFIRM="Type YES in CAPITAL letters to proceed: "
if /i not "%CONFIRM%"=="YES" (
    echo.
    echo [CANCELLED] Operation cancelled for safety.
    echo.
    pause
    exit /b 0
)

echo.
echo [1/4] Checking Docker availability...
docker --version >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] Docker not found or not running!
    echo.
    echo Please install Docker Desktop:
    echo https://www.docker.com/products/docker-desktop/
    echo.
    echo Make sure Docker Desktop is running before executing this script.
    echo.
    pause
    exit /b 1
)
echo [OK] Docker found

echo.
echo [2/4] Dumping preprod database using PostgreSQL 18 (via Docker)...
echo This may take a few minutes depending on data size...

REM Use environment variables if available, otherwise use defaults
if not defined PREPROD_DB_URL (
    set "PREPROD_DB_URL=postgresql://postgres:thAEPEWfKHTGvCwRfaeeichfMNxwdnbD@tokaido.proxy.rlwy.net:59798/railway"
)

docker run --rm postgres:18 pg_dump --no-owner --no-acl --clean --if-exists "%PREPROD_DB_URL%" > preprod_backup.sql 2>dump_error.log

if %errorlevel% neq 0 (
    echo [ERROR] Failed to dump preprod database!
    echo.
    type dump_error.log 2>nul
    echo.
    echo Common issues:
    echo   1. Connection refused - Check network/VPN/firewall
    echo   2. Wrong credentials - Verify password
    echo   3. Docker not running - Start Docker Desktop
    echo.
    del dump_error.log 2>nul
    pause
    exit /b 1
)
del dump_error.log 2>nul

REM Verify backup file was created
if not exist preprod_backup.sql (
    echo [ERROR] Backup file was not created!
    pause
    exit /b 1
)

REM Check if file has content (at least 1KB)
for %%A in (preprod_backup.sql) do set FILESIZE=%%~zA
if %FILESIZE% LSS 1024 (
    echo [WARNING] Backup file is suspiciously small (%FILESIZE% bytes)
    echo This might indicate a failed dump.
    set /p CONTINUE="Continue anyway? (yes/no): "
    if /i not "!CONTINUE!"=="yes" (
        echo Operation cancelled.
        pause
        exit /b 1
    )
)

echo [OK] Preprod database dumped to preprod_backup.sql (%FILESIZE% bytes)

echo.
echo [3/4] Creating production backup before overwriting...
set "PROD_BACKUP=production_backup_%date:~-4%%date:~-10,2%%date:~-7,2%_%time:~0,2%%time:~3,2%%time:~6,2%.sql"
set "PROD_BACKUP=%PROD_BACKUP: =0%"

if not defined PROD_DB_URL (
    set "PROD_DB_URL=postgresql://postgres:iBEjCnCHbjwnnIhyJhoRXGiUtXNHMjpp@sakura.proxy.rlwy.net:31281/railway"
)

echo Creating safety backup of current production data...
docker run --rm postgres:18 pg_dump --no-owner --no-acl "%PROD_DB_URL%" > "%PROD_BACKUP%" 2>nul

if %errorlevel% equ 0 (
    echo [OK] Production backup saved: %PROD_BACKUP%
) else (
    echo [WARNING] Could not backup production (might be empty or unreachable)
)

echo.
echo [4/4] Restoring preprod data to production database (via Docker)...
echo This will OVERWRITE production data...

docker run --rm -i postgres:18 psql "%PROD_DB_URL%" < preprod_backup.sql 2>restore_error.log

if %errorlevel% neq 0 (
    echo [ERROR] Failed to restore to production!
    echo.
    type restore_error.log 2>nul
    echo.
    echo The backup files are saved:
    echo   - preprod_backup.sql (source data)
    echo   - %PROD_BACKUP% (production backup)
    echo.
    echo You can manually restore later using:
    echo   docker run --rm -i postgres:18 psql "connection_string" ^< preprod_backup.sql
    echo.
    del restore_error.log 2>nul
    pause
    exit /b 1
)
del restore_error.log 2>nul

echo.
echo ========================================
echo [SUCCESS] Database synced successfully!
echo ========================================
echo.
echo Preprod data has been copied to production (sakura)
echo.
echo Files created:
echo   - preprod_backup.sql (source data - keep this!)
if exist "%PROD_BACKUP%" (
    echo   - %PROD_BACKUP% (old production backup)
)
echo.
echo Next steps:
echo 1. Verify data in production database
echo 2. Test critical functionality
echo 3. Monitor Railway logs for any errors
echo 4. Notify team that production data was updated
echo.
echo If something went wrong, you can restore from:
echo   %PROD_BACKUP%
echo.
pause
