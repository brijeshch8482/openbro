# OpenBro Uninstaller for Windows — nuclear-grade cleanup
#
# One-line nuclear uninstall (deletes EVERYTHING openbro everywhere):
#   iwr -useb https://github.com/brijeshch8482/openbro/raw/main/scripts/uninstall.ps1 | iex
#
# Why this script is more aggressive than `pip uninstall`:
#   - Iterates EVERY installed Python (3.10/3.11/3.12/3.13/3.14) — users often
#     end up with openbro on multiple Pythons after manual pip experiments.
#   - Deletes orphan launcher .exe files left behind in Scripts/ when pip
#     uninstall doesn't clean them up (admin needed for system-wide ones).
#   - Removes the user-chosen storage drive (where GGUF models, memory DB,
#     skills, audit logs live) — reads it from config.yaml BEFORE deleting
#     the config.
#   - Sweeps the HuggingFace cache for both Whisper STT and GGUF LLM files.

[CmdletBinding()]
param(
    [switch]$Force,
    [switch]$KeepData,
    [switch]$KeepWhisper
)

$ErrorActionPreference = "Continue"

function Write-Step($num, $total, $msg) {
    Write-Host ""
    Write-Host "[$num/$total] $msg" -ForegroundColor Cyan
}
function Write-OK($msg)   { Write-Host "  [OK] $msg" -ForegroundColor Green }
function Write-Info($msg) { Write-Host "  $msg" -ForegroundColor DarkGray }
function Write-Warn($msg) { Write-Host "  ! $msg" -ForegroundColor Yellow }

Write-Host ""
Write-Host "  +-------------------------------------------+" -ForegroundColor Yellow
Write-Host "  |       OpenBro Nuclear Uninstaller         |" -ForegroundColor Yellow
Write-Host "  +-------------------------------------------+" -ForegroundColor Yellow
Write-Host ""

if (-not $Force) {
    Write-Host "  This will remove:" -ForegroundColor White
    Write-Host "    - openbro Python package from EVERY Python on this PC" -ForegroundColor DarkGray
    Write-Host "    - Orphan openbro.exe launchers in Scripts/ dirs" -ForegroundColor DarkGray
    Write-Host "    - Config / memory / audit log at ~/.openbro/" -ForegroundColor DarkGray
    Write-Host "    - Your chosen storage drive (GGUF models, memory DB)" -ForegroundColor DarkGray
    Write-Host "    - HuggingFace cache (Whisper + GGUF models)" -ForegroundColor DarkGray
    Write-Host ""
    $confirm = Read-Host "  Sure? [y/N]"
    if ($confirm -notmatch "^[yY]") {
        Write-Host "  Cancelled." -ForegroundColor DarkGray
        exit 0
    }
}

# ─── Step 0: kill any running openbro / python processes ──────────────
Write-Step 0 6 "Stopping running openbro processes..."
$killed = 0
try {
    $procs = Get-CimInstance Win32_Process -Filter "Name LIKE 'python%'" -ErrorAction SilentlyContinue
    foreach ($p in $procs) {
        if ($p.CommandLine -and ($p.CommandLine -match "openbro|OpenBro")) {
            try {
                Stop-Process -Id $p.ProcessId -Force -ErrorAction Stop
                $killed++
            } catch {}
        }
    }
    Get-Process -Name openbro -ErrorAction SilentlyContinue | ForEach-Object {
        try { Stop-Process -Id $_.Id -Force -ErrorAction Stop; $killed++ } catch {}
    }
} catch {}
if ($killed -gt 0) {
    Write-OK "Stopped $killed process(es)"
    Start-Sleep -Seconds 1
} else {
    Write-Info "No running openbro processes"
}

# ─── Discover ALL Python interpreters on the system ──────────────────
$pythons = New-Object System.Collections.ArrayList
foreach ($ver in @("3.10","3.11","3.12","3.13","3.14")) {
    try {
        $p = & py "-$ver" -c "import sys; print(sys.executable)" 2>$null
        if ($LASTEXITCODE -eq 0 -and $p) {
            [void]$pythons.Add(@{ Version=$ver; Exe=$p.Trim() })
        }
    } catch {}
}
# Also catch any `python` / `python3` on PATH not surfaced via `py`
foreach ($cmd in @("python","python3")) {
    try {
        $p = & $cmd -c "import sys; print(sys.executable)" 2>$null
        if ($LASTEXITCODE -eq 0 -and $p) {
            $exe = $p.Trim()
            if (-not ($pythons.Exe -contains $exe)) {
                [void]$pythons.Add(@{ Version="?"; Exe=$exe })
            }
        }
    } catch {}
}

# ─── Read storage path from config BEFORE uninstalling pkg ─────────────
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

# ─── Step 1/6: pip uninstall from EVERY Python ─────────────────────────
Write-Step 1 6 "Uninstalling openbro from $($pythons.Count) Python install(s)..."
foreach ($py in $pythons) {
    try {
        $out = & $py.Exe -m pip uninstall openbro -y 2>&1
        if ($out -match "Successfully uninstalled") {
            Write-OK "Python $($py.Version) ($($py.Exe)): removed"
        } else {
            Write-Info "Python $($py.Version) ($($py.Exe)): not installed"
        }
    } catch {
        Write-Info "Python $($py.Version): probe failed (skipping)"
    }
}

# ─── Step 2/6: delete orphan launcher .exe files ──────────────────────
Write-Step 2 6 "Removing orphan openbro.exe launchers..."
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
    if (Test-Path $exe) {
        try {
            Remove-Item $exe -Force -ErrorAction Stop
            Write-OK "Removed $exe"
        } catch {
            $adminNeeded += $exe
        }
    }
}
if ($adminNeeded.Count -gt 0) {
    Write-Warn "Need admin to delete these:"
    foreach ($p in $adminNeeded) { Write-Host "    $p" -ForegroundColor Yellow }
    Write-Info "Run this in an ADMIN PowerShell to finish cleanup:"
    foreach ($p in $adminNeeded) {
        Write-Host "    Remove-Item '$p' -Force" -ForegroundColor White
    }
}

