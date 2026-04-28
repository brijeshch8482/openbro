# OpenBro Uninstaller for Windows

$ErrorActionPreference = "Stop"

Write-Host ""
Write-Host "OpenBro Uninstaller" -ForegroundColor Yellow
Write-Host "===================" -ForegroundColor Yellow
Write-Host ""

# Confirm
$confirm = Read-Host "Are you sure you want to uninstall OpenBro? (y/N)"
if ($confirm -ne "y" -and $confirm -ne "Y") {
    Write-Host "Cancelled." -ForegroundColor DarkGray
    exit 0
}

# Uninstall pip package
Write-Host "[1/3] Removing OpenBro package..." -ForegroundColor Green
$python = $null
foreach ($cmd in @("python", "python3", "py")) {
    try {
        & $cmd --version 2>&1 | Out-Null
        $python = $cmd
        break
    } catch {}
}

if ($python) {
    & $python -m pip uninstall openbro -y 2>&1
    Write-Host "  Package removed." -ForegroundColor Green
} else {
    Write-Host "  Python not found, skipping pip uninstall." -ForegroundColor Yellow
}

# Ask about data
$configDir = Join-Path $env:USERPROFILE ".openbro"
Write-Host ""
Write-Host "[2/3] Data cleanup" -ForegroundColor Green

if (Test-Path $configDir) {
    Write-Host "  Config directory found: $configDir" -ForegroundColor DarkGray

    # Show what's there
    $size = (Get-ChildItem -Path $configDir -Recurse -File -ErrorAction SilentlyContinue | Measure-Object -Property Length -Sum).Sum
    $sizeMB = [math]::Round($size / 1MB, 2)
    Write-Host "  Size: $sizeMB MB" -ForegroundColor DarkGray

    $deleteData = Read-Host "  Delete config, history, and memory? (y/N)"
    if ($deleteData -eq "y" -or $deleteData -eq "Y") {
        Remove-Item -Path $configDir -Recurse -Force
        Write-Host "  Data deleted." -ForegroundColor Green
    } else {
        Write-Host "  Data kept at: $configDir" -ForegroundColor DarkGray
    }
} else {
    Write-Host "  No data directory found." -ForegroundColor DarkGray
}

# Check for custom storage
Write-Host ""
Write-Host "[3/3] Custom storage check" -ForegroundColor Green
Write-Host "  If you set a custom storage path during setup," -ForegroundColor DarkGray
Write-Host "  that data is NOT automatically deleted." -ForegroundColor DarkGray
Write-Host "  Check your config for the path and delete manually if needed." -ForegroundColor DarkGray

Write-Host ""
Write-Host "============================================" -ForegroundColor Green
Write-Host "  OpenBro uninstalled successfully!" -ForegroundColor Green
Write-Host "============================================" -ForegroundColor Green
Write-Host ""
Write-Host "  Sad to see you go, bhai. Come back anytime!" -ForegroundColor Cyan
Write-Host ""
