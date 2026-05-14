# OpenBro Uninstaller for Windows — safe-by-default cleanup
#
# Lessons learned: an earlier version of this script swept C:/D:/E: for any
# folder whose name started with 'OpenBro' and prompted-with-default-Yes.
# That regex matched both the user's data folder (D:\OpenBro-teting) AND the
# developer's source-code clone (D:\OpenBro). User hit Enter expecting only
# the data folder to go — and their `git clone` was wiped instead. Painful.
#
# This rewrite adds four safety layers:
#   1. WHITELIST: only delete paths that are explicitly listed in config.yaml
#      (storage.base_dir / storage.models_dir). No regex sweep over drives.
#   2. SKIP DEV FOLDERS: if a target contains `.git/`, `pyproject.toml`, or
#      `setup.py`, it's treated as a source-code clone and SKIPPED without
#      prompting. You can't accidentally nuke a checkout.
#   3. DEFAULT NO: every destructive prompt defaults to N. User must type Y
#      explicitly. Blank Enter = keep.
#   4. DRY RUN BY DEFAULT: lists exactly what WOULD be deleted, then exits.
#      Re-run with -Commit to actually delete. Use -Force to bypass prompts
#      after you've reviewed the dry-run output.
#
# Usage:
#   iwr -useb .../uninstall.ps1 | iex           # dry-run (default)
#   .\uninstall.ps1 -Commit                     # actually delete (with prompts)
#   .\uninstall.ps1 -Commit -Force              # delete without prompts
#   .\uninstall.ps1 -Commit -KeepData           # keep user storage drive
#   .\uninstall.ps1 -Commit -KeepWhisper        # keep HF cache

[CmdletBinding()]
param(
    [switch]$Commit,
    [switch]$Force,
    [switch]$KeepData,
    [switch]$KeepWhisper
)

$ErrorActionPreference = "Continue"
$DryRun = -not $Commit

function Write-Step($num, $total, $msg) {
    Write-Host ""
    Write-Host "[$num/$total] $msg" -ForegroundColor Cyan
}
function Write-OK($msg)   { Write-Host "  [OK] $msg" -ForegroundColor Green }
function Write-Info($msg) { Write-Host "  $msg" -ForegroundColor DarkGray }
function Write-Warn($msg) { Write-Host "  ! $msg" -ForegroundColor Yellow }
function Write-Skip($msg) { Write-Host "  [skip] $msg" -ForegroundColor Yellow }

Write-Host ""
Write-Host "  +-------------------------------------------+" -ForegroundColor Yellow
if ($DryRun) {
    Write-Host "  |   OpenBro Uninstaller (DRY RUN mode)      |" -ForegroundColor Yellow
} else {
    Write-Host "  |   OpenBro Uninstaller (WILL DELETE)       |" -ForegroundColor Red
}
Write-Host "  +-------------------------------------------+" -ForegroundColor Yellow
Write-Host ""
if ($DryRun) {
    Write-Host "  No files will be deleted. This is a preview." -ForegroundColor Green
    Write-Host "  To actually delete, re-run with -Commit." -ForegroundColor DarkGray
    Write-Host ""
}

# A target is "protected" if it looks like a source-code checkout. We refuse
# to delete these no matter what — saves users from accidentally nuking a
# git clone that happens to be named `OpenBro`.
function Test-IsDevFolder($path) {
    if (-not (Test-Path $path)) { return $false }
    foreach ($marker in @(".git", "pyproject.toml", "setup.py", ".github", "CHANGELOG.md")) {
        if (Test-Path (Join-Path $path $marker)) {
            return $true
        }
    }
    return $false
}

# ─── Step 0: stop running openbro processes ────────────────────────────
Write-Step 0 5 "Checking for running openbro processes..."
$running = @()
try {
    $procs = Get-CimInstance Win32_Process -Filter "Name LIKE 'python%'" -ErrorAction SilentlyContinue
    foreach ($p in $procs) {
        if ($p.CommandLine -and ($p.CommandLine -match "openbro|OpenBro")) {
            $running += $p
        }
    }
    Get-Process -Name openbro -ErrorAction SilentlyContinue | ForEach-Object { $running += $_ }
} catch {}
if ($running.Count -gt 0) {
    Write-Info "$($running.Count) openbro process(es) running."
    if (-not $DryRun) {
        foreach ($p in $running) {
            try { Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop } catch {}
            try { Stop-Process -Id $p.Id -Force -ErrorAction Stop } catch {}
        }
        Start-Sleep -Seconds 1
        Write-OK "Stopped"
    } else {
        Write-Host "  WOULD STOP: $($running.Count) process(es)" -ForegroundColor Yellow
    }
} else {
    Write-Info "No openbro processes running"
}

# ─── Discover Pythons (no regex sweep — use `py -X.Y` launcher only) ──
$pythons = @()
foreach ($ver in @("3.10","3.11","3.12","3.13","3.14")) {
    try {
        $p = & py "-$ver" -c "import sys; print(sys.executable)" 2>$null
        if ($LASTEXITCODE -eq 0 -and $p) {
            $pythons += @{ Version=$ver; Exe=$p.Trim() }
        }
    } catch {}
}

