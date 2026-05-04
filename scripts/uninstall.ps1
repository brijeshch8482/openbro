# OpenBro Uninstaller for Windows
# One-line uninstall:
#   iwr -useb https://github.com/brijeshch8482/openbro/raw/main/scripts/uninstall.ps1 | iex

[CmdletBinding()]
param(
    [switch]$Force,
    [switch]$KeepData,
    [switch]$KeepOllama,
    [switch]$KeepWhisper
)

$ErrorActionPreference = "Continue"

function Write-Step($num, $total, $msg) {
    Write-Host ""
    Write-Host "[$num/$total] $msg" -ForegroundColor Cyan
}

function Write-OK($msg)   { Write-Host "  ✓ $msg" -ForegroundColor Green }
function Write-Info($msg) { Write-Host "  $msg" -ForegroundColor DarkGray }
function Write-Warn($msg) { Write-Host "  ! $msg" -ForegroundColor Yellow }

Write-Host ""
Write-Host "  ╔═══════════════════════════════════════════╗" -ForegroundColor Yellow
Write-Host "  ║         OpenBro Uninstaller v1.0         ║" -ForegroundColor Yellow
Write-Host "  ╚═══════════════════════════════════════════╝" -ForegroundColor Yellow
Write-Host ""

# Confirm
if (-not $Force) {
    $confirm = Read-Host "  Sure you want to uninstall OpenBro? [y/N]"
    if ($confirm -notmatch "^[yY]") {
        Write-Host "  Cancelled." -ForegroundColor DarkGray
        exit 0
    }
}

# ─── Find Python (so we can read config + uninstall pip pkg) ──
$python = $null
foreach ($cmd in @("python", "python3", "py")) {
    try {
        & $cmd --version 2>&1 | Out-Null
        $python = $cmd
        break
    } catch {}
}

# Read config to find custom storage path BEFORE uninstalling pkg
$customBase = $null
$customModels = $null
if ($python) {
    try {
        $cfgJson = & $python -c @"
import json
try:
    from openbro.utils.config import load_config
    c = load_config()
    print(json.dumps({'base': (c.get('storage') or {}).get('base_dir'), 'models': (c.get('storage') or {}).get('models_dir')}))
except Exception:
    print('{}')
"@ 2>$null
        if ($cfgJson) {
            $cfg = $cfgJson | ConvertFrom-Json
            $customBase = $cfg.base
            $customModels = $cfg.models
        }
    } catch {}
}

# ─── Step 1/5: Pip package ─────────────────────────────────
Write-Step 1 5 "Removing OpenBro Python package..."
if ($python) {
    & $python -m pip uninstall openbro -y 2>&1 | Out-Null
    if ($LASTEXITCODE -eq 0) {
        Write-OK "openbro pip package removed"
    } else {
        Write-Warn "openbro was not installed via pip (or already removed)"
    }
} else {
    Write-Warn "Python not found, skipping pip uninstall"
}

# ─── Step 2/5: Config dir ──────────────────────────────────
Write-Step 2 5 "Cleaning config + memory + logs..."
$configDir = Join-Path $env:USERPROFILE ".openbro"
if (Test-Path $configDir) {
    $size = 0
    try {
        $size = (Get-ChildItem $configDir -Recurse -File -ErrorAction SilentlyContinue |
                 Measure-Object -Property Length -Sum).Sum
    } catch {}
    $sizeMB = [math]::Round($size / 1MB, 2)
    Write-Info "$configDir ($sizeMB MB)"
    $del = $true
    if ($KeepData) { $del = $false }
    elseif (-not $Force) {
        $r = Read-Host "  Delete config, memory, history, audit log? [Y/n]"
        $del = ($r -eq "" -or $r -match "^[yY]")
    }
    if ($del) {
        Remove-Item -Path $configDir -Recurse -Force -ErrorAction SilentlyContinue
        Write-OK "Removed $configDir"
    } else {
        Write-Info "Kept $configDir"
    }
} else {
    Write-Info "No config dir at $configDir"
}

# ─── Step 3/5: Custom storage (if set during setup) ────────
Write-Step 3 5 "Checking custom storage paths..."
$paths = @()
if ($customBase -and $customBase -ne $configDir -and (Test-Path $customBase)) { $paths += $customBase }
if ($customModels -and $customModels -ne $configDir -and (Test-Path $customModels) -and ($paths -notcontains $customModels)) { $paths += $customModels }

