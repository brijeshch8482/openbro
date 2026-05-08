# OpenBro Installer for Windows
# Zero-friction one-line install:
#   iwr -useb https://github.com/brijeshch8482/openbro/raw/main/scripts/install.ps1 | iex

[CmdletBinding()]
param(
    [string]$Extras = "all,voice",
    [string]$Branch = "main",
    [switch]$NoSetup,
    [switch]$NoLaunch
)

$ErrorActionPreference = "Stop"
$REPO = "brijeshch8482/openbro"

# Force UTF-8 output so box-drawing chars and check marks render properly
# (Windows PowerShell defaults to OEM/Win-1252 which mangles them to '?')
try {
    [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
    $OutputEncoding = [System.Text.UTF8Encoding]::new()
} catch {}

function Write-Step($num, $total, $msg) {
    Write-Host ""
    Write-Host "[$num/$total] $msg" -ForegroundColor Cyan
}

function Write-OK($msg)   { Write-Host "  [OK] $msg" -ForegroundColor Green }
function Write-Info($msg) { Write-Host "  $msg" -ForegroundColor DarkGray }
function Write-Warn($msg) { Write-Host "  [!] $msg" -ForegroundColor Yellow }
function Write-Err($msg)  { Write-Host "  [X] $msg" -ForegroundColor Red }

Write-Host ""
Write-Host "  +-------------------------------------------+" -ForegroundColor Cyan
Write-Host "  |          OpenBro Installer v1.0          |" -ForegroundColor Cyan
Write-Host "  |      Tera Apna AI Bro - Open Source      |" -ForegroundColor Cyan
Write-Host "  +-------------------------------------------+" -ForegroundColor Cyan
Write-Host ""

# ─── Step 1/5: Python (robust detect + install) ──────────────
Write-Step 1 5 "Checking Python..."

# Refresh session PATH from registry (covers earlier installs in same session)
function Refresh-Path {
    $env:Path = [Environment]::GetEnvironmentVariable("Path","Machine") + ";" +
                [Environment]::GetEnvironmentVariable("Path","User")
}

# Detects Microsoft Store Python alias (a stub that opens Store, not real Python)
function Is-StorePythonStub($exePath) {
    if (-not $exePath) { return $false }
    return $exePath -like "*\WindowsApps\*"
}

# Try to launch a python exe and parse "Python X.Y.Z"; returns @{cmd, version, minor, path} or $null
function Probe-Python($exe) {
    try {
        $resolved = (Get-Command $exe -ErrorAction Stop).Source
        if (Is-StorePythonStub $resolved) { return $null }
        # Use --version which writes to stdout in 3.4+; redirect both anyway
        $verRaw = & $exe --version 2>&1 | Out-String
        if ($verRaw -match "Python\s+3\.(\d+)\.(\d+)?") {
            $minor = [int]$Matches[1]
            return @{
                cmd     = $exe
                version = $verRaw.Trim()
                minor   = $minor
                path    = $resolved
            }
        }
    } catch {}
    return $null
}

# Hunt across PATH, py launcher targets, and known install dirs.
function Find-Python {
    $candidates = @()

    foreach ($cmd in @("python", "python3")) {
        $info = Probe-Python $cmd
        if ($info) { $candidates += $info }
    }

    # py launcher: list every installed Python
    try {
        $null = Get-Command py -ErrorAction Stop
        $list = & py --list-paths 2>&1
        foreach ($line in $list) {
            if ($line -match "(-V:)?(\d+\.\d+)\s+\*?\s*(.+)$") {
                $exe = $Matches[3].Trim()
                if ($exe -and (Test-Path $exe)) {
                    $info = Probe-Python $exe
                    if ($info) { $candidates += $info }
                }
            }
        }
    } catch {}

    # Common install dirs (per-user + machine-wide)
    $globs = @(
        "$env:LocalAppData\Programs\Python\Python3*\python.exe",
        "$env:ProgramFiles\Python3*\python.exe",
        "${env:ProgramFiles(x86)}\Python3*\python.exe",
        "C:\Python3*\python.exe"
    )
    foreach ($g in $globs) {
        Get-ChildItem -Path $g -ErrorAction SilentlyContinue | ForEach-Object {
            $info = Probe-Python $_.FullName
            if ($info) { $candidates += $info }
        }
    }

    # Pick the highest 3.10+
    $valid = $candidates | Where-Object { $_.minor -ge 10 } | Sort-Object minor -Descending
    if ($valid) { return $valid[0] }
    # Or report best-but-too-old so we can warn user
    if ($candidates) {
        return ($candidates | Sort-Object minor -Descending)[0] | Add-Member -NotePropertyName tooOld -NotePropertyValue $true -PassThru
    }
    return $null
}

# Install Python 3.12 — try multiple strategies
function Install-Python {
    # Strategy 1: winget (modern Win10/Win11)
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        Write-Info "Using winget (Python.Python.3.12, user scope)..."
        $wgArgs = @(
            "install", "--id", "Python.Python.3.12",
            "--source", "winget",
            "--silent", "--accept-source-agreements", "--accept-package-agreements",
            "--scope", "user"
        )
        $proc = Start-Process winget -ArgumentList $wgArgs -Wait -PassThru -NoNewWindow
        # winget exit 0 = installed; -1978335189 = already installed (also fine)
        if ($proc.ExitCode -eq 0 -or $proc.ExitCode -eq -1978335189) {
            return $true
        }
        Write-Info "winget exit=$($proc.ExitCode), trying direct download..."
    } else {
        Write-Info "winget not available, using direct download..."
    }

    # Strategy 2: direct download from python.org
    # We pin a known stable version and verify the URL exists before downloading.
    $candidates = @("3.12.8", "3.12.7", "3.12.6", "3.13.1", "3.13.0")
    foreach ($ver in $candidates) {
        $url = "https://www.python.org/ftp/python/$ver/python-$ver-amd64.exe"
        try {
            $head = Invoke-WebRequest -Method Head -Uri $url -UseBasicParsing -ErrorAction Stop -TimeoutSec 10
            if ($head.StatusCode -eq 200) {
                $tmp = "$env:TEMP\python-$ver-installer.exe"
                Write-Info "Downloading Python $ver (~25 MB)..."
                Invoke-WebRequest -Uri $url -OutFile $tmp -UseBasicParsing -TimeoutSec 120
                Write-Info "Running installer (silent, user scope, adds to PATH)..."
                $instArgs = @(
                    "/quiet",
                    "InstallAllUsers=0",
                    "PrependPath=1",
                    "Include_test=0",
                    "Include_doc=0",
                    "Include_launcher=1",
                    "AssociateFiles=0",
                    "Shortcuts=0"
                )
                $p = Start-Process $tmp -ArgumentList $instArgs -Wait -PassThru -NoNewWindow
                Remove-Item $tmp -Force -ErrorAction SilentlyContinue
                if ($p.ExitCode -eq 0) { return $true }
                Write-Info "Installer exit=$($p.ExitCode), trying next version..."
            }
        } catch {
            continue
        }
    }
    return $false
}

