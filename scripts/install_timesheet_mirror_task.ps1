[CmdletBinding()]
param(
    [string]$PythonExe = '',
    [ValidateRange(1, 720)]
    [int]$RecoveryHours = 48,
    [ValidateRange(60, 86400)]
    [int]$IntervalSeconds = 300,
    [switch]$Replay30Days,
    [switch]$SyncUsers
)

$ErrorActionPreference = 'Stop'
$TaskName = 'RADAI Attendance Sync'
$ScriptDirectory = Split-Path -Parent $MyInvocation.MyCommand.Path
$BackendDirectory = Split-Path -Parent $ScriptDirectory
$AgentPath = Join-Path $ScriptDirectory 'timesheet_mirror_sync.py'
$AgentEnvPath = Join-Path $ScriptDirectory 'timesheet_mirror.env'
$RequirementsPath = Join-Path $BackendDirectory 'requirements-sync-agent.txt'

$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
$isAdministrator = $principal.IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator
)
if (-not $isAdministrator) {
    throw 'Run PowerShell as Administrator, then run this installer again.'
}

foreach ($requiredPath in @($AgentPath, $AgentEnvPath, $RequirementsPath)) {
    if (-not (Test-Path -LiteralPath $requiredPath -PathType Leaf)) {
        throw "Required file is missing: $requiredPath"
    }
}

if (-not $PythonExe) {
    $pythonCommand = (Get-Command python -ErrorAction Stop).Source
    $PythonExe = (& $pythonCommand -c 'import sys; print(sys.executable)' | Select-Object -Last 1).Trim()
}
$PythonExe = (Resolve-Path -LiteralPath $PythonExe -ErrorAction Stop).Path

Write-Host "Backend directory : $BackendDirectory"
Write-Host "Python executable : $PythonExe"
Write-Host "Scheduled task    : $TaskName"

& $PythonExe --version
if ($LASTEXITCODE -ne 0) {
    throw 'The selected Python executable could not run.'
}

Write-Host "`n[1/5] Installing sync-agent dependencies..."
& $PythonExe -m pip install -r $RequirementsPath
if ($LASTEXITCODE -ne 0) {
    throw "Dependency installation failed with exit code $LASTEXITCODE."
}

Write-Host "`n[2/5] Checking Matrix SQL Server and Production authentication..."
& $PythonExe $AgentPath --env-file $AgentEnvPath --check --hours 24
if ($LASTEXITCODE -ne 0) {
    throw 'Preflight failed. The scheduled task was not created.'
}

if ($Replay30Days) {
    Write-Host "`n[3/5] Replaying the last 30 days in idempotent batches..."
    & $PythonExe $AgentPath `
        --env-file $AgentEnvPath `
        --hours 720 `
        --batch-size 100 `
        --max-events-per-run 100000 `
        --allow-large-replay
    if ($LASTEXITCODE -ne 0) {
        throw 'The 30-day attendance replay failed. The scheduled task was not created.'
    }
} else {
    Write-Host "`n[3/5] Historical replay not requested."
}

if ($SyncUsers) {
    Write-Host "`n[4/5] Synchronizing the Matrix employee master..."
    & $PythonExe $AgentPath `
        --env-file $AgentEnvPath `
        --users `
        --hours 2 `
        --batch-size 100
    if ($LASTEXITCODE -ne 0) {
        throw 'Employee-master synchronization failed. The scheduled task was not created.'
    }
} else {
    Write-Host "`n[4/5] Employee-master synchronization not requested."
}

Write-Host "`n[5/5] Priming the checkpoint and registering the startup task..."
& $PythonExe $AgentPath `
    --env-file $AgentEnvPath `
    --hours $RecoveryHours `
    --prime-state
if ($LASTEXITCODE -ne 0) {
    throw 'Checkpoint priming failed. The scheduled task was not created.'
}

$arguments = @(
    ('"{0}"' -f $AgentPath)
    '--env-file'
    ('"{0}"' -f $AgentEnvPath)
    '--hours'
    $RecoveryHours
    '--batch-size'
    '100'
    '--watch'
    '--interval'
    $IntervalSeconds
) -join ' '

$action = New-ScheduledTaskAction `
    -Execute $PythonExe `
    -Argument $arguments `
    -WorkingDirectory $BackendDirectory
$trigger = New-ScheduledTaskTrigger -AtStartup
$settings = New-ScheduledTaskSettingsSet `
    -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -WakeToRun

Register-ScheduledTask `
    -TaskName $TaskName `
    -Description 'Secure Matrix biometric attendance bridge to RADAI Production.' `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -User 'SYSTEM' `
    -RunLevel Highest `
    -Force | Out-Null

Start-ScheduledTask -TaskName $TaskName
Start-Sleep -Seconds 10

$task = Get-ScheduledTask -TaskName $TaskName
$taskInfo = Get-ScheduledTaskInfo -TaskName $TaskName
Write-Host "`nInstallation complete."
Write-Host "Task state       : $($task.State)"
Write-Host "Task account     : $($task.Principal.UserId)"
Write-Host "Last task result : 0x$('{0:X}' -f $taskInfo.LastTaskResult)"
Write-Host 'Expected running result: 0x41301 (continuous watcher is active).'
Write-Host 'Open RADAI Production > Employees > Time Sheet > Setup and refresh the status.'
