<#
.SYNOPSIS
    Build Desqueeze for Windows: portable .exe, installer, or both.

.DESCRIPTION
    Run this on Windows, from anywhere in the repository:

        powershell -ExecutionPolicy Bypass -File PC\tools\build_windows.ps1

    It creates a virtual environment, installs the Python dependencies,
    downloads ExifTool, FFmpeg and DNGLab into PC\vendor\windows, runs the
    tests, and then packages.  Artifacts land in PC\dist.

    Inno Setup is only needed for the installer; without it the script still
    produces the portable build and says why the installer was skipped.

.PARAMETER Target
    portable, installer, or both (default).

.PARAMETER SkipTests
    Package without running the test suite first.  Not recommended.
#>

[CmdletBinding()]
param(
    [ValidateSet('portable', 'installer', 'both')]
    [string]$Target = 'both',
    [switch]$SkipTests
)

$ErrorActionPreference = 'Stop'

$ToolsDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$PcDir    = Split-Path -Parent $ToolsDir
$RepoDir  = Split-Path -Parent $PcDir
$VenvDir  = Join-Path $PcDir '.venv'
$DistDir  = Join-Path $PcDir 'dist'

function Step($message) {
    Write-Host ''
    Write-Host "=== $message ===" -ForegroundColor Cyan
}

# --- Python environment -----------------------------------------------------

Step 'Python environment'
$python = (Get-Command python -ErrorAction SilentlyContinue)
if (-not $python) { $python = Get-Command py -ErrorAction SilentlyContinue }
if (-not $python) {
    throw 'Python 3.10 or newer is required and was not found on PATH.'
}

if (-not (Test-Path (Join-Path $VenvDir 'Scripts\python.exe'))) {
    & $python.Source -m venv $VenvDir
}
$VenvPython = Join-Path $VenvDir 'Scripts\python.exe'

& $VenvPython -m pip install --upgrade pip --quiet
& $VenvPython -m pip install --quiet `
    PyQt6 Pillow rawpy pyinstaller
if ($LASTEXITCODE -ne 0) { throw 'installing the Python dependencies failed' }

# --- helper binaries --------------------------------------------------------

Step 'Helper binaries (ExifTool, FFmpeg, DNGLab)'
& $VenvPython (Join-Path $ToolsDir 'fetch_binaries.py') --platform windows
if ($LASTEXITCODE -ne 0) { throw 'fetching the helper binaries failed' }

# --- tests ------------------------------------------------------------------

if (-not $SkipTests) {
    Step 'Tests'
    Push-Location $PcDir
    try {
        & $VenvPython -m unittest discover -s tests -t tests
        if ($LASTEXITCODE -ne 0) { throw 'the test suite failed' }
    } finally {
        Pop-Location
    }
} else {
    Write-Host 'skipping tests at your request' -ForegroundColor Yellow
}

# --- package ----------------------------------------------------------------

Push-Location $PcDir
try {
    if ($Target -in @('installer', 'both')) {
        Step 'PyInstaller (folder build, for the installer)'
        $env:DESQUEEZE_ONEFILE = '0'
        & $VenvPython -m PyInstaller --noconfirm --clean `
            --distpath $DistDir --workpath (Join-Path $PcDir 'build') `
            (Join-Path $ToolsDir 'desqueeze.spec')
        if ($LASTEXITCODE -ne 0) { throw 'PyInstaller (folder) failed' }
    }

    if ($Target -in @('portable', 'both')) {
        Step 'PyInstaller (single-file portable build)'
        $env:DESQUEEZE_ONEFILE = '1'
        & $VenvPython -m PyInstaller --noconfirm --clean `
            --distpath $DistDir --workpath (Join-Path $PcDir 'build-onefile') `
            (Join-Path $ToolsDir 'desqueeze.spec')
        if ($LASTEXITCODE -ne 0) { throw 'PyInstaller (portable) failed' }
    }
} finally {
    Remove-Item Env:\DESQUEEZE_ONEFILE -ErrorAction SilentlyContinue
    Pop-Location
}

# --- installer --------------------------------------------------------------

if ($Target -in @('installer', 'both')) {
    Step 'Inno Setup installer'
    $iscc = Get-Command iscc -ErrorAction SilentlyContinue
    if (-not $iscc) {
        foreach ($candidate in @(
            "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
            "$env:ProgramFiles\Inno Setup 6\ISCC.exe")) {
            if (Test-Path $candidate) { $iscc = Get-Item $candidate; break }
        }
    }
    if ($iscc) {
        & $iscc.FullName (Join-Path $ToolsDir 'desqueeze.iss')
        if ($LASTEXITCODE -ne 0) { throw 'Inno Setup failed' }
    } else {
        Write-Host ('Inno Setup was not found, so the installer was skipped. ' +
                    'Install it from https://jrsoftware.org/isdl.php, or run ' +
                    'with -Target portable.') -ForegroundColor Yellow
    }
}

# --- verify -----------------------------------------------------------------

Step 'Verify'
& $VenvPython (Join-Path $ToolsDir 'verify_build.py') $DistDir

Step 'Done'
Get-ChildItem $DistDir -ErrorAction SilentlyContinue |
    Select-Object Name, @{Name = 'Size'; Expression = {
        if ($_.PSIsContainer) { 'folder' }
        else { '{0:N1} MB' -f ($_.Length / 1MB) } }} |
    Format-Table -AutoSize
