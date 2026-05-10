#!/usr/bin/env bash
# OpenBro Installer for Linux/macOS
# Zero-friction one-line install:
#   curl -fsSL https://github.com/brijeshch8482/openbro/raw/main/scripts/install.sh | bash

set -e

REPO="brijeshch8482/openbro"
EXTRAS="${OPENBRO_EXTRAS:-all,voice}"
BRANCH="${OPENBRO_BRANCH:-main}"
OPENBRO_NO_SETUP="${OPENBRO_NO_SETUP:-0}"
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

# ─── Step 1/5: Python (robust detect + install) ──────────────
step 1 5 "Checking Python..."

# Probe a python exe; echo "cmd|version|minor" if it's real Python 3.10+, nothing otherwise.
probe_python() {
    local exe="$1"
    if ! command -v "$exe" &> /dev/null; then return 1; fi
    local resolved
    resolved=$(command -v "$exe")
    # Reject Microsoft Store stub (only matters in WSL but cheap to check)
    case "$resolved" in *WindowsApps*) return 1 ;; esac
    local raw
    raw=$("$exe" --version 2>&1) || return 1
    local minor
    minor=$(echo "$raw" | grep -oE '3\.[0-9]+' | head -1 | cut -d. -f2)
    if [ -z "$minor" ]; then return 1; fi
    echo "$exe|$raw|$minor|$resolved"
    return 0
}

# Find best available Python (highest minor ≥ 10). Echo "cmd|ver|minor|path" or "OLD|cmd|ver|minor|path".
find_python() {
    local best_cmd="" best_minor=0 best_ver="" best_path=""
    local old_cmd="" old_minor=0 old_ver="" old_path=""

    # Iterate likely candidates
    local candidates="python3 python python3.13 python3.12 python3.11 python3.10"
    for c in $candidates; do
        local r
        r=$(probe_python "$c") || continue
        IFS='|' read -r cmd ver minor path <<< "$r"
        if [ "$minor" -ge 10 ]; then
            if [ "$minor" -gt "$best_minor" ]; then
                best_cmd="$cmd"; best_minor="$minor"; best_ver="$ver"; best_path="$path"
            fi
        else
            if [ "$minor" -gt "$old_minor" ]; then
                old_cmd="$cmd"; old_minor="$minor"; old_ver="$ver"; old_path="$path"
            fi
        fi
    done

    # Also scan common install dirs (macOS Homebrew, manual installs)
    for p in /usr/local/bin/python3.* /opt/homebrew/bin/python3.* /opt/python3*/bin/python3 ~/.pyenv/versions/3.*/bin/python3 ; do
        [ -x "$p" ] || continue
        local r
        r=$(probe_python "$p") || continue
        IFS='|' read -r cmd ver minor path <<< "$r"
        if [ "$minor" -ge 10 ] && [ "$minor" -gt "$best_minor" ]; then
            best_cmd="$cmd"; best_minor="$minor"; best_ver="$ver"; best_path="$path"
        fi
    done

    if [ -n "$best_cmd" ]; then
        echo "OK|$best_cmd|$best_ver|$best_minor|$best_path"
        return 0
    fi
    if [ -n "$old_cmd" ]; then
        echo "OLD|$old_cmd|$old_ver|$old_minor|$old_path"
        return 0
    fi
    return 1
}

install_python() {
    if [[ "$OSTYPE" == "darwin"* ]]; then
        if command -v brew &> /dev/null; then
            info "Installing via Homebrew..."
            brew install python@3.12 2>&1 | tail -5
            return $?
        fi
        info "Installing Homebrew first..."
        /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
        brew install python@3.12
        return $?
    fi

    if [ -f /etc/debian_version ]; then
        info "Installing via apt-get (sudo required)..."
        sudo apt-get update -qq && sudo apt-get install -y python3 python3-pip python3-venv
        return $?
    fi
    if [ -f /etc/redhat-release ] || [ -f /etc/fedora-release ]; then
        info "Installing via dnf (sudo required)..."
        sudo dnf install -y python3 python3-pip
        return $?
    fi
    if [ -f /etc/arch-release ]; then
        info "Installing via pacman (sudo required)..."
        sudo pacman -S --noconfirm python python-pip
        return $?
    fi

    # Unknown distro: try common universal paths
    if command -v apk &> /dev/null; then
        sudo apk add --no-cache python3 py3-pip
        return $?
    fi
    return 1
}

