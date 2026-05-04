#!/usr/bin/env bash
# OpenBro Uninstaller for Linux/macOS
# One-line uninstall:
#   curl -fsSL https://github.com/brijeshch8482/openbro/raw/main/scripts/uninstall.sh | bash

FORCE="${OPENBRO_FORCE:-0}"
KEEP_DATA="${OPENBRO_KEEP_DATA:-0}"
KEEP_OLLAMA="${OPENBRO_KEEP_OLLAMA:-0}"
KEEP_WHISPER="${OPENBRO_KEEP_WHISPER:-0}"

C='\033[0;36m'
G='\033[0;32m'
Y='\033[1;33m'
R='\033[0;31m'
D='\033[2m'
B='\033[1m'
N='\033[0m'

step()  { echo -e "\n${C}[$1/$2] $3${N}"; }
ok()    { echo -e "  ${G}✓${N} $1"; }
info()  { echo -e "  ${D}$1${N}"; }
warn()  { echo -e "  ${Y}!${N} $1"; }

ask_yn() {  # ask_yn "prompt" default(y|n)
    local prompt="$1"
    local default="${2:-n}"
    if [ "$FORCE" = "1" ]; then
        # In force mode default 'y' means yes
        [ "$default" = "y" ] && return 0 || return 1
    fi
    local hint="[y/N]"
    [ "$default" = "y" ] && hint="[Y/n]"
    read -p "  $prompt $hint " -r resp
    if [ "$default" = "y" ]; then
        [[ -z "$resp" || "$resp" =~ ^[Yy]$ ]] && return 0 || return 1
    else
        [[ "$resp" =~ ^[Yy]$ ]] && return 0 || return 1
    fi
}

echo ""
echo -e "${Y}  ╔═══════════════════════════════════════════╗${N}"
echo -e "${Y}  ║         OpenBro Uninstaller v1.0         ║${N}"
echo -e "${Y}  ╚═══════════════════════════════════════════╝${N}"

if [ "$FORCE" != "1" ]; then
    if ! ask_yn "Sure you want to uninstall OpenBro?" "n"; then
        echo "  Cancelled."
        exit 0
    fi
fi

# ─── Find Python ──────────────────────────────────────────
PYTHON=""
for cmd in python3 python; do
    if command -v "$cmd" &> /dev/null; then
        PYTHON="$cmd"
        break
    fi
done

