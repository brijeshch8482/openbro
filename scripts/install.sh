#!/usr/bin/env bash
# OpenBro Installer for Linux/macOS
# Zero-friction one-line install:
#   curl -fsSL https://github.com/brijeshch8482/openbro/raw/main/scripts/install.sh | bash

set -e

REPO="brijeshch8482/openbro"
EXTRAS="${OPENBRO_EXTRAS:-all,voice}"
BRANCH="${OPENBRO_BRANCH:-main}"
NO_OLLAMA="${OPENBRO_NO_OLLAMA:-0}"
NO_LAUNCH="${OPENBRO_NO_LAUNCH:-0}"

# Colors
C='\033[0;36m'   # Cyan
G='\033[0;32m'   # Green
Y='\033[1;33m'   # Yellow
R='\033[0;31m'   # Red
D='\033[2m'      # Dim
B='\033[1m'      # Bold
N='\033[0m'      # Reset

step()  { echo -e "\n${C}[$1/$2] $3${N}"; }
ok()    { echo -e "  ${G}✓${N} $1"; }
info()  { echo -e "  ${D}$1${N}"; }
warn()  { echo -e "  ${Y}!${N} $1"; }
err()   { echo -e "  ${R}✗${N} $1"; }

echo ""
echo -e "${C}  ╔═══════════════════════════════════════════╗${N}"
echo -e "${C}  ║          OpenBro Installer v1.0          ║${N}"
echo -e "${C}  ║      Tera Apna AI Bro - Open Source      ║${N}"
echo -e "${C}  ╚═══════════════════════════════════════════╝${N}"

# ─── Step 1/5: Python (auto-install if missing) ──────────────
step 1 5 "Checking Python..."

find_python() {
    for cmd in python3 python; do
        if command -v "$cmd" &> /dev/null; then
            v=$("$cmd" --version 2>&1)
            m=$(echo "$v" | grep -oE '3\.[0-9]+' | head -1 | cut -d. -f2)
            if [ -n "$m" ] && [ "$m" -ge 10 ] 2>/dev/null; then
                echo "$cmd"
                return 0
            fi
        fi
    done
    return 1
}

PYTHON=$(find_python)
if [ -n "$PYTHON" ]; then
    ok "Found $($PYTHON --version 2>&1)"
else
    warn "Python 3.10+ not found — auto-installing..."
    INSTALL_OK=0

    if [[ "$OSTYPE" == "darwin"* ]]; then
        # macOS: prefer brew
        if command -v brew &> /dev/null; then
            info "Installing via Homebrew..."
            brew install python@3.12 && INSTALL_OK=1
        else
            info "Installing Homebrew first (needs admin)..."
            /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)" && \
                brew install python@3.12 && INSTALL_OK=1
        fi
    elif [ -f /etc/debian_version ]; then
        info "Installing via apt-get (needs sudo)..."
        sudo apt-get update -qq && \
            sudo apt-get install -y python3 python3-pip python3-venv && INSTALL_OK=1
    elif [ -f /etc/redhat-release ] || [ -f /etc/fedora-release ]; then
        info "Installing via dnf (needs sudo)..."
        sudo dnf install -y python3 python3-pip && INSTALL_OK=1
    elif [ -f /etc/arch-release ]; then
        info "Installing via pacman (needs sudo)..."
        sudo pacman -S --noconfirm python python-pip && INSTALL_OK=1
    else
        warn "Unknown distro — cannot auto-install Python."
    fi

    if [ "$INSTALL_OK" != "1" ]; then
        err "Auto-install failed. Install Python 3.10+ manually then re-run:"
        if [[ "$OSTYPE" == "darwin"* ]]; then
            echo -e "  ${B}brew install python@3.12${N}"
        elif [ -f /etc/debian_version ]; then
            echo -e "  ${B}sudo apt install python3 python3-pip python3-venv${N}"
        elif [ -f /etc/redhat-release ]; then
            echo -e "  ${B}sudo dnf install python3 python3-pip${N}"
        else
            echo -e "  ${B}https://python.org/downloads/${N}"
        fi
        exit 1
    fi

    # Re-detect after install
    PYTHON=$(find_python)
    if [ -n "$PYTHON" ]; then
        ok "Installed $($PYTHON --version 2>&1)"
    else
        err "Python installed but not on PATH. Open a new shell and re-run."
        exit 1
    fi
