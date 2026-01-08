#!/bin/bash
# =============================================================================
# RITA PDF EXTRACTOR - Linux/Mac Launcher
# =============================================================================

# Colors for terminal output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

echo -e "${CYAN}"
echo "=============================================="
echo "   🚗 RITA PDF EXTRACTOR"
echo "   Vehicle Maintenance Invoice Processor"
echo "=============================================="
echo -e "${NC}"

# Get script directory
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

# Activate conda environment
echo -e "${YELLOW}🔄 Activating conda environment...${NC}"
source "$(conda info --base)/etc/profile.d/conda.sh"
conda activate RITA_PDF_EXTRACTOR

if [ $? -ne 0 ]; then
    echo -e "${RED}❌ Failed to activate conda environment RITA_PDF_EXTRACTOR${NC}"
    echo "Please run: conda create -n RITA_PDF_EXTRACTOR python=3.10"
    exit 1
fi

echo -e "${GREEN}✅ Environment activated${NC}"
echo ""

# Run the Python interactive menu
python rita_extractor.py --menu

# Deactivate when done
conda deactivate
