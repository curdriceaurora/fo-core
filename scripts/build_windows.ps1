<#
.SYNOPSIS
  Build Windows executable and installer using PyInstaller + Inno Setup.

.DESCRIPTION
  1) Checks and installs WebView2 Runtime if not present
  2) Runs PyInstaller via scripts/build.py (with app manifest)
  3) Creates Tauri sidecar copy
  4) Builds an installer using Inno Setup (ISCC)

.PARAMETER Version
  Optional version override for installer naming (defaults to pyproject.toml)

.PARAMETER SkipBuild
  Skip PyInstaller build and only run ISCC
#>

param(
  [string]$Version = "",
  [switch]$SkipBuild
)

$ErrorActionPreference = "Stop"

function Get-Version {
  if ($Version -ne "") { return $Version }
  $py = @'
import tomllib
from pathlib import Path
print(tomllib.loads(Path("pyproject.toml").read_text())['project']['version'])
'@
  return (python -c $py).Trim()
}

$repoRoot = Split-Path -Parent $PSScriptRoot
Set-Location $repoRoot

# ---------------------------------------------------------------------------
# Check and install WebView2 Runtime (required by Tauri)
# ---------------------------------------------------------------------------
Write-Host "==> Checking WebView2 Runtime..."
$webview2Key = "HKLM:\SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
$webview2KeyUser = "HKCU:\SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
if (-not (Test-Path $webview2Key) -and -not (Test-Path $webview2KeyUser)) {
    Write-Host "    WebView2 Runtime not found. Downloading bootstrapper..."
    $bootstrapperUrl = "https://go.microsoft.com/fwlink/p/?LinkId=2124703"
    $bootstrapperPath = "$env:TEMP\MicrosoftEdgeWebview2Setup.exe"
    Invoke-WebRequest -Uri $bootstrapperUrl -OutFile $bootstrapperPath
    # TODO: Add hash verification for production builds.
    # The bootstrapper URL is a Microsoft redirect that always serves the latest
    # version, so the hash changes with each release. For CI/CD, pin a specific
    # version URL and verify against a known SHA-256:
    #   $expectedHash = "<sha256-of-pinned-version>"
    #   $actualHash = (Get-FileHash -Path $bootstrapperPath -Algorithm SHA256).Hash
    #   if ($actualHash -ne $expectedHash) {
    #       Remove-Item $bootstrapperPath
    #       throw "WebView2 bootstrapper hash mismatch: expected $expectedHash, got $actualHash"
    #   }
    Write-Host "    Installing WebView2 Runtime (silent)..."
    Start-Process -FilePath $bootstrapperPath -ArgumentList "/silent /install" -Wait
    Write-Host "    WebView2 Runtime installed." -ForegroundColor Green
} else {
    Write-Host "    WebView2 Runtime already installed." -ForegroundColor Green
}

# ---------------------------------------------------------------------------
# Application manifest path (embedded by winres via build.rs for Tauri builds;
# also passed explicitly when invoking PyInstaller directly)
# ---------------------------------------------------------------------------
$manifestFile = Join-Path $repoRoot "desktop\build\app.manifest"

if (-not $SkipBuild) {
  python scripts/build.py --clean --manifest-file $manifestFile
}

# ---------------------------------------------------------------------------
# Create Tauri sidecar copy (Tauri expects: file-organizer-backend-{target-triple})
# ---------------------------------------------------------------------------
Write-Host "==> Creating Tauri sidecar copy..."
$sidecarTriple = "x86_64-pc-windows-msvc"
$distDir = Join-Path $repoRoot "dist"
$sourceExe = Get-ChildItem -Path $distDir -Filter "file-organizer-*.exe" -ErrorAction SilentlyContinue |
    Where-Object { $_.Name -notmatch "backend" } |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1
if ($null -ne $sourceExe) {
    $sidecarName = "file-organizer-backend-${sidecarTriple}.exe"
    $sidecarPath = Join-Path $distDir $sidecarName
    Copy-Item -Path $sourceExe.FullName -Destination $sidecarPath -Force
    Write-Host "    Sidecar: $sidecarPath"
} else {
    Write-Host "WARNING: No source executable found in $distDir for sidecar copy." -ForegroundColor Yellow
}

$version = Get-Version
$iss = Join-Path $PSScriptRoot "build_windows.iss"
$iscc = "C:\Program Files (x86)\Inno Setup 6\ISCC.exe"

if (-not (Test-Path $iscc)) {
  Write-Host "ERROR: Inno Setup (ISCC) not found at $iscc" -ForegroundColor Red
  Write-Host "Install Inno Setup 6+ and retry." -ForegroundColor Yellow
  exit 1
}

& $iscc /DAppVersion=$version $iss
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

Write-Host "Installer built in dist/ (version $version)." -ForegroundColor Green
