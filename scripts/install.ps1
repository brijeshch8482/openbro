# OpenBro Installer for Windows
# Run: irm https://raw.githubusercontent.com/brijeshch8482/openbro/main/scripts/install.ps1 | iex

$ErrorActionPreference = "Stop"

Write-Host ""
Write-Host "   ____                   ____" -ForegroundColor Cyan
Write-Host "  / __ \____  ___  ____  / __ )_________" -ForegroundColor Cyan
Write-Host " / / / / __ \/ _ \/ __ \/ __  / ___/ __ \" -ForegroundColor Cyan
Write-Host "/ /_/ / /_/ /  __/ / / / /_/ / /  / /_/ /" -ForegroundColor Cyan
Write-Host "\____/ .___/\___/_/ /_/_____/_/   \____/" -ForegroundColor Cyan
Write-Host "    /_/" -ForegroundColor Cyan
Write-Host ""
Write-Host "OpenBro Installer - Tera Apna AI Bro" -ForegroundColor Yellow
Write-Host "======================================" -ForegroundColor Yellow
Write-Host ""

# Check Python
Write-Host "[1/4] Checking Python..." -ForegroundColor Green
$python = $null
foreach ($cmd in @("python", "python3", "py")) {
    try {
        $version = & $cmd --version 2>&1
        if ($version -match "Python 3\.(\d+)") {
            $minor = [int]$Matches[1]
            if ($minor -ge 10) {
                $python = $cmd
                Write-Host "  Found: $version" -ForegroundColor DarkGray
                break
            }
        }
    } catch {}
}

if (-not $python) {
    Write-Host "  Python 3.10+ not found!" -ForegroundColor Red
    Write-Host "  Install Python from https://python.org/downloads" -ForegroundColor Yellow
    Write-Host "  Make sure to check 'Add Python to PATH' during install!" -ForegroundColor Yellow
    exit 1
}

# Install OpenBro
Write-Host "[2/4] Installing OpenBro..." -ForegroundColor Green
& $python -m pip install --upgrade pip 2>&1 | Out-Null
& $python -m pip install openbro 2>&1

if ($LASTEXITCODE -ne 0) {
    Write-Host "  pip install failed. Trying from GitHub..." -ForegroundColor Yellow
    & $python -m pip install git+https://github.com/brijeshch8482/openbro.git 2>&1
}

if ($LASTEXITCODE -ne 0) {
    Write-Host "  Installation failed!" -ForegroundColor Red
    exit 1
}

Write-Host "  OpenBro installed successfully!" -ForegroundColor Green

# Check Ollama (optional)
Write-Host "[3/4] Checking Ollama (optional, for offline mode)..." -ForegroundColor Green
$ollamaInstalled = $false
try {
    $ollamaVersion = & ollama --version 2>&1
    Write-Host "  Found: $ollamaVersion" -ForegroundColor DarkGray
    $ollamaInstalled = $true
} catch {
    Write-Host "  Ollama not found (optional - needed only for offline mode)" -ForegroundColor DarkGray
    Write-Host "  Install later from: https://ollama.ai" -ForegroundColor DarkGray
}

# Verify installation
Write-Host "[4/4] Verifying installation..." -ForegroundColor Green
try {
    & $python -c "import openbro; print(f'  OpenBro v{openbro.__version__} ready!')" 2>&1
} catch {
    Write-Host "  Warning: Could not verify installation" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "============================================" -ForegroundColor Green
Write-Host "  Installation complete!" -ForegroundColor Green
Write-Host "============================================" -ForegroundColor Green
Write-Host ""
Write-Host "  Start OpenBro:  openbro" -ForegroundColor Cyan
Write-Host "  Re-run setup:   openbro --setup" -ForegroundColor Cyan
Write-Host "  Get help:       openbro --help" -ForegroundColor Cyan
Write-Host ""

if (-not $ollamaInstalled) {
    Write-Host "  For offline mode, install Ollama: https://ollama.ai" -ForegroundColor Yellow
    Write-Host "  Then run: ollama pull qwen2.5-coder:7b" -ForegroundColor Yellow
    Write-Host ""
}