# ── Detect ──
DETECT=$(find_python || true)
PYTHON=""
if [ -n "$DETECT" ]; then
    STATUS=$(echo "$DETECT" | cut -d'|' -f1)
    if [ "$STATUS" = "OK" ]; then
        PYTHON=$(echo "$DETECT" | cut -d'|' -f2)
        VER=$(echo "$DETECT" | cut -d'|' -f3)
        PATH_=$(echo "$DETECT" | cut -d'|' -f5)
        ok "Found $VER at $PATH_"
    elif [ "$STATUS" = "OLD" ]; then
        OLD_VER=$(echo "$DETECT" | cut -d'|' -f3)
        warn "Found old $OLD_VER — needs 3.10+. Installing newer..."
        if ! install_python; then
            err "Python install failed."
            echo -e "  Manual fix:" >&2
            if [[ "$OSTYPE" == "darwin"* ]]; then
                echo -e "    ${B}brew install python@3.12${N}"
            elif [ -f /etc/debian_version ]; then
                echo -e "    ${B}sudo apt install python3 python3-pip python3-venv${N}"
            else
                echo -e "    ${B}https://python.org/downloads/${N}"
            fi
            exit 1
        fi
        DETECT=$(find_python || true)
        STATUS=$(echo "$DETECT" | cut -d'|' -f1)
        if [ "$STATUS" = "OK" ]; then
            PYTHON=$(echo "$DETECT" | cut -d'|' -f2)
            VER=$(echo "$DETECT" | cut -d'|' -f3)
            ok "Installed $VER"
        else
            err "Install ran but Python still not detected. Open a new shell and retry."
            exit 1
        fi
    fi
else
    warn "No Python found — auto-installing..."
    if ! install_python; then
        err "Python install failed across all strategies."
        echo "" >&2
        echo "  Possible causes:" >&2
        echo "  - No internet" >&2
        echo "  - sudo password not entered" >&2
        echo "  - Corporate / locked-down system" >&2
        echo "" >&2
        echo "  Install Python 3.10+ manually then re-run." >&2
        exit 1
    fi
    DETECT=$(find_python || true)
    STATUS=$(echo "$DETECT" | cut -d'|' -f1)
    if [ "$STATUS" = "OK" ]; then
        PYTHON=$(echo "$DETECT" | cut -d'|' -f2)
        VER=$(echo "$DETECT" | cut -d'|' -f3)
        ok "Installed $VER"
    else
        err "Install ran but Python still not detected. Open a new shell and retry."
        exit 1
    fi
fi

# Final sanity check
if ! "$PYTHON" -c "import sys; print('Python exe:', sys.executable)" 2>/dev/null; then
    err "Python found but failed to run."
    exit 1
fi

# ─── Step 1.5: Node.js (for MCP servers via npx) ────────────
NO_NODE="${OPENBRO_NO_NODE:-0}"
if [ "$NO_NODE" != "1" ]; then
    echo ""
    echo -e "${C}[1.5/5] Checking Node.js (for MCP servers)...${N}"
    NODE_OK=0
    if command -v node &> /dev/null; then
        NODE_VER=$(node --version 2>/dev/null | sed 's/^v//')
        NODE_MAJOR=$(echo "$NODE_VER" | cut -d. -f1)
        if [ -n "$NODE_MAJOR" ] && [ "$NODE_MAJOR" -ge 18 ] 2>/dev/null; then
            ok "Node.js v$NODE_VER found"
            NODE_OK=1
        fi
    fi

    if [ "$NODE_OK" != "1" ]; then
        warn "Node.js not found - installing..."
        if [[ "$OSTYPE" == "darwin"* ]]; then
            command -v brew &> /dev/null && brew install node && NODE_OK=1
        elif [ -f /etc/debian_version ]; then
            # Use NodeSource for a recent LTS
            curl -fsSL https://deb.nodesource.com/setup_lts.x | sudo -E bash - 2>/dev/null \
                && sudo apt-get install -y nodejs && NODE_OK=1
        elif [ -f /etc/redhat-release ] || [ -f /etc/fedora-release ]; then
            sudo dnf install -y nodejs && NODE_OK=1
        elif [ -f /etc/arch-release ]; then
            sudo pacman -S --noconfirm nodejs npm && NODE_OK=1
        fi

        if [ "$NODE_OK" = "1" ] && command -v node &> /dev/null; then
            ok "Installed Node.js $(node --version)"
        else
            warn "Could not auto-install Node.js. MCP servers using npx will fail."
            info "Install manually: https://nodejs.org/"
        fi
    fi