# ── Detect what we have ──
$pyInfo = Find-Python

if ($pyInfo -and -not $pyInfo.tooOld) {
    $python = $pyInfo.cmd
    Write-OK "Found $($pyInfo.version) at $($pyInfo.path)"
} elseif ($pyInfo -and $pyInfo.tooOld) {
    Write-Warn "Found old $($pyInfo.version) — needs 3.10+. Installing newer alongside..."
    if (-not (Install-Python)) {
        Write-Err "Python install failed across all strategies."
        Write-Host ""
        Write-Host "  Manual fix: download Python 3.12 from https://python.org/downloads/" -ForegroundColor Yellow
        Write-Host "  IMPORTANT: tick 'Add Python to PATH' during install!" -ForegroundColor Yellow
        exit 1
    }
    Refresh-Path
    Start-Sleep -Seconds 2  # brief settle for installer registry writes
    $pyInfo = Find-Python
    if ($pyInfo -and -not $pyInfo.tooOld) {
        $python = $pyInfo.cmd
        Write-OK "Installed $($pyInfo.version) at $($pyInfo.path)"
    } else {
        Write-Err "Install completed but Python still not detected."
        Write-Host "  Open a NEW PowerShell window and re-run the installer." -ForegroundColor Yellow
        Write-Host "  Or run: py -3.12 -m pip install 'openbro[all,voice]'" -ForegroundColor Cyan
        exit 1
    }
} else {
    Write-Warn "No Python found — installing Python 3.12 (~30 sec)..."
    if (-not (Install-Python)) {
        Write-Err "Python install failed across all strategies."
        Write-Host ""
        Write-Host "  Possible causes:" -ForegroundColor Yellow
        Write-Host "  - No internet (winget + python.org both blocked)" -ForegroundColor DarkGray
        Write-Host "  - Antivirus / firewall blocking the installer" -ForegroundColor DarkGray
        Write-Host "  - Corporate policy disallows package install" -ForegroundColor DarkGray
        Write-Host ""
        Write-Host "  Manual fix:" -ForegroundColor Yellow
        Write-Host "    winget install Python.Python.3.12 --scope user" -ForegroundColor Cyan
        Write-Host "    OR download from https://python.org/downloads/" -ForegroundColor Cyan
        Write-Host "    (tick 'Add Python to PATH' during install)" -ForegroundColor DarkGray
        exit 1
    }
    Refresh-Path
    Start-Sleep -Seconds 2
    $pyInfo = Find-Python
    if ($pyInfo -and -not $pyInfo.tooOld) {
        $python = $pyInfo.cmd
        Write-OK "Installed $($pyInfo.version) at $($pyInfo.path)"
    } else {
        Write-Err "Install ran but Python still not detected on PATH."
        Write-Host ""
        Write-Host "  Open a NEW PowerShell window (PATH refresh) and re-run:" -ForegroundColor Yellow
        Write-Host "    iwr -useb https://github.com/$REPO/raw/$Branch/scripts/install.ps1 | iex" -ForegroundColor Cyan
        exit 1
    }
}