# Read config to find custom storage path BEFORE uninstalling pkg
CUSTOM_BASE=""
CUSTOM_MODELS=""
if [ -n "$PYTHON" ]; then
    CFG=$("$PYTHON" -c '
import json
try:
    from openbro.utils.config import load_config
    c = load_config()
    print(json.dumps({"base": (c.get("storage") or {}).get("base_dir"), "models": (c.get("storage") or {}).get("models_dir")}))
except Exception:
    print("{}")
' 2>/dev/null)
    CUSTOM_BASE=$(echo "$CFG" | sed -n 's/.*"base":\s*"\([^"]*\)".*/\1/p')
    CUSTOM_MODELS=$(echo "$CFG" | sed -n 's/.*"models":\s*"\([^"]*\)".*/\1/p')
fi

# ─── Step 1/5: Pip package ────────────────────────────────
step 1 5 "Removing OpenBro Python package..."
if [ -n "$PYTHON" ]; then
    "$PYTHON" -m pip uninstall openbro -y 2>&1 | grep -v "WARNING" || true
    if "$PYTHON" -c "import openbro" 2>/dev/null; then
        warn "openbro still importable — may be in another env"
    else
        ok "openbro pip package removed"
    fi
else
    warn "Python not found, skipping pip uninstall"
fi

# ─── Step 2/5: Config dir ─────────────────────────────────
step 2 5 "Cleaning config + memory + logs..."
CONFIG_DIR="$HOME/.openbro"
if [ -d "$CONFIG_DIR" ]; then
    SIZE=$(du -sh "$CONFIG_DIR" 2>/dev/null | cut -f1)
    info "$CONFIG_DIR ($SIZE)"
    DEL=1
    if [ "$KEEP_DATA" = "1" ]; then DEL=0; fi
    if [ "$DEL" = "1" ] && [ "$FORCE" != "1" ]; then
        ask_yn "Delete config, memory, history, audit log?" "y" && DEL=1 || DEL=0
    fi
    if [ "$DEL" = "1" ]; then
        rm -rf "$CONFIG_DIR" && ok "Removed $CONFIG_DIR"
    else
        info "Kept $CONFIG_DIR"
    fi
else
    info "No config dir at $CONFIG_DIR"
fi

# ─── Step 3/5: Custom storage ─────────────────────────────
step 3 5 "Checking custom storage paths..."
PATHS=()
[ -n "$CUSTOM_BASE" ] && [ "$CUSTOM_BASE" != "$CONFIG_DIR" ] && [ -d "$CUSTOM_BASE" ] && PATHS+=("$CUSTOM_BASE")
if [ -n "$CUSTOM_MODELS" ] && [ "$CUSTOM_MODELS" != "$CONFIG_DIR" ] && [ -d "$CUSTOM_MODELS" ]; then
    DUP=0
    for p in "${PATHS[@]}"; do [ "$p" = "$CUSTOM_MODELS" ] && DUP=1; done
    [ "$DUP" = "0" ] && PATHS+=("$CUSTOM_MODELS")
fi

if [ "${#PATHS[@]}" = "0" ]; then
    info "No custom storage paths found"
else
    for p in "${PATHS[@]}"; do
        SIZE=$(du -sh "$p" 2>/dev/null | cut -f1)
        info "$p ($SIZE)"
        if ask_yn "Delete this folder?" "n"; then
            rm -rf "$p" && ok "Removed $p"
        else
            info "Kept $p"
        fi
    done
fi

# ─── Step 4/5: Ollama models ──────────────────────────────
step 4 5 "Ollama models..."
if ! command -v ollama &> /dev/null; then
    info "Ollama not installed"
elif [ "$KEEP_OLLAMA" = "1" ]; then
    info "Skipped (OPENBRO_KEEP_OLLAMA=1)"
else
    MODELS=$(ollama list 2>/dev/null | tail -n +2)
    if [ -z "$MODELS" ]; then
        info "No models downloaded"
    else
        echo ""
        echo -e "  ${B}Downloaded Ollama models:${N}"
        ollama list
        echo ""
        if ask_yn "Delete ALL Ollama models? (frees disk)" "n"; then
            ollama list 2>/dev/null | tail -n +2 | awk '{print $1}' | while read -r m; do
                [ -n "$m" ] && ollama rm "$m" >/dev/null 2>&1 && info "removed $m"
            done
            ok "Models removed"
        else
            info "Kept Ollama models"
        fi

        if ask_yn "Uninstall Ollama itself?" "n"; then
            if [[ "$OSTYPE" == "darwin"* ]]; then
                brew uninstall ollama 2>/dev/null && ok "Ollama uninstalled" || \
                    warn "brew uninstall failed. Remove /Applications/Ollama.app manually."
            elif [ -f /etc/debian_version ]; then
                sudo apt-get remove -y ollama 2>/dev/null && ok "Ollama uninstalled" || \
                    warn "apt remove failed. Try: sudo rm -f /usr/local/bin/ollama"
            else
                sudo rm -f /usr/local/bin/ollama && ok "Removed /usr/local/bin/ollama" || \
                    warn "Could not remove ollama binary"
            fi
        fi
    fi
fi

# ─── Step 5/5: Whisper cache ──────────────────────────────
step 5 5 "Whisper STT model cache..."
WHISPER_CACHE="$HOME/.cache/huggingface/hub"
if [ -d "$WHISPER_CACHE" ]; then
    WFOLDERS=$(find "$WHISPER_CACHE" -maxdepth 1 -type d \( -name '*whisper*' -o -name '*faster*' \) 2>/dev/null)
    if [ -n "$WFOLDERS" ]; then
        TOTAL=$(echo "$WFOLDERS" | xargs du -sh 2>/dev/null | tail -1 | cut -f1)
        info "Whisper cache: $TOTAL"
        DEL=0
        if [ "$KEEP_WHISPER" != "1" ] && ask_yn "Delete Whisper model cache?" "n"; then
            DEL=1
        fi
        if [ "$DEL" = "1" ]; then
            echo "$WFOLDERS" | xargs rm -rf 2>/dev/null
            ok "Cache cleared"
        else
            info "Kept Whisper cache"
        fi
    else
        info "No Whisper cache found"
    fi
else
    info "No HuggingFace cache dir"
fi

echo ""
echo -e "${G}  ╔═══════════════════════════════════════════╗${N}"
echo -e "${G}  ║       ✓ OpenBro uninstalled.             ║${N}"
echo -e "${G}  ╚═══════════════════════════════════════════╝${N}"
echo ""
echo -e "${C}  Sad to see you go, bhai. Come back anytime:${N}"
echo -e "${D}    curl -fsSL https://github.com/brijeshch8482/openbro/raw/main/scripts/install.sh | bash${N}"
echo ""
