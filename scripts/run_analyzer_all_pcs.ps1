param(
  [Parameter(Mandatory=$true)]
  [string]$ArchiveRootParent,

  # Optional: parent folder that contains per-PC capture folders.
  # If set, CAPTURE_ROOT will be set to <CaptureRootParent>/<PC> for each run.
  [string]$CaptureRootParent = "",

  # analyze: run analyzer.py for each PC folder
  # list: show pending (unanalyzed) count per PC folder and exit
  [string]$Mode = "analyze",

  [int]$Limit = 50,

  # Use string to avoid PowerShell host-specific bool/switch argument quirks.
  # Accepts: true/false, 1/0, yes/no, on/off (case-insensitive)
  [string]$UntilEmpty = "true",

  [string]$PythonExe = "",

  [string]$RepoRoot = ""
)

$ErrorActionPreference = 'Stop'

function Resolve-FullPath([string]$path) {
  if ([string]::IsNullOrWhiteSpace($path)) { return $path }
  return (Resolve-Path -Path $path).Path
}

function Convert-ToBool([string]$value, [bool]$defaultValue) {
  if ($null -eq $value) { return $defaultValue }
  $v = $value.Trim().ToLowerInvariant()
  if ($v -eq "") { return $defaultValue }
  switch ($v) {
    "1" { return $true }
    "0" { return $false }
    "true" { return $true }
    "false" { return $false }
    "yes" { return $true }
    "no" { return $false }
    "on" { return $true }
    "off" { return $false }
    default { return $defaultValue }
  }
}

if ([string]::IsNullOrWhiteSpace($RepoRoot)) {
  $RepoRoot = Split-Path -Parent $PSScriptRoot
}
$RepoRoot = Resolve-FullPath $RepoRoot

if ([string]::IsNullOrWhiteSpace($PythonExe)) {
  $venvPython = Join-Path $RepoRoot ".venv\Scripts\python.exe"
  if (Test-Path $venvPython) {
    $PythonExe = $venvPython
  } else {
    $PythonExe = "python"
  }
}

$ArchiveRootParentFull = Resolve-FullPath $ArchiveRootParent
if (-not (Test-Path $ArchiveRootParentFull)) {
  throw "ArchiveRootParent not found: $ArchiveRootParentFull"
}

$CaptureRootParentFull = ""
if (-not [string]::IsNullOrWhiteSpace($CaptureRootParent)) {
  $CaptureRootParentFull = Resolve-FullPath $CaptureRootParent
  if (-not (Test-Path $CaptureRootParentFull)) {
    throw "CaptureRootParent not found: $CaptureRootParentFull"
  }
}

$untilEmptyBool = Convert-ToBool $UntilEmpty $true

$pcDirs = Get-ChildItem -Path $ArchiveRootParentFull -Directory | Sort-Object Name
if (-not $pcDirs -or $pcDirs.Count -eq 0) {
  throw "No PC folders found under: $ArchiveRootParentFull"
}

Write-Host "Running analyzer sequentially for $($pcDirs.Count) PC folders..." -ForegroundColor Cyan
Write-Host "RepoRoot: $RepoRoot"
Write-Host "PythonExe: $PythonExe"
Write-Host "ArchiveRootParent: $ArchiveRootParentFull"
if ($CaptureRootParentFull -ne "") { Write-Host "CaptureRootParent: $CaptureRootParentFull" }
Write-Host "Mode: $Mode"
Write-Host "Limit: $Limit  UntilEmpty: $untilEmptyBool" 

Push-Location $RepoRoot
try {
  if ($Mode.Trim().ToLowerInvariant() -eq "list") {
  $pendingScript = Join-Path $RepoRoot "scripts\pending_counts.py"
  if (-not (Test-Path $pendingScript)) {
    throw "Missing helper script: $pendingScript"
  }

    $rows = @()
    foreach ($pcDir in $pcDirs) {
      $pcName = $pcDir.Name
      $archiveRoot = $pcDir.FullName

      $dbPath = Join-Path $archiveRoot "mirulog.db"

      $out = & $PythonExe $pendingScript --db $dbPath
      $raw = ("$out").Trim()

      $pending = $null
      if ($raw -match '^\d+$') {
        $pending = [int]$raw
      }

      $rows += [PSCustomObject]@{
        PC = $pcName
        Pending = $pending
        ArchiveRoot = $archiveRoot
      }
    }

    $rows | Sort-Object -Property PC | Format-Table -AutoSize
    return
  }

  foreach ($pcDir in $pcDirs) {
    $pcName = $pcDir.Name
    $archiveRoot = $pcDir.FullName

    Write-Host "\n=== [$pcName] ARCHIVE_ROOT=$archiveRoot ===" -ForegroundColor Yellow

    $env:ARCHIVE_ROOT = $archiveRoot
    if ($CaptureRootParentFull -ne "") {
      $env:CAPTURE_ROOT = (Join-Path $CaptureRootParentFull $pcName)
    }

    $argsList = @("$RepoRoot\analyzer.py")
    if ($Limit -gt 0) {
      $argsList += @('--limit', "$Limit")
    }
    if ($untilEmptyBool) {
      $argsList += '--until-empty'
    }

    & $PythonExe @argsList
    if ($LASTEXITCODE -ne 0) {
      throw "Analyzer failed for PC '$pcName' (exit code=$LASTEXITCODE)"
    }
  }

  Write-Host "\nDone." -ForegroundColor Green
}
finally {
  Pop-Location
}
