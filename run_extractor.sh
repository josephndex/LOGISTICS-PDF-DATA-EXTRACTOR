#!/bin/bash
# =============================================================================
# RITA PDF EXTRACTOR - Unified Launcher
# =============================================================================
# Handles setup, CLI mode, and Web UI mode in one script
# =============================================================================

# Colors for terminal output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# Get script directory
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR" || { echo -e "${RED}❌ Failed to change directory${NC}"; exit 1; }

# =============================================================================
# FUNCTIONS
# =============================================================================

show_header() {
    echo -e "${CYAN}"
    echo "══════════════════════════════════════════════════════════"
    echo "   🚗 RITA PDF EXTRACTOR"
    echo "   Vehicle Maintenance Invoice Processor"
    echo "══════════════════════════════════════════════════════════"
    echo -e "${NC}"
}

check_conda() {
    if ! command -v conda &> /dev/null; then
        echo -e "${RED}❌ Conda not found!${NC}"
        echo ""
        echo "Please install Miniconda or Anaconda first:"
        echo "  https://docs.conda.io/en/latest/miniconda.html"
        exit 1
    fi
}

activate_env() {
    # Try to source conda
    if [ -f "$(conda info --base)/etc/profile.d/conda.sh" ]; then
        source "$(conda info --base)/etc/profile.d/conda.sh"
    else
        echo -e "${RED}❌ Could not find conda.sh${NC}"
        exit 1
    fi

    conda activate RITA_PDF_EXTRACTOR 2>/dev/null
    return $?
}

check_env_exists() {
    conda env list | grep -q "RITA_PDF_EXTRACTOR"
    return $?
}

# =============================================================================
# SETUP FUNCTION
# =============================================================================

run_setup() {
    echo ""
    echo -e "${CYAN}===============================================================================${NC}"
    echo -e "${CYAN}                         RITA PDF EXTRACTOR - SETUP${NC}"
    echo -e "${CYAN}===============================================================================${NC}"
    echo ""

    echo "[1/5] Checking for existing environment..."
    if check_env_exists; then
        echo "      Environment 'RITA_PDF_EXTRACTOR' already exists."
        read -p "      Do you want to recreate it? (y/n): " RECREATE
        if [[ "$RECREATE" =~ ^[Yy]$ ]]; then
            echo "      Removing existing environment..."
            conda env remove -n RITA_PDF_EXTRACTOR -y
            SKIP_CREATE=false
        else
            echo "      Keeping existing environment. Updating packages only..."
            SKIP_CREATE=true
        fi
    else
        SKIP_CREATE=false
    fi

    if [[ "$SKIP_CREATE" != "true" ]]; then
        echo ""
        echo "[2/5] Creating conda environment with Python 3.10..."
        conda create -n RITA_PDF_EXTRACTOR python=3.10 -y
        if [ $? -ne 0 ]; then
            echo -e "${RED}❌ Failed to create conda environment!${NC}"
            exit 1
        fi
    fi

    echo ""
    echo "[3/5] Activating environment..."
    activate_env
    if [ $? -ne 0 ]; then
        echo -e "${RED}❌ Failed to activate environment!${NC}"
        exit 1
    fi

    echo ""
    echo "[4/6] Installing Poppler for PDF processing..."
    conda install -c conda-forge poppler -y

    echo ""
    echo "[5/6] Installing Python packages..."
    echo "      This may take several minutes on first install..."
    echo ""

    echo "      Installing PaddlePaddle (OCR engine)..."
    pip install paddlepaddle

    echo "      Installing PaddleOCR..."
    pip install paddleocr

    echo "      Installing PDF processing libraries..."
    pip install pdf2image Pillow pytesseract

    echo "      Installing data processing libraries..."
    pip install pandas numpy openpyxl

    echo "      Installing text matching libraries..."
    pip install rapidfuzz dateparser

    echo "      Installing Google Sheets libraries..."
    pip install gspread google-auth google-auth-oauthlib

    echo "      Installing database libraries (optional)..."
    pip install python-dotenv mysql-connector-python

    echo "      Installing Web UI (FastAPI)..."
    pip install fastapi uvicorn jinja2 python-multipart

    echo ""
    echo "[6/6] Verifying installation..."
    python -c "from paddleocr import PaddleOCR; print('      PaddleOCR: OK')" 2>/dev/null || echo -e "      ${RED}PaddleOCR: FAILED${NC}"
    python -c "import pandas; print('      pandas: OK')" 2>/dev/null || echo -e "      ${RED}pandas: FAILED${NC}"
    python -c "from pdf2image import convert_from_path; print('      pdf2image: OK')" 2>/dev/null || echo -e "      ${RED}pdf2image: FAILED${NC}"
    python -c "import fastapi; print('      fastapi: OK')" 2>/dev/null || echo -e "      ${RED}fastapi: FAILED${NC}"
    command -v pdftoppm &> /dev/null && echo -e "      ${GREEN}Poppler: OK${NC}" || echo -e "      ${RED}Poppler: FAILED${NC}"

    echo ""
    echo -e "${GREEN}===============================================================================${NC}"
    echo -e "${GREEN}                          SETUP COMPLETE!${NC}"
    echo -e "${GREEN}===============================================================================${NC}"
    echo ""
    echo "Run this script again and select option 1 or 2 to start RITA."
    echo ""
}

