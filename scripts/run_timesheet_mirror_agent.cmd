@echo off
setlocal

cd /d "%~dp0.."
set "AGENT_ENV=scripts\timesheet_mirror.env"
set "AGENT_LOG=scripts\timesheet_mirror_agent.log"
set "AGENT_LOG_OLD=scripts\timesheet_mirror_agent.log.1"
set "PYTHON_EXE=%LocalAppData%\Programs\Python\Python313\python.exe"
set "PYTHONUTF8=1"

if not exist "%AGENT_ENV%" (
  echo [%date% %time%] Missing %CD%\%AGENT_ENV%>>"%AGENT_LOG%"
  exit /b 2
)

if not exist "%PYTHON_EXE%" (
  for /f "delims=" %%P in ('where python 2^>nul') do if not defined PYTHON_FOUND set "PYTHON_FOUND=%%P"
  if defined PYTHON_FOUND set "PYTHON_EXE=%PYTHON_FOUND%"
)

if not exist "%PYTHON_EXE%" (
  echo [%date% %time%] Python executable was not found.>>"%AGENT_LOG%"
  exit /b 3
)

for %%L in ("%AGENT_LOG%") do if %%~zL GTR 10485760 move /y "%AGENT_LOG%" "%AGENT_LOG_OLD%" >nul
echo [%date% %time%] Starting Time Sheet mirror agent.>>"%AGENT_LOG%"

"%PYTHON_EXE%" scripts\timesheet_mirror_sync.py ^
  --env-file "%AGENT_ENV%" ^
  --hours 2 ^
  --batch-size 100 ^
  --watch ^
  --interval 300 >>"%AGENT_LOG%" 2>&1

set "AGENT_EXIT=%ERRORLEVEL%"
echo [%date% %time%] Time Sheet mirror agent exited with code %AGENT_EXIT%.>>"%AGENT_LOG%"
exit /b %AGENT_EXIT%