# Final sanity: actually run python and confirm it works
try {
    $sanityOut = & $python -c "import sys; print(sys.executable)" 2>&1
    if ($LASTEXITCODE -ne 0) { throw "exit ${LASTEXITCODE} - ${sanityOut}" }
    Write-Info "Python exe: $sanityOut"
} catch {
    Write-Err "Python found but failed to run: $_"
    exit 1
}

# ─── Step 2/5: pip + OpenBro ─────────────────────────────────
Write-Step 2 5 "Installing OpenBro [$Extras] (this may take 1-2 minutes)..."

# pip writes warnings to stderr (e.g. "Scripts not on PATH"). Under
# $ErrorActionPreference=Stop, any stderr line from a native command becomes
# a terminating error. We swallow stderr explicitly to avoid that.
function Invoke-Pip {
    param([string[]]$PipArgs)
    & $python -m pip @PipArgs --no-warn-script-location 2>$null
    return $LASTEXITCODE
}

$pipExit = Invoke-Pip @("install", "--upgrade", "pip", "--quiet")
if ($pipExit -ne 0) {
    Write-Info "pip self-upgrade returned $pipExit (continuing)"
}

# Try PyPI first; fall back to GitHub. Try with chosen extras, then trim
# voice if Python is too new for some wheels.
$pkgSpec = "openbro[$Extras]"
$installExit = Invoke-Pip @("install", "--upgrade", $pkgSpec, "--quiet")

if ($installExit -ne 0 -and $Extras -match "voice") {
    # Some voice deps (faster-whisper, sounddevice) lack wheels for very new
    # Python versions. Retry without voice so user still gets a working bro.
    Write-Warn "Install with voice deps failed. Retrying without voice..."
    $reduced = ($Extras -split "," | Where-Object { $_ -ne "voice" }) -join ","
    if (-not $reduced) { $reduced = "all" }
    $pkgSpec = "openbro[$reduced]"
    $installExit = Invoke-Pip @("install", "--upgrade", $pkgSpec, "--quiet")
}

if ($installExit -ne 0) {
    Write-Info "PyPI install failed (exit $installExit), trying GitHub @$Branch..."
    $installExit = Invoke-Pip @(
        "install", "--upgrade",
        "git+https://github.com/$REPO.git@$Branch#egg=openbro[$Extras]"
    )
}

if ($installExit -ne 0) {
    Write-Err "Installation failed. Try manually:"
    Write-Host "    $python -m pip install '$pkgSpec'" -ForegroundColor Yellow
    exit 1
}
Write-OK "OpenBro installed"

