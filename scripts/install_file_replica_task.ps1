param(
    [Parameter(Mandatory = $true)][string]$PythonPath,
    [Parameter(Mandatory = $true)][string]$EnvironmentFile,
    [string]$TaskName = 'RADAI File Server Replica'
)

$ErrorActionPreference = 'Stop'
$replicaPython = (Resolve-Path -LiteralPath $PythonPath).Path
$replicaScript = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot 'file_replica_sync.py')).Path
$replicaEnvironment = (Resolve-Path -LiteralPath $EnvironmentFile).Path
$replicaDirectory = Join-Path $env:LOCALAPPDATA 'RADAI\FileReplica'
$null = New-Item -ItemType Directory -Path $replicaDirectory -Force
$replicaAccount = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$replicaAcl = Get-Acl -LiteralPath $replicaDirectory
$replicaAcl.SetAccessRuleProtection($true, $false)
foreach ($identity in @($replicaAccount, 'SYSTEM', 'BUILTIN\Administrators')) {
    $rule = New-Object System.Security.AccessControl.FileSystemAccessRule($identity, 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow')
    $replicaAcl.SetAccessRule($rule)
}
Set-Acl -LiteralPath $replicaDirectory -AclObject $replicaAcl
$replicaLocalEnvironment = Join-Path $replicaDirectory 'connector.env'
if ($replicaEnvironment -ne $replicaLocalEnvironment) {
    Copy-Item -LiteralPath $replicaEnvironment -Destination $replicaLocalEnvironment -Force
}

# A generated launcher keeps credentials out of process arguments and task XML.
function Quote-ReplicaLiteral([string]$Value) { return "'" + $Value.Replace("'", "''") + "'" }
$replicaLauncher = Join-Path $replicaDirectory 'run-connector.ps1'
$replicaLog = Join-Path $replicaDirectory 'connector.log'
$replicaLaunchText = @(
    '$ErrorActionPreference = ''Continue''',
    '$env:PYTHONUTF8 = ''1''',
    '$replicaMutex = New-Object System.Threading.Mutex($false, ''Local\RADAI.FileReplica.Connector'')',
    'if (-not $replicaMutex.WaitOne(0)) { exit 0 }',
    'try {',
    ('    & ' + (Quote-ReplicaLiteral $replicaPython) + ' -u ' + (Quote-ReplicaLiteral $replicaScript) + ' --env-file ' + (Quote-ReplicaLiteral $replicaLocalEnvironment) + ' --watch *> ' + (Quote-ReplicaLiteral $replicaLog)),
    '    $replicaExit = $LASTEXITCODE',
    '} finally { $replicaMutex.ReleaseMutex(); $replicaMutex.Dispose() }',
    'exit $replicaExit'
)
$replicaLaunchText | Set-Content -LiteralPath $replicaLauncher -Encoding UTF8
$replicaAction = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument ('-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "' + $replicaLauncher + '"')
$replicaTrigger = New-ScheduledTaskTrigger -AtLogOn -User $replicaAccount
$replicaPrincipal = New-ScheduledTaskPrincipal -UserId $replicaAccount -LogonType Interactive -RunLevel Limited
$replicaSettings = New-ScheduledTaskSettingsSet -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$replicaTask = New-ScheduledTask -Action $replicaAction -Trigger $replicaTrigger -Principal $replicaPrincipal -Settings $replicaSettings -Description 'Read-only RADAI folder catalogue connector. Runs while this Windows user is signed in; requires the office share and RADAI backend.'
$null = Register-ScheduledTask -TaskName $TaskName -InputObject $replicaTask -Force
Write-Output "Installed $TaskName. Starts at sign-in; run Start-ScheduledTask -TaskName '$TaskName' to start now. Log: $replicaLog"