if ($paths.Count -eq 0) {
    Write-Info "No custom storage paths found"
} else {
    foreach ($p in $paths) {
        $size = 0
        try {
            $size = (Get-ChildItem $p -Recurse -File -ErrorAction SilentlyContinue |
                     Measure-Object -Property Length -Sum).Sum
        } catch {}
        $sizeMB = [math]::Round($size / 1MB, 2)
        Write-Info "$p ($sizeMB MB)"
        $del = $false
        if (-not $Force) {
            $r = Read-Host "  Delete this folder? [y/N]"
            $del = ($r -match "^[yY]")
        }
        if ($del) {
            Remove-Item -Path $p -Recurse -Force -ErrorAction SilentlyContinue
            Write-OK "Removed $p"
        } else {
            Write-Info "Kept $p"
        }
    }
}

# ─── Step 4/5: Ollama models (offline LLM cache, can be 5+ GB) ──
Write-Step 4 5 "Ollama models..."
$ollamaInstalled = $false
try {
    $null = Get-Command ollama -ErrorAction Stop
    $ollamaInstalled = $true
} catch {}

if (-not $ollamaInstalled) {
    Write-Info "Ollama not installed"
} elseif ($KeepOllama) {
    Write-Info "Skipped (-KeepOllama)"
} else {
    $models = & ollama list 2>$null | Select-Object -Skip 1
    if (-not $models) {
        Write-Info "No models downloaded"
    } else {
        Write-Host ""
        Write-Host "  Downloaded Ollama models:" -ForegroundColor White
        & ollama list
        Write-Host ""
        $r = "n"
        if (-not $Force) {
            $r = Read-Host "  Delete ALL Ollama models? (frees disk space) [y/N]"
        }
        if ($r -match "^[yY]") {
            $modelNames = (& ollama list 2>$null | Select-Object -Skip 1) -replace '^\s*(\S+)\s.*','$1' | Where-Object { $_ }
            foreach ($m in $modelNames) {
                & ollama rm $m 2>&1 | Out-Null
                Write-Info "removed $m"
            }
            Write-OK "Models removed"
        } else {
            Write-Info "Kept Ollama models"
        }

        if (-not $Force) {
            $r2 = Read-Host "  Uninstall Ollama itself too? [y/N]"
            if ($r2 -match "^[yY]") {
                Write-Info "Uninstalling Ollama via winget..."
                try {
                    & winget uninstall --id Ollama.Ollama --silent 2>&1 | Out-Null
                    Write-OK "Ollama uninstalled"
                } catch {
                    Write-Warn "winget failed. Uninstall manually from Settings → Apps."
                }
            }
        }
    }
}

# ─── Step 5/5: Whisper model cache (~140-700 MB) ─────────
Write-Step 5 5 "Whisper STT model cache..."
$whisperCache = Join-Path $env:USERPROFILE ".cache\huggingface\hub"
if (Test-Path $whisperCache) {
    $whisperFolders = Get-ChildItem $whisperCache -Directory -ErrorAction SilentlyContinue |
                      Where-Object { $_.Name -like "*whisper*" -or $_.Name -like "*faster*" }
    if ($whisperFolders) {
        $totalSize = ($whisperFolders | ForEach-Object { Get-ChildItem $_.FullName -Recurse -File -ErrorAction SilentlyContinue } | Measure-Object -Property Length -Sum).Sum
        $sizeMB = [math]::Round($totalSize / 1MB, 2)
        Write-Info "Whisper cache: $sizeMB MB"
        $del = $false
        if ($KeepWhisper) { $del = $false }
        elseif (-not $Force) {
            $r = Read-Host "  Delete Whisper model cache? [y/N]"
            $del = ($r -match "^[yY]")
        }
        if ($del) {
            $whisperFolders | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
            Write-OK "Cache cleared"
        } else {
            Write-Info "Kept Whisper cache"
        }
    } else {
        Write-Info "No Whisper cache found"
    }
} else {
    Write-Info "No HuggingFace cache dir"
}

Write-Host ""
Write-Host "  ╔═══════════════════════════════════════════╗" -ForegroundColor Green
Write-Host "  ║       ✓ OpenBro uninstalled.             ║" -ForegroundColor Green
Write-Host "  ╚═══════════════════════════════════════════╝" -ForegroundColor Green
Write-Host ""
Write-Host "  Sad to see you go, bhai. Come back anytime:" -ForegroundColor Cyan
Write-Host "    iwr -useb https://github.com/brijeshch8482/openbro/raw/main/scripts/install.ps1 | iex" -ForegroundColor DarkGray
Write-Host ""
