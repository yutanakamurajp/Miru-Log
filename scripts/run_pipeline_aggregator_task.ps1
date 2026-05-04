param(
  [string]$RepoRoot = "",
  [string]$DataRoot = "",
  [string[]]$BatchArgs = @()
)

$ErrorActionPreference = 'Stop'

if ([string]::IsNullOrWhiteSpace($RepoRoot)) {
  $RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
} else {
  $RepoRoot = (Resolve-Path $RepoRoot).Path
}

$batchPath = Join-Path $RepoRoot "scripts\run_pipeline_aggregator.bat"
if (-not (Test-Path $batchPath)) {
  throw "Batch file not found: $batchPath"
}

$logDir = Join-Path $RepoRoot "logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$logPath = Join-Path $logDir "aggregator-task.log"

"=== $(Get-Date -Format s) task start ===" | Out-File -FilePath $logPath -Encoding utf8 -Append
"RepoRoot=$RepoRoot" | Out-File -FilePath $logPath -Encoding utf8 -Append
if (-not [string]::IsNullOrWhiteSpace($DataRoot)) {
  "DataRoot=$DataRoot" | Out-File -FilePath $logPath -Encoding utf8 -Append
}
if ($BatchArgs.Count -gt 0) {
  "BatchArgs=$($BatchArgs -join ' ')" | Out-File -FilePath $logPath -Encoding utf8 -Append
}

Push-Location $RepoRoot
try {
  $cmdParts = New-Object System.Collections.Generic.List[string]
  $cmdParts.Add('call')
  $cmdParts.Add(('"' + $batchPath + '"'))

  if (-not [string]::IsNullOrWhiteSpace($DataRoot)) {
    $cmdParts.Add(('"' + $DataRoot + '"'))
  }
  foreach ($arg in $BatchArgs) {
    if (-not [string]::IsNullOrWhiteSpace($arg)) {
      if ($arg.Contains(' ')) {
        $cmdParts.Add(('"' + $arg.Replace('"', '""') + '"'))
      } else {
        $cmdParts.Add($arg)
      }
    }
  }

  $stdoutPath = Join-Path $logDir 'aggregator-task.stdout.log'
  $stderrPath = Join-Path $logDir 'aggregator-task.stderr.log'
  $cmdArguments = '/d /c ' + ('"' + [string]::Join(' ', $cmdParts) + '"')

  $process = Start-Process -FilePath 'cmd.exe' -ArgumentList $cmdArguments -WorkingDirectory $RepoRoot -Wait -PassThru -NoNewWindow -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath

  if (Test-Path $stdoutPath) {
    Get-Content $stdoutPath | Out-File -FilePath $logPath -Encoding utf8 -Append
  }
  if (Test-Path $stderrPath) {
    Get-Content $stderrPath | Out-File -FilePath $logPath -Encoding utf8 -Append
  }

  $exitCode = $process.ExitCode
  "ExitCode=$exitCode" | Out-File -FilePath $logPath -Encoding utf8 -Append
  if ($exitCode -ne 0) {
    throw "run_pipeline_aggregator.bat failed with exit code $exitCode"
  }
}
finally {
  Pop-Location
  "=== $(Get-Date -Format s) task end ===" | Out-File -FilePath $logPath -Encoding utf8 -Append
}