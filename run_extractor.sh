#!/bin/bash
# =============================================================================
# RITA PDF EXTRACTOR - Interactive Mode Launcher
# =============================================================================

# Colors for terminal output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

echo -e "${CYAN}"
echo "══════════════════════════════════════════════════════════"
echo "   🚗 RITA PDF EXTRACTOR - Interactive Mode"
echo "   Vehicle Maintenance Invoice Processor"
echo "══════════════════════════════════════════════════════════"
echo -e "${NC}"

# Get script directory
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR" || { echo -e "${RED}❌ Failed to change directory${NC}"; exit 1; }

# Check if conda is available
if ! command -v conda &> /dev/null; then
    echo -e "${RED}❌ Conda not found. Please install Anaconda or Miniconda.${NC}"
    exit 1
fi

# Activate conda environment
echo -e "${YELLOW}🔄 Activating conda environment...${NC}"

# Try to source conda
if [ -f "$(conda info --base)/etc/profile.d/conda.sh" ]; then
    source "$(conda info --base)/etc/profile.d/conda.sh"
else
    echo -e "${RED}❌ Could not find conda.sh${NC}"
    exit 1
fi

conda activate RITA_PDF_EXTRACTOR

if [ $? -ne 0 ]; then
    echo -e "${RED}❌ Failed to activate conda environment RITA_PDF_EXTRACTOR${NC}"
    echo -e "${YELLOW}Please create it with:${NC}"
    echo "  conda create -n RITA_PDF_EXTRACTOR python=3.10"
    echo "  conda activate RITA_PDF_EXTRACTOR"
    echo "  pip install paddleocr paddlepaddle pandas openpyxl pdf2image pillow rapidfuzz dateparser"
    exit 1
fi

echo -e "${GREEN}✅ Environment activated${NC}"
echo ""

# Check if the interactive script exists
if [ ! -f "rita_interactive.py" ]; then
    echo -e "${RED}❌ rita_interactive.py not found!${NC}"
    conda deactivate
    exit 1
fi

# Run the interactive extractor
echo -e "${GREEN}🚀 Starting Interactive Mode...${NC}"
echo ""
python rita_interactive.py

# Capture exit code
EXIT_CODE=$?

# Deactivate when done
conda deactivate

if [ $EXIT_CODE -ne 0 ]; then
    echo ""
    echo -e "${RED}⚠ Program exited with errors (code: $EXIT_CODE)${NC}"
fi

exit $EXIT_CODE