# Add Python's user Scripts dir to PATH (current session + persistent)
# so `openbro` command works without restarting shell.
try {
    $userScripts = & $python -c "import sysconfig; print(sysconfig.get_path('scripts', 'nt_user'))" 2>$null
    if ($userScripts -and (Test-Path $userScripts)) {
        # Current session
        if ($env:Path -notlike "*$userScripts*") {
            $env:Path = "$userScripts;$env:Path"
        }
        # Persist for future shells (user-scope PATH)
        $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
        if (-not $userPath) { $userPath = "" }
        if ($userPath -notlike "*$userScripts*") {
            $newUserPath = if ($userPath) { "$userScripts;$userPath" } else { $userScripts }
            [Environment]::SetEnvironmentVariable("Path", $newUserPath, "User")
            Write-Info "Added $userScripts to user PATH"
        }
    }
} catch {}

# ─── Step 3/5: Verify ────────────────────────────────────────
Write-Step 3 5 "Verifying installation..."
$verifyOut = & $python -c "import openbro; print(openbro.__version__)" 2>&1
if ($LASTEXITCODE -eq 0) {
    Write-OK "OpenBro v$verifyOut ready"
} else {
    Write-Err "Verification failed: $verifyOut"
    exit 1
}

# ─── Step 4/5: PATH check ────────────────────────────────────
Write-Step 4 5 "Checking openbro command..."
try {
    $null = Get-Command openbro -ErrorAction Stop
    Write-OK "'openbro' command available"
} catch {
    Write-Warn "'openbro' not on PATH yet — using 'python -m openbro' fallback"
}

# ─── Step 5/5: Configure LLM (auto-runs wizard) ──────────────
Write-Step 5 5 "Setting up your LLM..."
Write-Host "  Pick offline (free, Ollama) or online (Claude / GPT / Groq)." -ForegroundColor DarkGray
Write-Host "  Offline: model auto-downloads. Online: just paste your API key." -ForegroundColor DarkGray
Write-Host ""

if (-not $NoSetup) {
    $resp = Read-Host "  Configure now? [Y/n]"
    if ($resp -eq "" -or $resp -match "^[yY]") {
        Write-Host ""
        # --setup runs the wizard which handles: provider pick, Ollama install + model
        # download, cloud API keys, storage drive, personality, optional Telegram setup.
        # Then exits without launching the chat REPL.
        try {
            & openbro --setup
        } catch {
            & $python -m openbro --setup
        }
    } else {
        Write-Info "Skipped. Run 'openbro --setup' anytime to configure."
    }
} else {
    Write-Info "Skipped (--NoSetup)"
}

Write-Host ""
Write-Host "  ╔═══════════════════════════════════════════╗" -ForegroundColor Green
Write-Host "  ║       ✓ OpenBro is ready!                ║" -ForegroundColor Green
Write-Host "  ╚═══════════════════════════════════════════╝" -ForegroundColor Green
Write-Host ""
Write-Host "  Quick commands:" -ForegroundColor White
Write-Host "    openbro              " -NoNewline -ForegroundColor Cyan
Write-Host "Start chatting" -ForegroundColor DarkGray
Write-Host "    openbro --voice      " -NoNewline -ForegroundColor Cyan
Write-Host "Voice mode (mic + TTS)" -ForegroundColor DarkGray
Write-Host "    openbro --telegram   " -NoNewline -ForegroundColor Cyan
Write-Host "Run as Telegram bot" -ForegroundColor DarkGray
Write-Host "    openbro --setup      " -NoNewline -ForegroundColor Cyan
Write-Host "Re-run setup wizard" -ForegroundColor DarkGray
Write-Host "    openbro --help       " -NoNewline -ForegroundColor Cyan
Write-Host "All flags" -ForegroundColor DarkGray
Write-Host ""

if (-not $NoLaunch) {
    $launch = Read-Host "  Start chatting now? [Y/n]"
    if ($launch -eq "" -or $launch -match "^[yY]") {
        Write-Host ""
        & openbro
    } else {
        Write-Host "  Run 'openbro' anytime to start." -ForegroundColor DarkGray
        Write-Host ""
    }
}
