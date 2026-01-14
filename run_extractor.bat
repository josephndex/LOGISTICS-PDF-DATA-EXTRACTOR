@echo off
REM =============================================================================
REM RITA PDF EXTRACTOR - Unified Launcher
REM =============================================================================
REM Handles setup, CLI mode, and Web UI mode in one script
REM =============================================================================

setlocal enabledelayedexpansion

REM Get script directory
cd /d "%~dp0"
if errorlevel 1 (
    echo ERROR: Failed to change directory
    pause
    exit /b 1
)

:show_header
echo.
echo ==========================================================
echo    RITA PDF EXTRACTOR
echo    Vehicle Maintenance Invoice Processor
echo ==========================================================
echo.

REM Check if conda is available
where conda >nul 2>nul
if errorlevel 1 (
    echo ERROR: Conda not found!
    echo.
    echo Please install Miniconda or Anaconda first:
    echo   https://docs.conda.io/en/latest/miniconda.html
    pause
    exit /b 1
)

REM Check if environment exists
set ENV_READY=0
call conda env list | findstr /C:"RITA_PDF_EXTRACTOR" >nul 2>nul
if !ERRORLEVEL! equ 0 (
    call conda activate RITA_PDF_EXTRACTOR >nul 2>nul
    if not errorlevel 1 (
        set ENV_READY=1
    )
)

REM Show menu
echo Select an option:
echo.
if "!ENV_READY!"=="1" (
    echo   [1] CLI Mode - Terminal interface
    echo   [2] Web Mode - Browser interface
    echo   [3] Setup/Reinstall - Install dependencies
    echo   [0] Exit
) else (
    echo   [NOTE] Environment not set up. Please run setup first.
    echo.
    echo   [3] Setup - Install dependencies
    echo   [0] Exit
)
echo.

set /p CHOICE="Enter choice: "

if "!CHOICE!"=="1" (
    if "!ENV_READY!"=="1" (
        goto :run_cli
    ) else (
        echo ERROR: Environment not ready. Run setup first - option 3
        pause
        exit /b 1
    )
)
if "!CHOICE!"=="2" (
    if "!ENV_READY!"=="1" (
        goto :run_web
    ) else (
        echo ERROR: Environment not ready. Run setup first - option 3
        pause
        exit /b 1
    )
)
if "!CHOICE!"=="3" goto :run_setup
if "!CHOICE!"=="0" (
    echo Goodbye!
    exit /b 0
)

echo Invalid choice.
pause
exit /b 1

REM =============================================================================
REM SETUP
REM =============================================================================
:run_setup
echo.
echo ===============================================================================
echo                          RITA PDF EXTRACTOR - SETUP
echo ===============================================================================
echo.

echo [1/5] Checking for existing environment...
set SKIP_CREATE=0
call conda env list | findstr /C:"RITA_PDF_EXTRACTOR" >nul 2>nul
if !ERRORLEVEL! equ 0 (
    echo       Environment RITA_PDF_EXTRACTOR already exists.
    set /p RECREATE="      Do you want to recreate it? [y/n]: "
    if /i "!RECREATE!"=="y" (
        echo       Removing existing environment...
        call conda env remove -n RITA_PDF_EXTRACTOR -y
        set SKIP_CREATE=0
    ) else (
        echo       Keeping existing environment. Updating packages only...
        set SKIP_CREATE=1
    )
)

if "!SKIP_CREATE!"=="0" (
    echo.
    echo [2/5] Creating conda environment with Python 3.10...
    call conda create -n RITA_PDF_EXTRACTOR python=3.10 -y
    if errorlevel 1 (
        echo ERROR: Failed to create conda environment!
        pause
        exit /b 1
    )
)

echo.
echo [3/5] Activating environment...
call conda activate RITA_PDF_EXTRACTOR
if errorlevel 1 (
    echo ERROR: Failed to activate environment!
    pause
    exit /b 1
)

echo.
echo [4/6] Installing Poppler for PDF processing...
call conda install -c conda-forge poppler -y

echo.
echo [5/6] Installing Python packages...
echo       This may take several minutes on first install...
echo.

echo       Installing PaddlePaddle - OCR engine...
pip install paddlepaddle

echo       Installing PaddleOCR...
pip install paddleocr

echo       Installing PDF processing libraries...
pip install pdf2image Pillow pytesseract

echo       Installing data processing libraries...
pip install pandas numpy openpyxl

echo       Installing text matching libraries...
pip install rapidfuzz dateparser

echo       Installing Google Sheets libraries...
pip install gspread google-auth google-auth-oauthlib

echo       Installing database libraries (optional)...
pip install python-dotenv mysql-connector-python

echo       Installing Web UI (FastAPI)...
pip install fastapi uvicorn jinja2 python-multipart

echo.
echo [6/6] Verifying installation...
python -c "from paddleocr import PaddleOCR; print('      PaddleOCR: OK')"
python -c "import pandas; print('      pandas: OK')"
python -c "from pdf2image import convert_from_path; print('      pdf2image: OK')"
python -c "import fastapi; print('      fastapi: OK')"
where pdftoppm >nul 2>nul && echo       Poppler: OK || echo       Poppler: NOT FOUND

echo.
echo ===============================================================================
echo                           SETUP COMPLETE
echo ===============================================================================
echo.
echo Run this script again and select option 1 or 2 to start RITA.
echo.
pause
exit /b 0

REM =============================================================================
REM CLI MODE
REM =============================================================================
:run_cli
if not exist "rita_interactive.py" (
    echo ERROR: rita_interactive.py not found!
    pause
    exit /b 1
)

echo.
echo Starting CLI Mode...
echo.
python rita_interactive.py
set EXIT_CODE=%errorlevel%
goto :end

REM =============================================================================
REM WEB MODE
REM =============================================================================
:run_web
if not exist "rita_web.py" (
    echo ERROR: rita_web.py not found!
    pause
    exit /b 1
)

REM Check if fastapi and uvicorn are installed
python -c "import fastapi; import uvicorn" 2>nul
if errorlevel 1 (
    echo FastAPI/Uvicorn not found. Installing...
    pip install fastapi uvicorn jinja2 python-multipart
)

echo.
echo Starting Web Mode...
echo    Open your browser to: http://localhost:8000
echo    Press Ctrl+C to stop the server
echo.
python -m uvicorn rita_web:app --host 0.0.0.0 --port 8000
set EXIT_CODE=%errorlevel%
goto :end

:end
if not "!EXIT_CODE!"=="0" (
    echo.
    echo WARNING: Program exited with errors - code: !EXIT_CODE!
)
pause
exit /b !EXIT_CODE!
