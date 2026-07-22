#!/usr/bin/env pwsh
<#
.SYNOPSIS
    RAD AI Attendance Sync Agent - Health Check & Restart Script (Windows)

.DESCRIPTION
    Checks the status of the RAD AI attendance sync agent and restarts it if needed.
    Can be run manually or scheduled via Task Scheduler for automatic recovery.

.PARAMETER Check
    Only check status, don't restart

.PARAMETER Force
    Force restart even if agent appears healthy

.PARAMETER Hours
    Number of hours to sync when restarting (default: 48)

.EXAMPLE
    .\restart_sync_agent.ps1
    Check status and restart if needed

.EXAMPLE
    .\restart_sync_agent.ps1 -Check
    Only check status

.EXAMPLE
    .\restart_sync_agent.ps1 -Force -Hours 72
    Force restart and sync last 72 hours

.NOTES
    Author: RAD AI DevOps
    Date: 2026-07-22
    Version: 1.0
#>

param(
    [Parameter(HelpMessage="Only check status, don't restart")]
    [switch]$Check,
    
    [Parameter(HelpMessage="Force restart even if healthy")]
    [switch]$Force,
    
    [Parameter(HelpMessage="Hours to sync (default: 48)")]
    [int]$Hours = 48,
    
    [Parameter(HelpMessage="Task Scheduler task name")]
    [string]$TaskName = "RAD AI Attendance Sync",
    
    [Parameter(HelpMessage="Path to sync agent script")]
    [string]$AgentPath = "C:\RadAI\sync-agent\timesheet_mirror_sync.py",
    
    [Parameter(HelpMessage="Path to Python executable")]
    [string]$PythonPath = "C:\Python311\python.exe"
)

# Colors for output
function Write-Success { param($Message) Write-Host "✅ $Message" -ForegroundColor Green }
function Write-Warning { param($Message) Write-Host "⚠️  $Message" -ForegroundColor Yellow }
function Write-Error { param($Message) Write-Host "❌ $Message" -ForegroundColor Red }
function Write-Info { param($Message) Write-Host "ℹ️  $Message" -ForegroundColor Cyan }

# Header
Write-Host ""
Write-Host "╔═══════════════════════════════════════════════════════════════╗" -ForegroundColor Cyan
Write-Host "║       RAD AI Attendance Sync Agent - Health Check            ║" -ForegroundColor Cyan
Write-Host "╚═══════════════════════════════════════════════════════════════╝" -ForegroundColor Cyan
Write-Host ""

