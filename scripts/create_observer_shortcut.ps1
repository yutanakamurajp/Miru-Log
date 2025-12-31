param(
  [string]$ExePath = "",
  [string]$ShortcutPath = "",
  [string]$Arguments = ""
)

$ErrorActionPreference = 'Stop'

function Resolve-DefaultExePath {
  $repoRoot = (Resolve-Path (Join-Path $PSScriptRoot ".."))
  return (Join-Path $repoRoot "dist\mirulog-observer.exe")
}

if ([string]::IsNullOrWhiteSpace($ExePath)) {
  $ExePath = Resolve-DefaultExePath
}
$ExePath = (Resolve-Path $ExePath).Path

$workingDir = Split-Path -Parent $ExePath

if ([string]::IsNullOrWhiteSpace($ShortcutPath)) {
  $desktop = [Environment]::GetFolderPath('Desktop')
  $ShortcutPath = Join-Path $desktop "MiruLog Observer.lnk"
}

$shortcutDir = Split-Path -Parent $ShortcutPath
if (-not [string]::IsNullOrWhiteSpace($shortcutDir)) {
  New-Item -ItemType Directory -Force -Path $shortcutDir | Out-Null
}

if (-not (Test-Path $ExePath)) {
  throw "EXE not found: $ExePath"
}

$wsh = New-Object -ComObject WScript.Shell
$sc = $wsh.CreateShortcut($ShortcutPath)
$sc.TargetPath = $ExePath
$sc.WorkingDirectory = $workingDir
$sc.Arguments = $Arguments
$sc.IconLocation = "$ExePath,0"
$sc.Save()

Write-Host "Created shortcut: $ShortcutPath"
Write-Host "  Target: $ExePath"
Write-Host "  Start in: $workingDir"
if ($Arguments) {
  Write-Host "  Args: $Arguments"
}