# Read storage paths from config BEFORE pip uninstall (config disappears with the pkg)
$customBase = $null
$customModels = $null
foreach ($py in $pythons) {
    try {
        $cfgJson = & $py.Exe -c @"
import json
try:
    from openbro.utils.config import load_config
    c = load_config()
    s = c.get('storage') or {}
    print(json.dumps({'base': s.get('base_dir'), 'models': s.get('models_dir')}))
except Exception:
    print('{}')
"@ 2>$null
        if ($cfgJson -and $LASTEXITCODE -eq 0) {
            $cfg = $cfgJson | ConvertFrom-Json
            if ($cfg.base) { $customBase = $cfg.base }
            if ($cfg.models) { $customModels = $cfg.models }
            if ($customBase) { break }
        }
    } catch {}
}

# ─── Step 1/5: pip uninstall from each Python that has openbro ─────────
Write-Step 1 5 "Pip-uninstall openbro from every Python that has it..."
foreach ($py in $pythons) {
    try {
        $probe = & $py.Exe -c "import openbro; print('yes')" 2>$null
    } catch {
        $probe = $null
    }
    if ($probe -notmatch "yes") {
        Write-Info "Python $($py.Version): openbro not installed"
        continue
    }
    if ($DryRun) {
        Write-Host "  WOULD RUN: $($py.Exe) -m pip uninstall openbro -y" -ForegroundColor Yellow
    } else {
        $out = & $py.Exe -m pip uninstall openbro -y 2>&1
        if ($out -match "Successfully uninstalled") {
            Write-OK "Python $($py.Version): removed"
        } else {
            Write-Warn "Python $($py.Version): uninstall reported issues"
        }
    }
}

# ─── Step 2/5: orphan .exe launchers (never delete dev folders) ───────
Write-Step 2 5 "Looking for orphan openbro.exe launchers..."
$exeLocations = @(
    "C:\Python310\Scripts\openbro.exe",
    "C:\Python311\Scripts\openbro.exe",
    "C:\Python312\Scripts\openbro.exe",
    "C:\Python313\Scripts\openbro.exe",
    "C:\Python314\Scripts\openbro.exe",
    "$env:USERPROFILE\AppData\Local\Programs\Python\Python310\Scripts\openbro.exe",
    "$env:USERPROFILE\AppData\Local\Programs\Python\Python311\Scripts\openbro.exe",
    "$env:USERPROFILE\AppData\Local\Programs\Python\Python312\Scripts\openbro.exe",
    "$env:USERPROFILE\AppData\Local\Programs\Python\Python313\Scripts\openbro.exe",
    "$env:USERPROFILE\AppData\Local\Programs\Python\Python314\Scripts\openbro.exe",
    "$env:APPDATA\Python\Python310\Scripts\openbro.exe",
    "$env:APPDATA\Python\Python311\Scripts\openbro.exe",
    "$env:APPDATA\Python\Python312\Scripts\openbro.exe",
    "$env:APPDATA\Python\Python313\Scripts\openbro.exe",
    "$env:APPDATA\Python\Python314\Scripts\openbro.exe"
)
$adminNeeded = @()
foreach ($exe in $exeLocations) {
    if (-not (Test-Path $exe)) { continue }
    if ($DryRun) {
        Write-Host "  WOULD DELETE: $exe" -ForegroundColor Yellow
        continue
    }
    try {
        Remove-Item $exe -Force -ErrorAction Stop
        Write-OK "Removed $exe"
    } catch {
        $adminNeeded += $exe
    }
}
if ($adminNeeded.Count -gt 0) {
    Write-Warn "These need admin to delete:"
    foreach ($p in $adminNeeded) {
        Write-Host "    Remove-Item '$p' -Force" -ForegroundColor White
    }
}

# ─── Step 3/5: config dir (~/.openbro) ────────────────────────────────
Write-Step 3 5 "Config dir..."
$configDir = Join-Path $env:USERPROFILE ".openbro"
if (-not (Test-Path $configDir)) {
    Write-Info "No config dir at $configDir"
} elseif (Test-IsDevFolder $configDir) {
    Write-Skip "$configDir - looks like a dev folder. Skipping."
} else {
    $size = (Get-ChildItem $configDir -Recurse -File -ErrorAction SilentlyContinue |
             Measure-Object -Property Length -Sum).Sum
    $sizeMB = [math]::Round($size / 1MB, 2)
    Write-Info "$configDir ($sizeMB MB)"
    $shouldDelete = $false
    if ($KeepData) {
        Write-Info "Kept (-KeepData)"
    } elseif ($DryRun) {
        Write-Host "  WOULD DELETE: $configDir" -ForegroundColor Yellow
    } elseif ($Force) {
        $shouldDelete = $true
    } else {
        $r = Read-Host "  Delete config dir? [y/N]"
        $shouldDelete = ($r -match "^[yY]")
    }
    if ($shouldDelete) {
        Remove-Item -Path $configDir -Recurse -Force -ErrorAction SilentlyContinue
        Write-OK "Removed $configDir"
    } elseif (-not $DryRun -and -not $KeepData) {
        Write-Info "Kept (user declined)"
    }
}

