@echo off
echo ========================================
echo DATABASE SYNC: Preprod to Production
echo ========================================
echo.
echo Source (Preprod):  tokaido.proxy.rlwy.net:59798
echo Target (Production): sakura.proxy.rlwy.net:31281
echo.
echo This will copy ALL data from preprod to production!
echo.
pause

echo.
echo [1/3] Checking pg_dump availability...
where pg_dump >nul 2>&1
if %errorlevel% neq 0 (
    echo ERROR: pg_dump not found!
    echo.
    echo Please install PostgreSQL client tools:
    echo https://www.postgresql.org/download/windows/
    echo.
    pause
    exit /b 1
)
echo OK: pg_dump found

echo.
echo [2/3] Dumping preprod database...
echo This may take a few minutes depending on data size...
pg_dump --no-owner --no-acl --clean --if-exists "postgresql://postgres:thAEPEWfKHTGvCwRfaeeichfMNxwdnbD@tokaido.proxy.rlwy.net:59798/railway" > preprod_backup.sql

if %errorlevel% neq 0 (
    echo ERROR: Failed to dump preprod database!
    pause
    exit /b 1
)
echo OK: Preprod database dumped to preprod_backup.sql

echo.
echo [3/3] Restoring to production database...
psql "postgresql://postgres:iBEjCnCHbjwnnIhyJhoRXGiUtXNHMjpp@sakura.proxy.rlwy.net:31281/railway" < preprod_backup.sql

if %errorlevel% neq 0 (
    echo ERROR: Failed to restore to production!
    echo.
    echo The backup file (preprod_backup.sql) is saved.
    echo You can manually restore it later.
    pause
    exit /b 1
)

echo.
echo ========================================
echo SUCCESS! Database synced successfully
echo ========================================
echo.
echo Preprod data has been copied to production (sakura)
echo Backup file saved: preprod_backup.sql
echo.
echo Next steps:
echo 1. Verify data in production database
echo 2. Set up Railway backend service
echo 3. Deploy to production
echo.
pause