fi

# ─── Step 2/5: pip + OpenBro ─────────────────────────────────
step 2 5 "Installing OpenBro [$EXTRAS] (this may take 1-2 minutes)..."
"$PYTHON" -m pip install --upgrade pip --quiet 2>/dev/null || true

PKG_SPEC="openbro[$EXTRAS]"
if "$PYTHON" -m pip install --upgrade "$PKG_SPEC" --quiet 2>/dev/null; then
    :
else
    info "PyPI install failed, installing from GitHub ($BRANCH)..."
    "$PYTHON" -m pip install --upgrade \
        "git+https://github.com/$REPO.git@$BRANCH#egg=openbro[$EXTRAS]"
fi
ok "OpenBro installed"

# ─── Step 3/5: Verify ────────────────────────────────────────
step 3 5 "Verifying installation..."
if VER=$("$PYTHON" -c "import openbro; print(openbro.__version__)" 2>&1); then
    ok "OpenBro v$VER ready"
else
    err "Verification failed: $VER"
    exit 1
fi

# ─── Step 4/5: Ollama (optional) ─────────────────────────────
step 4 5 "Checking Ollama (offline mode)..."
OLLAMA_INSTALLED=false
if command -v ollama &> /dev/null; then
    ok "Ollama found: $(ollama --version 2>&1 | head -1)"
    OLLAMA_INSTALLED=true
elif [ "$NO_OLLAMA" = "1" ]; then
    info "Skipped (OPENBRO_NO_OLLAMA=1)"
else
    warn "Ollama not installed (needed for free offline LLM)"
    if [ -t 0 ]; then
        read -p "  Install Ollama now? [Y/n] " -n 1 -r resp
        echo ""
        if [[ -z "$resp" || "$resp" =~ ^[Yy]$ ]]; then
            info "Running Ollama installer..."
            curl -fsSL https://ollama.com/install.sh | sh && ok "Ollama installed" || \
                warn "Ollama install failed. Try manually: https://ollama.com"
        else
            info "Skipped. Install later: curl -fsSL https://ollama.com/install.sh | sh"
        fi
    else
        info "Non-interactive shell - skipped. Install: https://ollama.com"
    fi
fi

# ─── Step 5/5: PATH check ────────────────────────────────────
step 5 5 "Checking openbro on PATH..."
if command -v openbro &> /dev/null; then
    ok "'openbro' command available"
else
    warn "'openbro' not on PATH yet — start a new shell or use:"
    echo -e "    ${B}$PYTHON -m openbro${N}"
fi

echo ""
echo -e "${G}  ╔═══════════════════════════════════════════╗${N}"
echo -e "${G}  ║       ✓ Installation complete!           ║${N}"
echo -e "${G}  ╚═══════════════════════════════════════════╝${N}"
echo ""
echo -e "  ${B}Quick commands:${N}"
echo -e "    ${C}openbro${N}              ${D}Start chatting (first run launches setup)${N}"
echo -e "    ${C}openbro --voice${N}      ${D}Voice mode (mic + TTS)${N}"
echo -e "    ${C}openbro --telegram${N}   ${D}Run as Telegram bot${N}"
echo -e "    ${C}openbro --setup${N}      ${D}Re-run wizard${N}"
echo -e "    ${C}openbro --help${N}       ${D}All flags${N}"
echo ""

if [ "$NO_LAUNCH" != "1" ] && [ -t 0 ]; then
    read -p "  Launch OpenBro now? [Y/n] " -n 1 -r launch
    echo ""
    if [[ -z "$launch" || "$launch" =~ ^[Yy]$ ]]; then
        echo ""
        openbro
    else
        info "Run 'openbro' anytime to start."
        echo ""
    fi
fi
