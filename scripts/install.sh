#!/usr/bin/env bash
# OpenBro Installer for Linux/macOS
# Run: curl -fsSL https://raw.githubusercontent.com/brijeshch8482/openbro/main/scripts/install.sh | bash

set -e

CYAN='\033[0;36m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
DIM='\033[2m'
NC='\033[0m'

echo ""
echo -e "${CYAN}   ____                   ____${NC}"
echo -e "${CYAN}  / __ \\____  ___  ____  / __ )_________${NC}"
echo -e "${CYAN} / / / / __ \\/ _ \\/ __ \\/ __  / ___/ __ \\${NC}"
echo -e "${CYAN}/ /_/ / /_/ /  __/ / / / /_/ / /  / /_/ /${NC}"
echo -e "${CYAN}\\____/ .___/\\___/_/ /_/_____/_/   \\____/${NC}"
echo -e "${CYAN}    /_/${NC}"
echo ""
echo -e "${YELLOW}OpenBro Installer - Tera Apna AI Bro${NC}"
echo -e "${YELLOW}======================================${NC}"
echo ""

# Check Python
echo -e "${GREEN}[1/4] Checking Python...${NC}"
PYTHON=""
for cmd in python3 python; do
    if command -v "$cmd" &> /dev/null; then
        version=$("$cmd" --version 2>&1 | grep -oP '3\.\d+')
        minor=$(echo "$version" | cut -d. -f2)
        if [ "$minor" -ge 10 ] 2>/dev/null; then
            PYTHON="$cmd"
            echo -e "${DIM}  Found: $("$cmd" --version)${NC}"
            break
        fi
    fi
done

if [ -z "$PYTHON" ]; then
    echo -e "${RED}  Python 3.10+ not found!${NC}"
    echo -e "${YELLOW}  Install Python:${NC}"
    if [[ "$OSTYPE" == "darwin"* ]]; then
        echo "    brew install python@3.12"
    else
        echo "    sudo apt install python3 python3-pip  (Ubuntu/Debian)"
        echo "    sudo dnf install python3 python3-pip  (Fedora)"
    fi
    exit 1
fi

# Install OpenBro
echo -e "${GREEN}[2/4] Installing OpenBro...${NC}"
"$PYTHON" -m pip install --upgrade pip 2>/dev/null || true
"$PYTHON" -m pip install openbro 2>&1 || {
    echo -e "${YELLOW}  pip install failed. Trying from GitHub...${NC}"
    "$PYTHON" -m pip install git+https://github.com/brijeshch8482/openbro.git 2>&1
}

echo -e "${GREEN}  OpenBro installed successfully!${NC}"

# Check Ollama (optional)
echo -e "${GREEN}[3/4] Checking Ollama (optional, for offline mode)...${NC}"
OLLAMA_INSTALLED=false
if command -v ollama &> /dev/null; then
    echo -e "${DIM}  Found: $(ollama --version)${NC}"
    OLLAMA_INSTALLED=true
else
    echo -e "${DIM}  Ollama not found (optional - needed only for offline mode)${NC}"
    echo -e "${DIM}  Install later: curl -fsSL https://ollama.ai/install.sh | sh${NC}"
fi

# Verify
echo -e "${GREEN}[4/4] Verifying installation...${NC}"
"$PYTHON" -c "import openbro; print(f'  OpenBro v{openbro.__version__} ready!')" 2>/dev/null || {
    echo -e "${YELLOW}  Warning: Could not verify installation${NC}"
}

echo ""
echo -e "${GREEN}============================================${NC}"
echo -e "${GREEN}  Installation complete!${NC}"
echo -e "${GREEN}============================================${NC}"
echo ""
echo -e "${CYAN}  Start OpenBro:  openbro${NC}"
echo -e "${CYAN}  Re-run setup:   openbro --setup${NC}"
echo -e "${CYAN}  Get help:       openbro --help${NC}"
echo ""

if [ "$OLLAMA_INSTALLED" = false ]; then
    echo -e "${YELLOW}  For offline mode, install Ollama:${NC}"
    echo -e "${YELLOW}    curl -fsSL https://ollama.ai/install.sh | sh${NC}"
    echo -e "${YELLOW}    ollama pull qwen2.5-coder:7b${NC}"
    echo ""
fi