fi

# ─── Step 2/5: pip + OpenBro ─────────────────────────────────
step 2 5 "Installing OpenBro [$EXTRAS] (this may take 1-2 minutes)..."
"$PYTHON" -m pip install --upgrade pip --quiet 2>/dev/null || true

PKG_SPEC="openbro[$EXTRAS]"
# llama-cpp-python wheels live on a separate index (NOT PyPI). Without this,
# pip downloads a 68 MB source tarball and tries to compile — usually fails.
LLAMA_WHEEL_INDEX="https://abetlen.github.io/llama-cpp-python/whl/cpu"
if "$PYTHON" -m pip install --upgrade --extra-index-url "$LLAMA_WHEEL_INDEX" "$PKG_SPEC" --quiet 2>/dev/null; then
    :
else
    info "PyPI install failed, installing from GitHub ($BRANCH)..."
    # PEP 508 direct-URL form (pip >= 23 rejects the old '#egg=name[extra]')
    "$PYTHON" -m pip install --upgrade \
        --extra-index-url "$LLAMA_WHEEL_INDEX" \
        "openbro[$EXTRAS] @ git+https://github.com/$REPO.git@$BRANCH"
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

# ─── Step 4/5: PATH check ────────────────────────────────────
step 4 5 "Checking openbro command..."
if command -v openbro &> /dev/null; then
    ok "'openbro' command available"
    OPENBRO_CMD="openbro"
else
    warn "'openbro' not on PATH yet — using fallback"
    OPENBRO_CMD="$PYTHON -m openbro"
fi

# ─── Step 5/5: Configure LLM (auto-runs wizard) ──────────────
step 5 5 "Setting up your LLM..."
echo -e "  ${D}Pick offline (free, Ollama) or online (Claude / GPT / Groq).${N}"
echo -e "  ${D}Offline: model auto-downloads. Online: just paste your API key.${N}"
echo ""

if [ "$OPENBRO_NO_SETUP" != "1" ] && [ -t 0 ]; then
    read -p "  Configure now? [Y/n] " -n 1 -r resp
    echo ""
    if [[ -z "$resp" || "$resp" =~ ^[Yy]$ ]]; then
        echo ""
        # --setup runs the wizard which handles: provider pick, Ollama install +
        # model download, cloud API keys, storage, personality, optional Telegram.
        $OPENBRO_CMD --setup
    else
        info "Skipped. Run 'openbro --setup' anytime to configure."
    fi
elif [ "$OPENBRO_NO_SETUP" = "1" ]; then
    info "Skipped (OPENBRO_NO_SETUP=1)"
fi

echo ""
echo -e "${G}  ╔═══════════════════════════════════════════╗${N}"
echo -e "${G}  ║       ✓ OpenBro is ready!                ║${N}"
echo -e "${G}  ╚═══════════════════════════════════════════╝${N}"
echo ""
echo -e "  ${B}Quick commands:${N}"
echo -e "    ${C}openbro${N}              ${D}Start chatting${N}"
echo -e "    ${C}openbro --voice${N}      ${D}Voice mode (mic + TTS)${N}"
echo -e "    ${C}openbro --telegram${N}   ${D}Run as Telegram bot${N}"
echo -e "    ${C}openbro --setup${N}      ${D}Re-run setup wizard${N}"
echo -e "    ${C}openbro --help${N}       ${D}All flags${N}"
echo ""

if [ "$NO_LAUNCH" != "1" ] && [ -t 0 ]; then
    read -p "  Start chatting now? [Y/n] " -n 1 -r launch
    echo ""
    if [[ -z "$launch" || "$launch" =~ ^[Yy]$ ]]; then
        echo ""
        $OPENBRO_CMD
    else
        info "Run 'openbro' anytime to start."
        echo ""
    fi
fi