# ─── Step 4/5: WHITELISTED storage paths only (from config) ───────────
# Critical: only paths the user EXPLICITLY chose in the wizard. No regex
# scan over drives. If they custom-named their data folder something we
# don't know about, that's fine — we leave it alone.
Write-Step 4 5 "User-chosen storage paths (from config)..."
$storagePaths = @()
foreach ($p in @($customBase, $customModels)) {
    if ($p -and (Test-Path $p) -and ($p -ne $configDir) -and -not ($storagePaths -contains $p)) {
        $storagePaths += $p
    }
}
if ($storagePaths.Count -eq 0) {
    Write-Info "No custom storage paths in config (or config already gone)"
} else {
    foreach ($p in $storagePaths) {
        if (Test-IsDevFolder $p) {
            Write-Skip "$p - has .git/pyproject.toml; treating as source clone. Skipping."
            continue
        }
        $size = (Get-ChildItem $p -Recurse -File -ErrorAction SilentlyContinue |
                 Measure-Object -Property Length -Sum).Sum
        $sizeGB = [math]::Round($size / 1GB, 2)
        Write-Info "$p ($sizeGB GB)"
        $shouldDelete = $false
        if ($KeepData) {
            Write-Info "Kept (-KeepData)"
        } elseif ($DryRun) {
            Write-Host "  WOULD DELETE: $p" -ForegroundColor Yellow
        } elseif ($Force) {
            $shouldDelete = $true
        } else {
            $r = Read-Host "  Delete this storage folder? [y/N]"
            $shouldDelete = ($r -match "^[yY]")
        }
        if ($shouldDelete) {
            Remove-Item -Path $p -Recurse -Force -ErrorAction SilentlyContinue
            if (Test-Path $p) {
                Write-Warn "Partial delete (some files in use): $p"
            } else {
                Write-OK "Removed $p"
            }
        } elseif (-not $DryRun -and -not $KeepData) {
            Write-Info "Kept (user declined)"
        }
    }
}

# ─── Step 5/5: HuggingFace cache (Whisper STT + GGUF LLM) ─────────────
Write-Step 5 5 "HuggingFace cache (Whisper STT + GGUF models)..."
$hfCache = Join-Path $env:USERPROFILE ".cache\huggingface"
if (-not (Test-Path $hfCache)) {
    Write-Info "No HF cache at $hfCache"
} elseif (Test-IsDevFolder $hfCache) {
    Write-Skip "$hfCache - looks like a dev folder. Skipping."
} else {
    $size = (Get-ChildItem $hfCache -Recurse -File -ErrorAction SilentlyContinue |
             Measure-Object -Property Length -Sum).Sum
    $sizeMB = [math]::Round($size / 1MB, 2)
    Write-Info "$hfCache ($sizeMB MB)"
    $shouldDelete = $false
    if ($KeepWhisper) {
        Write-Info "Kept (-KeepWhisper)"
    } elseif ($DryRun) {
        Write-Host "  WOULD DELETE: $hfCache" -ForegroundColor Yellow
    } elseif ($Force) {
        $shouldDelete = $true
    } else {
        $r = Read-Host "  Delete HuggingFace cache? [y/N]"
        $shouldDelete = ($r -match "^[yY]")
    }
    if ($shouldDelete) {
        Remove-Item -Path $hfCache -Recurse -Force -ErrorAction SilentlyContinue
        Write-OK "Removed $hfCache"
    } elseif (-not $DryRun -and -not $KeepWhisper) {
        Write-Info "Kept (user declined)"
    }
}

# ─── Final ───────────────────────────────────────────────────────────
Write-Host ""
if ($DryRun) {
    Write-Host "  +-------------------------------------------+" -ForegroundColor Green
    Write-Host "  |   DRY RUN complete. Nothing was deleted.  |" -ForegroundColor Green
    Write-Host "  +-------------------------------------------+" -ForegroundColor Green
    Write-Host ""
    Write-Host "  Review the 'WOULD DELETE' lines above." -ForegroundColor Cyan
    Write-Host "  When ready, run with -Commit to actually delete:" -ForegroundColor Cyan
    Write-Host ""
    Write-Host "    .\uninstall.ps1 -Commit                  # prompts for each item" -ForegroundColor White
    Write-Host "    .\uninstall.ps1 -Commit -Force           # no prompts" -ForegroundColor White
    Write-Host "    .\uninstall.ps1 -Commit -KeepData        # keep storage drive" -ForegroundColor White
    Write-Host ""
} else {
    $leftover = $null
    try {
        $leftover = (Get-Command openbro -ErrorAction SilentlyContinue).Source
    } catch {}
    if ($leftover) {
        Write-Warn "openbro still on PATH: $leftover (may need admin to remove)"
    }
    Write-Host "  +-------------------------------------------+" -ForegroundColor Green
    Write-Host "  |   OpenBro uninstalled.                    |" -ForegroundColor Green
    Write-Host "  +-------------------------------------------+" -ForegroundColor Green
}
Write-Host ""
