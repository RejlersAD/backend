# PowerShell script to create Windows Task Scheduler task for RAD AI Attendance Sync
# Run as Administrator

param(
    [string]$PythonPath = "C:\Python311\python.exe",
    [string]$ScriptPath = "C:\RadAI\sync-agent\timesheet_mirror_sync.py",
    [string]$WorkingDir = "C:\RadAI\sync-agent",
    [string]$TaskName = "RAD AI Attendance Sync",
    [int]$IntervalMinutes = 5
)

Write-Host "===============================================" -ForegroundColor Cyan
Write-Host "RAD AI Attendance Sync - Task Scheduler Setup" -ForegroundColor Cyan
Write-Host "===============================================`n" -ForegroundColor Cyan

# Check if running as administrator
$isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Host "[ERROR] This script must run as Administrator" -ForegroundColor Red
    Write-Host "Right-click PowerShell and select 'Run as Administrator'" -ForegroundColor Yellow
    exit 1
}

# Check if Python exists
if (-not (Test-Path $PythonPath)) {
    Write-Host "[ERROR] Python not found at: $PythonPath" -ForegroundColor Red
    Write-Host "Update -PythonPath parameter or install Python" -ForegroundColor Yellow
    exit 1
}

Write-Host "[OK] Python found: $PythonPath" -ForegroundColor Green

# Check if script exists
if (-not (Test-Path $ScriptPath)) {
    Write-Host "[ERROR] Sync script not found at: $ScriptPath" -ForegroundColor Red
    Write-Host "Copy timesheet_mirror_sync.py to $ScriptPath first" -ForegroundColor Yellow
    exit 1
}

Write-Host "[OK] Sync script found: $ScriptPath" -ForegroundColor Green

# Check if task already exists
$existingTask = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($existingTask) {
    Write-Host "[WARNING] Task '$TaskName' already exists" -ForegroundColor Yellow
    $response = Read-Host "Do you want to replace it? (yes/no)"
    if ($response.ToLower() -ne 'yes') {
        Write-Host "Cancelled by user" -ForegroundColor Yellow
        exit 0
    }
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "[OK] Existing task removed" -ForegroundColor Green
}

# Create the task
Write-Host "`nCreating scheduled task..." -ForegroundColor Cyan

# Task action - run in daemon mode
$action = New-ScheduledTaskAction `
    -Execute $PythonPath `
    -Argument "$ScriptPath --daemon --interval $($IntervalMinutes * 60)" `
    -WorkingDirectory $WorkingDir

# Task trigger - at startup
$trigger = New-ScheduledTaskTrigger -AtStartup

# Task settings
$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -RunOnlyIfNetworkAvailable `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1)

# Task principal - run as SYSTEM
$principal = New-ScheduledTaskPrincipal `
    -UserId "SYSTEM" `
    -LogonType ServiceAccount `
    -RunLevel Highest

# Register the task
try {
    Register-ScheduledTask `
        -TaskName $TaskName `
        -Action $action `
        -Trigger $trigger `
        -Settings $settings `
        -Principal $principal `
        -Description "RAD AI Attendance Sync Agent - Syncs biometric data from Matrix SQL Server to Railway" | Out-Null
    
    Write-Host "[OK] Task created successfully!" -ForegroundColor Green
}
catch {
    Write-Host "[ERROR] Failed to create task: $_" -ForegroundColor Red
    exit 1
}

# Verify task
$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($task) {
    Write-Host "`nTask Details:" -ForegroundColor Cyan
    Write-Host "  Name:     $TaskName" -ForegroundColor Gray
    Write-Host "  State:    $($task.State)" -ForegroundColor Gray
    Write-Host "  Command:  $PythonPath" -ForegroundColor Gray
    Write-Host "  Args:     $ScriptPath --daemon --interval $($IntervalMinutes * 60)" -ForegroundColor Gray
    Write-Host "  Interval: Every $IntervalMinutes minutes" -ForegroundColor Gray
}

# Ask if user wants to start the task now
Write-Host "`n" -NoNewline
$response = Read-Host "Do you want to start the task now? (yes/no)"
if ($response.ToLower() -eq 'yes') {
    try {
        Start-ScheduledTask -TaskName $TaskName
        Start-Sleep -Seconds 2
        
        $taskInfo = Get-ScheduledTaskInfo -TaskName $TaskName
        Write-Host "[OK] Task started!" -ForegroundColor Green
        Write-Host "Last Run: $($taskInfo.LastRunTime)" -ForegroundColor Gray
        
        Write-Host "`nMonitor the sync:" -ForegroundColor Cyan
        Write-Host "  1. Check log: Get-Content $WorkingDir\timesheet_sync.log -Tail 20" -ForegroundColor Gray
        Write-Host "  2. Check status: .\check_sync_agent.ps1" -ForegroundColor Gray
        Write-Host "  3. Railway logs: https://railway.app" -ForegroundColor Gray
    }
    catch {
        Write-Host "[ERROR] Failed to start task: $_" -ForegroundColor Red
    }
}

Write-Host "`n===============================================" -ForegroundColor Green
Write-Host "Setup Complete!" -ForegroundColor Green
Write-Host "===============================================" -ForegroundColor Green
Write-Host ""

exit 0
