# OpenBro Installer for Windows
# Zero-friction one-line install:
#   iwr -useb https://github.com/brijeshch8482/openbro/raw/main/scripts/install.ps1 | iex

[CmdletBinding()]
param(
    [string]$Extras = "all,voice",
    [string]$Branch = "main",
    [switch]$NoOllama,
    [switch]$NoLaunch
)

$ErrorActionPreference = "Stop"
$REPO = "brijeshch8482/openbro"

function Write-Step($num, $total, $msg) {
    Write-Host ""
    Write-Host "[$num/$total] $msg" -ForegroundColor Cyan
}

function Write-OK($msg)   { Write-Host "  ✓ $msg" -ForegroundColor Green }
function Write-Info($msg) { Write-Host "  $msg" -ForegroundColor DarkGray }
function Write-Warn($msg) { Write-Host "  ! $msg" -ForegroundColor Yellow }
function Write-Err($msg)  { Write-Host "  ✗ $msg" -ForegroundColor Red }

Write-Host ""
Write-Host "  ╔═══════════════════════════════════════════╗" -ForegroundColor Cyan
Write-Host "  ║          OpenBro Installer v1.0          ║" -ForegroundColor Cyan
Write-Host "  ║      Tera Apna AI Bro - Open Source      ║" -ForegroundColor Cyan
Write-Host "  ╚═══════════════════════════════════════════╝" -ForegroundColor Cyan
Write-Host ""

# ─── Step 1/5: Python ────────────────────────────────────────
Write-Step 1 5 "Checking Python..."
$python = $null
foreach ($cmd in @("python", "python3", "py")) {
    try {
        $version = & $cmd --version 2>&1
        if ($version -match "Python 3\.(\d+)") {
            $minor = [int]$Matches[1]
            if ($minor -ge 10) {
                $python = $cmd
                Write-OK "Found $version"
                break
            } else {
                Write-Info "$version (too old, need 3.10+)"
            }
        }
    } catch {}
}

if (-not $python) {
    Write-Err "Python 3.10+ not found"
    Write-Host ""
    Write-Host "  Install Python from: https://python.org/downloads/" -ForegroundColor Yellow
    Write-Host "  IMPORTANT: Tick 'Add Python to PATH' during install!" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "  Or via winget:" -ForegroundColor DarkGray
    Write-Host "    winget install Python.Python.3.12" -ForegroundColor Gray
    exit 1
}

# ─── Step 2/5: pip + OpenBro ─────────────────────────────────
Write-Step 2 5 "Installing OpenBro [$Extras] (this may take 1-2 minutes)..."
& $python -m pip install --upgrade pip --quiet 2>&1 | Out-Null

# Try PyPI first; fall back to GitHub
$pkgSpec = "openbro[$Extras]"
& $python -m pip install --upgrade $pkgSpec --quiet 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Info "PyPI install failed, installing from GitHub ($Branch)..."
    & $python -m pip install --upgrade "git+https://github.com/$REPO.git@$Branch#egg=openbro[$Extras]" 2>&1
}

if ($LASTEXITCODE -ne 0) {
    Write-Err "Installation failed. Try manually:"
    Write-Host "    $python -m pip install '$pkgSpec'" -ForegroundColor Yellow
    exit 1
}
Write-OK "OpenBro installed"

# ─── Step 3/5: Verify ────────────────────────────────────────
Write-Step 3 5 "Verifying installation..."
$verifyOut = & $python -c "import openbro; print(openbro.__version__)" 2>&1
if ($LASTEXITCODE -eq 0) {
    Write-OK "OpenBro v$verifyOut ready"
} else {
    Write-Err "Verification failed: $verifyOut"
    exit 1
}

# ─── Step 4/5: Ollama (optional) ─────────────────────────────
Write-Step 4 5 "Checking Ollama (offline mode)..."
$ollamaInstalled = $false
try {
    $null = Get-Command ollama -ErrorAction Stop
    $ollamaVersion = (& ollama --version 2>&1) -join " "
    Write-OK "Ollama found: $ollamaVersion"
    $ollamaInstalled = $true
} catch {
    if ($NoOllama) {
        Write-Info "Skipped (--NoOllama)"
    } else {
        Write-Warn "Ollama not installed (needed for free offline LLM)"
        $resp = Read-Host "  Install Ollama now? [Y/n]"
        if ($resp -eq "" -or $resp -match "^[yY]") {
            try {
                Write-Info "Downloading Ollama installer..."
                $tmp = "$env:TEMP\OllamaSetup.exe"
                Invoke-WebRequest "https://ollama.com/download/OllamaSetup.exe" -OutFile $tmp -UseBasicParsing
                Write-Info "Running Ollama installer (silent)..."
                Start-Process -FilePath $tmp -ArgumentList "/S" -Wait
                Remove-Item $tmp -Force -ErrorAction SilentlyContinue
                Write-OK "Ollama installed"
            } catch {
                Write-Warn "Auto-install failed. Install manually: https://ollama.com"
            }
        } else {
            Write-Info "Skipped. Install later: https://ollama.com"
        }
    }
}

# ─── Step 5/5: PATH check + first run ────────────────────────
Write-Step 5 5 "Checking openbro on PATH..."
try {
    $null = Get-Command openbro -ErrorAction Stop
    Write-OK "'openbro' command available"
} catch {
    Write-Warn "'openbro' not on PATH yet — open a new PowerShell window or use:"
    Write-Host "    $python -m openbro" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "  ╔═══════════════════════════════════════════╗" -ForegroundColor Green
Write-Host "  ║       ✓ Installation complete!           ║" -ForegroundColor Green
Write-Host "  ╚═══════════════════════════════════════════╝" -ForegroundColor Green
Write-Host ""
Write-Host "  Quick commands:" -ForegroundColor White
Write-Host "    openbro              " -NoNewline -ForegroundColor Cyan
Write-Host "Start chatting (first run launches setup)" -ForegroundColor DarkGray
Write-Host "    openbro --voice      " -NoNewline -ForegroundColor Cyan
Write-Host "Voice mode (mic + TTS)" -ForegroundColor DarkGray
Write-Host "    openbro --telegram   " -NoNewline -ForegroundColor Cyan
Write-Host "Run as Telegram bot" -ForegroundColor DarkGray
Write-Host "    openbro --setup      " -NoNewline -ForegroundColor Cyan
Write-Host "Re-run wizard" -ForegroundColor DarkGray
Write-Host "    openbro --help       " -NoNewline -ForegroundColor Cyan
Write-Host "All flags" -ForegroundColor DarkGray
Write-Host ""

if (-not $NoLaunch) {
    $launch = Read-Host "  Launch OpenBro now? [Y/n]"
    if ($launch -eq "" -or $launch -match "^[yY]") {
        Write-Host ""
        & openbro
    } else {
        Write-Host "  Run 'openbro' anytime to start." -ForegroundColor DarkGray
        Write-Host ""
    }
}