# =============================================================================
# CLI MODE
# =============================================================================

run_cli() {
    if [ ! -f "rita_interactive.py" ]; then
        echo -e "${RED}❌ rita_interactive.py not found!${NC}"
        exit 1
    fi

    echo ""
    echo -e "${GREEN}🚀 Starting CLI Mode...${NC}"
    echo ""
    python rita_interactive.py
}

# =============================================================================
# WEB MODE
# =============================================================================

run_web() {
    if [ ! -f "rita_web.py" ]; then
        echo -e "${RED}❌ rita_web.py not found!${NC}"
        exit 1
    fi

    # Check if fastapi and uvicorn are installed
    python -c "import fastapi; import uvicorn" 2>/dev/null
    if [ $? -ne 0 ]; then
        echo -e "${YELLOW}📦 FastAPI/Uvicorn not found. Installing...${NC}"
        pip install fastapi uvicorn jinja2 python-multipart
    fi

    echo ""
    echo -e "${GREEN}🌐 Starting Web Mode...${NC}"
    echo -e "${CYAN}   Open your browser to: http://localhost:8000${NC}"
    echo -e "${YELLOW}   Press Ctrl+C to stop the server${NC}"
    echo ""
    python -m uvicorn rita_web:app --host 0.0.0.0 --port 8000
}

# =============================================================================
# MAIN
# =============================================================================

show_header
check_conda

# Check if environment exists and is ready
if check_env_exists; then
    # Try to activate
    activate_env
    if [ $? -eq 0 ]; then
        ENV_READY=true
    else
        ENV_READY=false
    fi
else
    ENV_READY=false
fi

# Show menu
echo -e "${CYAN}Select an option:${NC}"
echo ""
if [ "$ENV_READY" = true ]; then
    echo "  [1] 🖥️  CLI Mode (Terminal interface)"
    echo "  [2] 🌐 Web Mode (Browser interface)"
    echo "  [3] 🔧 Setup/Reinstall (Install dependencies)"
    echo "  [0] ❌ Exit"
else
    echo -e "  ${YELLOW}[!] Environment not set up. Please run setup first.${NC}"
    echo ""
    echo "  [3] 🔧 Setup (Install dependencies)"
    echo "  [0] ❌ Exit"
fi
echo ""

read -p "Enter choice: " CHOICE

case $CHOICE in
    1)
        if [ "$ENV_READY" = true ]; then
            run_cli
        else
            echo -e "${RED}❌ Environment not ready. Run setup first (option 3).${NC}"
        fi
        ;;
    2)
        if [ "$ENV_READY" = true ]; then
            run_web
        else
            echo -e "${RED}❌ Environment not ready. Run setup first (option 3).${NC}"
        fi
        ;;
    3)
        run_setup
        ;;
    0)
        echo "Goodbye!"
        exit 0
        ;;
    *)
        echo -e "${RED}Invalid choice.${NC}"
        exit 1
        ;;
esac

# Capture exit code
EXIT_CODE=$?

if [ $EXIT_CODE -ne 0 ]; then
    echo ""
    echo -e "${RED}⚠ Program exited with errors (code: $EXIT_CODE)${NC}"
fi

exit $EXIT_CODE