# Check if running as administrator
$isAdmin = ([Security.Principal.WindowsPrincipal] [Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) {
    Write-Warning "Not running as administrator. Some operations may fail."
}

# 1. Check Task Scheduler
Write-Info "Checking Task Scheduler..."
try {
    $task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
    
    if ($null -eq $task) {
        Write-Error "Task '$TaskName' not found in Task Scheduler"
        Write-Info "Create the task manually or run: Register-ScheduledTask -TaskName '$TaskName' ..."
        exit 1
    }
    
    $taskInfo = Get-ScheduledTaskInfo -TaskName $TaskName
    $taskState = $task.State
    $lastRunTime = $taskInfo.LastRunTime
    $lastResult = $taskInfo.LastTaskResult
    
    Write-Success "Task found: $TaskName"
    Write-Host "   State: $taskState" -ForegroundColor Gray
    Write-Host "   Last Run: $lastRunTime" -ForegroundColor Gray
    Write-Host "   Last Result: $lastResult (0 = success)" -ForegroundColor Gray
    
    if ($taskState -eq "Disabled") {
        Write-Warning "Task is DISABLED"
        if (-not $Check -and $Force) {
            Write-Info "Enabling task..."
            Enable-ScheduledTask -TaskName $TaskName | Out-Null
            Write-Success "Task enabled"
        }
    }
    
} catch {
    Write-Error "Failed to check Task Scheduler: $_"
    exit 1
}

# 2. Check sync agent script
Write-Host ""
Write-Info "Checking sync agent script..."
if (Test-Path $AgentPath) {
    Write-Success "Script found: $AgentPath"
    $scriptSize = (Get-Item $AgentPath).Length
    Write-Host "   Size: $scriptSize bytes" -ForegroundColor Gray
} else {
    Write-Error "Script not found: $AgentPath"
    Write-Info "Update -AgentPath parameter or restore the script"
    exit 1
}

# 3. Check Python
Write-Host ""
Write-Info "Checking Python..."
if (Test-Path $PythonPath) {
    $pythonVersion = & $PythonPath --version 2>&1
    Write-Success "Python found: $pythonVersion"
} else {
    Write-Warning "Python not found at: $PythonPath"
    Write-Info "Trying system Python..."
    try {
        $pythonVersion = python --version 2>&1
        Write-Success "System Python: $pythonVersion"
        $PythonPath = "python"
    } catch {
        Write-Error "Python not found. Install Python 3.8+ or update -PythonPath parameter"
        exit 1
    }
}

# 4. Check Railway backend connectivity
Write-Host ""
Write-Info "Checking Railway backend connectivity..."
$railwayUrl = $env:RAILWAY_BACKEND_URL
if ([string]::IsNullOrEmpty($railwayUrl)) {
    $railwayUrl = "https://aiflowbackend-production.up.railway.app"
    Write-Warning "RAILWAY_BACKEND_URL not set, using default: $railwayUrl"
}

try {
    $response = Invoke-WebRequest -Uri "$railwayUrl/api/v1/health/" -Method GET -TimeoutSec 10 -UseBasicParsing
    if ($response.StatusCode -eq 200) {
        Write-Success "Railway backend is reachable"
        Write-Host "   URL: $railwayUrl" -ForegroundColor Gray
    } else {
        Write-Warning "Railway backend returned status: $($response.StatusCode)"
    }
} catch {
    Write-Error "Cannot reach Railway backend: $_"
    Write-Info "Check network connection and firewall settings"
}

# 5. Check API key
Write-Host ""
Write-Info "Checking API key configuration..."
$apiKey = $env:TIMESHEET_MIRROR_API_KEY
if ([string]::IsNullOrEmpty($apiKey)) {
    Write-Error "TIMESHEET_MIRROR_API_KEY environment variable not set"
    Write-Info "Set via System Properties → Environment Variables or Task Scheduler task properties"
} else {
    $keyLength = $apiKey.Length
    $keyPreview = $apiKey.Substring(0, [Math]::Min(8, $keyLength)) + "..."
    Write-Success "API key configured: $keyPreview (length: $keyLength)"
}

# 6. Check if script is currently running
Write-Host ""
Write-Info "Checking if sync agent process is running..."
$pythonProcesses = Get-Process -Name python* -ErrorAction SilentlyContinue | 
    Where-Object { $_.CommandLine -like "*timesheet_mirror_sync*" }

if ($pythonProcesses) {
    Write-Success "Sync agent process is running (PID: $($pythonProcesses.Id -join ', '))"
    $runningTime = (Get-Date) - $pythonProcesses[0].StartTime
    Write-Host "   Running for: $($runningTime.ToString('hh\:mm\:ss'))" -ForegroundColor Gray
    
    if ($Force) {
        Write-Warning "Force flag set - will restart anyway"
    } elseif (-not $Check) {
        Write-Info "Agent is running. Use -Force to restart anyway."
        exit 0
    }
} else {
    Write-Warning "Sync agent process is NOT running"
}

# 7. Decision: Restart or not?
Write-Host ""
Write-Host "═══════════════════════════════════════════════════════════════" -ForegroundColor Cyan

if ($Check) {
    Write-Info "Check mode - no restart performed"
    exit 0
}

if (-not $Force -and $pythonProcesses) {
    Write-Info "Agent is running and -Force not specified. Exiting."
    exit 0
}

# 8. Restart the agent
Write-Host ""
Write-Info "Restarting sync agent..."
Write-Host "   Command: $PythonPath $AgentPath --hours $Hours" -ForegroundColor Gray

try {
    # Kill existing processes if any
    if ($pythonProcesses) {
        Write-Info "Stopping existing sync agent processes..."
        $pythonProcesses | Stop-Process -Force
        Start-Sleep -Seconds 2
        Write-Success "Existing processes stopped"
    }
    
    # Start new process
    if ($task) {
        Write-Info "Starting via Task Scheduler..."
        Start-ScheduledTask -TaskName $TaskName
        Start-Sleep -Seconds 3
        
        # Verify task started
        $taskInfo = Get-ScheduledTaskInfo -TaskName $TaskName
        if ($taskInfo.LastTaskResult -eq 267009) {  # Task is running
            Write-Success "Task started successfully"
        } else {
            Write-Warning "Task may not have started. Last result: $($taskInfo.LastTaskResult)"
        }
    } else {
        # Manual start (if task not found)
        Write-Info "Starting manually..."
        $process = Start-Process -FilePath $PythonPath -ArgumentList "$AgentPath --hours $Hours" -PassThru -WindowStyle Hidden
        Start-Sleep -Seconds 3
        
        if ($process -and -not $process.HasExited) {
            Write-Success "Process started (PID: $($process.Id))"
        } else {
            Write-Error "Process failed to start or exited immediately"
            exit 1
        }
    }
    
    Write-Host ""
    Write-Success "Sync agent restart initiated"
    Write-Info "Monitor progress:"
    Write-Host "   1. Check Railway logs: https://railway.app" -ForegroundColor Gray
    Write-Host "   2. Check frontend: https://www.radai.ae/hr/employees" -ForegroundColor Gray
    Write-Host "   3. Look for 'POST /api/v1/timesheet/mirror/ingest/ 200' in logs" -ForegroundColor Gray
    Write-Host ""
    Write-Success "Data should update within 15-30 minutes"
    
} catch {
    Write-Error "Failed to restart sync agent: $_"
    exit 1
}

Write-Host ""
Write-Host "╔═══════════════════════════════════════════════════════════════╗" -ForegroundColor Green
Write-Host "║                     ✅ RESTART COMPLETE                       ║" -ForegroundColor Green
Write-Host "╚═══════════════════════════════════════════════════════════════╝" -ForegroundColor Green
Write-Host ""

exit 0
