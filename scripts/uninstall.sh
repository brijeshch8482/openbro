#!/usr/bin/env bash
# OpenBro Uninstaller for Linux/macOS

CYAN='\033[0;36m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
DIM='\033[2m'
NC='\033[0m'

echo ""
echo -e "${YELLOW}OpenBro Uninstaller${NC}"
echo -e "${YELLOW}===================${NC}"
echo ""

# Confirm
read -p "Are you sure you want to uninstall OpenBro? (y/N) " confirm
if [[ "$confirm" != "y" && "$confirm" != "Y" ]]; then
    echo "Cancelled."
    exit 0
fi

# Uninstall pip package
echo -e "${GREEN}[1/3] Removing OpenBro package...${NC}"
PYTHON=""
for cmd in python3 python; do
    if command -v "$cmd" &> /dev/null; then
        PYTHON="$cmd"
        break
    fi
done

if [ -n "$PYTHON" ]; then
    "$PYTHON" -m pip uninstall openbro -y 2>&1
    echo -e "${GREEN}  Package removed.${NC}"
else
    echo -e "${YELLOW}  Python not found, skipping pip uninstall.${NC}"
fi

# Ask about data
CONFIG_DIR="$HOME/.openbro"
echo ""
echo -e "${GREEN}[2/3] Data cleanup${NC}"

if [ -d "$CONFIG_DIR" ]; then
    SIZE=$(du -sh "$CONFIG_DIR" 2>/dev/null | cut -f1)
    echo -e "${DIM}  Config directory: $CONFIG_DIR ($SIZE)${NC}"

    read -p "  Delete config, history, and memory? (y/N) " delete_data
    if [[ "$delete_data" == "y" || "$delete_data" == "Y" ]]; then
        rm -rf "$CONFIG_DIR"
        echo -e "${GREEN}  Data deleted.${NC}"
    else
        echo -e "${DIM}  Data kept at: $CONFIG_DIR${NC}"
    fi
else
    echo -e "${DIM}  No data directory found.${NC}"
fi

# Custom storage note
echo ""
echo -e "${GREEN}[3/3] Custom storage check${NC}"
echo -e "${DIM}  If you set a custom storage path during setup,${NC}"
echo -e "${DIM}  that data is NOT automatically deleted.${NC}"
echo -e "${DIM}  Check your config for the path and delete manually if needed.${NC}"

echo ""
echo -e "${GREEN}============================================${NC}"
echo -e "${GREEN}  OpenBro uninstalled successfully!${NC}"
echo -e "${GREEN}============================================${NC}"
echo ""
echo -e "${CYAN}  Sad to see you go, bhai. Come back anytime!${NC}"
echo ""
