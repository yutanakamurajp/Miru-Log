param(
  [switch]$NoConsole = $false,
  [string]$Name = "mirulog-observer"
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

$py = Join-Path $repoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
  throw "Python venv not found at $py. Create it first: py -3.10 -m venv .venv"
}

# Build output directories
$distDir = Join-Path $repoRoot "dist"
$buildDir = Join-Path $repoRoot "build"

$preserveDir = Join-Path $repoRoot "tmp\dist_preserve"

# Clean previous builds
if (Test-Path $preserveDir) { Remove-Item -Recurse -Force $preserveDir }

if (Test-Path $distDir) {
  New-Item -ItemType Directory -Force -Path $preserveDir | Out-Null

  $dotenv = Join-Path $distDir ".env"
  if (Test-Path $dotenv) {
    Copy-Item -Force $dotenv (Join-Path $preserveDir ".env")
  }

  Get-ChildItem -Path $distDir -Filter "*.lnk" -File -ErrorAction SilentlyContinue |
    ForEach-Object { Copy-Item -Force $_.FullName (Join-Path $preserveDir $_.Name) }

  Remove-Item -Recurse -Force $distDir
}
if (Test-Path $buildDir) { Remove-Item -Recurse -Force $buildDir }

$args = @(
  "-m", "PyInstaller",
  "--onefile",
  "--name", $Name,
  "--distpath", $distDir,
  "--workpath", $buildDir,
  "observer.py"
)

if ($NoConsole) {
  $args = @(
    "-m", "PyInstaller",
    "--onefile",
    "--noconsole",
    "--name", $Name,
    "--distpath", $distDir,
    "--workpath", $buildDir,
    "observer.py"
  )
}

& $py @args

# Restore preserved files and/or seed a minimal dist/.env
$seedEnv = Join-Path $repoRoot "scripts\observer.env"
$distEnv = Join-Path $distDir ".env"

if (Test-Path (Join-Path $preserveDir ".env")) {
  Copy-Item -Force (Join-Path $preserveDir ".env") $distEnv
} elseif (Test-Path $seedEnv) {
  Copy-Item -Force $seedEnv $distEnv
}

if (Test-Path $preserveDir) {
  Get-ChildItem -Path $preserveDir -Filter "*.lnk" -File -ErrorAction SilentlyContinue |
    ForEach-Object { Copy-Item -Force $_.FullName (Join-Path $distDir $_.Name) }
  Remove-Item -Recurse -Force $preserveDir
}

Write-Host "Built: $distDir\$Name.exe"