# ─── Step 3/6: config dir ──────────────────────────────────────────────
Write-Step 3 6 "Cleaning config dir..."
$configDir = Join-Path $env:USERPROFILE ".openbro"
if (Test-Path $configDir) {
    $size = 0
    try {
        $size = (Get-ChildItem $configDir -Recurse -File -ErrorAction SilentlyContinue |
                 Measure-Object -Property Length -Sum).Sum
    } catch {}
    $sizeMB = [math]::Round($size / 1MB, 2)
    Write-Info "$configDir ($sizeMB MB)"
    if ($KeepData) {
        Write-Info "Kept (-KeepData)"
    } else {
        Remove-Item -Path $configDir -Recurse -Force -ErrorAction SilentlyContinue
        Write-OK "Removed $configDir"
    }
} else {
    Write-Info "No config dir"
}

# ─── Step 4/6: storage paths (user's chosen drive) ────────────────────
Write-Step 4 6 "Cleaning storage drives..."
$pathsToCheck = New-Object System.Collections.ArrayList
foreach ($p in @($customBase, $customModels)) {
    if ($p -and (Test-Path $p) -and ($p -ne $configDir) -and -not ($pathsToCheck -contains $p)) {
        [void]$pathsToCheck.Add($p)
    }
}
# Also catch any D:\OpenBro* / E:\OpenBro* folders (common pattern)
foreach ($drive in (Get-PSDrive -PSProvider FileSystem -ErrorAction SilentlyContinue)) {
    if ($drive.Root -match "^[CDEFGH]:\\$") {
        try {
            Get-ChildItem -Path $drive.Root -Directory -ErrorAction SilentlyContinue |
                Where-Object { $_.Name -match "^OpenBro" } |
                ForEach-Object {
                    if (-not ($pathsToCheck -contains $_.FullName)) {
                        [void]$pathsToCheck.Add($_.FullName)
                    }
                }
        } catch {}
    }
}

if ($pathsToCheck.Count -eq 0) {
    Write-Info "No storage paths found"
} else {
    foreach ($p in $pathsToCheck) {
        $size = 0
        try {
            $size = (Get-ChildItem $p -Recurse -File -ErrorAction SilentlyContinue |
                     Measure-Object -Property Length -Sum).Sum
        } catch {}
        $sizeGB = [math]::Round($size / 1GB, 2)
        Write-Info "$p ($sizeGB GB)"
        $del = $true
        if ($KeepData) { $del = $false }
        elseif (-not $Force) {
            $r = Read-Host "  Delete this folder? [Y/n]"
            $del = ($r -eq "" -or $r -match "^[yY]")
        }
        if ($del) {
            Remove-Item -Path $p -Recurse -Force -ErrorAction SilentlyContinue
            if (Test-Path $p) {
                Write-Warn "Couldn't fully delete $p (some files in use)"
            } else {
                Write-OK "Removed $p"
            }
        } else {
            Write-Info "Kept $p"
        }
    }
}

# ─── Step 5/6: HuggingFace cache (Whisper + GGUF) ─────────────────────
Write-Step 5 6 "HuggingFace cache (Whisper + GGUF)..."
$hfCache = Join-Path $env:USERPROFILE ".cache\huggingface"
if (Test-Path $hfCache) {
    $size = 0
    try {
        $size = (Get-ChildItem $hfCache -Recurse -File -ErrorAction SilentlyContinue |
                 Measure-Object -Property Length -Sum).Sum
    } catch {}
    $sizeMB = [math]::Round($size / 1MB, 2)
    Write-Info "$hfCache ($sizeMB MB)"
    $del = $true
    if ($KeepWhisper) { $del = $false }
    elseif (-not $Force) {
        $r = Read-Host "  Delete HuggingFace cache (Whisper + GGUF)? [Y/n]"
        $del = ($r -eq "" -or $r -match "^[yY]")
    }
    if ($del) {
        Remove-Item -Path $hfCache -Recurse -Force -ErrorAction SilentlyContinue
        Write-OK "Removed $hfCache"
    } else {
        Write-Info "Kept $hfCache"
    }
} else {
    Write-Info "No HF cache"
}

# ─── Step 6/6: verify nothing left on PATH ────────────────────────────
Write-Step 6 6 "Final verification..."
$leftover = $null
try {
    $leftover = (Get-Command openbro -ErrorAction SilentlyContinue).Source
} catch {}
if ($leftover) {
    Write-Warn "openbro still on PATH: $leftover"
    Write-Info "Run admin PowerShell and: Remove-Item '$leftover' -Force"
} else {
    Write-OK "openbro is gone from PATH"
}

Write-Host ""
Write-Host "  +-------------------------------------------+" -ForegroundColor Green
Write-Host "  |     OpenBro fully uninstalled.            |" -ForegroundColor Green
Write-Host "  +-------------------------------------------+" -ForegroundColor Green
Write-Host ""
Write-Host "  Re-install when ready:" -ForegroundColor Cyan
Write-Host "    `$sha=(iwr -useb 'https://api.github.com/repos/brijeshch8482/openbro/commits/main'|ConvertFrom-Json).sha" -ForegroundColor DarkGray
Write-Host "    iwr -useb `"https://raw.githubusercontent.com/brijeshch8482/openbro/`$sha/scripts/install.ps1`" | iex" -ForegroundColor DarkGray
Write-Host ""
