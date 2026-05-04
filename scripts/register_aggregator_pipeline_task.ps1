[CmdletBinding(SupportsShouldProcess=$true)]
param(
  [string]$TaskName = "Miru-Log Aggregator Pipeline",
  [string]$RepoRoot = "",
  [string]$DataRoot = "",
  [string]$DailyTime = "23:55",
  [string[]]$PipelineArgs = @(),
  [switch]$WithNotify,
  [switch]$AtLogOn,
  [switch]$Delete
)

$ErrorActionPreference = 'Stop'

function Resolve-RepoRoot {
  if ([string]::IsNullOrWhiteSpace($RepoRoot)) {
    return (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
  }
  return (Resolve-Path $RepoRoot).Path
}

function Quote-ForCmd([string]$value) {
  return '"' + ($value -replace '"', '""') + '"'
}

function Resolve-OptionalPath([string]$pathValue) {
  if ([string]::IsNullOrWhiteSpace($pathValue)) {
    return ""
  }
  if (-not (Test-Path $pathValue)) {
    throw "Path not found: $pathValue"
  }
  return (Resolve-Path $pathValue).Path
}

$resolvedRepoRoot = Resolve-RepoRoot
$batchPath = Join-Path $resolvedRepoRoot "scripts\run_pipeline_aggregator.bat"
if (-not (Test-Path $batchPath)) {
  throw "Batch file not found: $batchPath"
}

$resolvedDataRoot = Resolve-OptionalPath $DataRoot
$currentUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name

if ($Delete) {
  if ($PSCmdlet.ShouldProcess($TaskName, "Unregister scheduled task")) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction Stop
    Write-Host "Deleted scheduled task: $TaskName"
  }
  return
}

$batchArgs = New-Object System.Collections.Generic.List[string]
if (-not [string]::IsNullOrWhiteSpace($resolvedDataRoot)) {
  $batchArgs.Add((Quote-ForCmd $resolvedDataRoot))
}
if ($WithNotify) {
  $batchArgs.Add("--with-notify")
}
foreach ($arg in $PipelineArgs) {
  if ([string]::IsNullOrWhiteSpace($arg)) {
    continue
  }
  if ($arg.Contains(' ')) {
    $batchArgs.Add((Quote-ForCmd $arg))
  } else {
    $batchArgs.Add($arg)
  }
}

$batchCommand = (Quote-ForCmd $batchPath)
if ($batchArgs.Count -gt 0) {
  $batchCommand += " " + [string]::Join(' ', $batchArgs)
}

$cmdArguments = "/c " + (Quote-ForCmd ("cd /d " + (Quote-ForCmd $resolvedRepoRoot) + " && call " + $batchCommand))

if ($AtLogOn) {
  $trigger = New-ScheduledTaskTrigger -AtLogOn -User $currentUser
  $scheduleText = "At logon"
} else {
  $parsedTime = [datetime]::ParseExact($DailyTime, 'HH:mm', [System.Globalization.CultureInfo]::InvariantCulture)
  $trigger = New-ScheduledTaskTrigger -Daily -At $parsedTime
  $scheduleText = "Daily at $DailyTime"
}

$action = New-ScheduledTaskAction -Execute "cmd.exe" -Argument $cmdArguments
$principal = New-ScheduledTaskPrincipal -UserId $currentUser -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$description = "Run Miru-Log aggregator pipeline on the aggregation PC."

Write-Host "TaskName: $TaskName"
Write-Host "User: $currentUser"
Write-Host "Schedule: $scheduleText"
Write-Host "RepoRoot: $resolvedRepoRoot"
if ($resolvedDataRoot) {
  Write-Host "DataRoot: $resolvedDataRoot"
} else {
  Write-Host "DataRoot: use ANALYZER_DATA_ROOT from .env"
}
Write-Host "Command: cmd.exe $cmdArguments"

if ($PSCmdlet.ShouldProcess($TaskName, "Register scheduled task")) {
  Register-ScheduledTask `
    -TaskName $TaskName `
    -Action $action `
    -Trigger $trigger `
    -Principal $principal `
    -Settings $settings `
    -Description $description `
    -Force | Out-Null

  Write-Host "Registered scheduled task: $TaskName"
}
