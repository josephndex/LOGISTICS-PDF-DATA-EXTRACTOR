@echo off
REM =============================================================================
REM RITA PDF EXTRACTOR - Interactive Mode Launcher
REM =============================================================================

setlocal enabledelayedexpansion

echo.
echo ==========================================================
echo    RITA PDF EXTRACTOR - Interactive Mode
echo    Vehicle Maintenance Invoice Processor
echo ==========================================================
echo.

REM Get script directory
cd /d "%~dp0"
if errorlevel 1 (
    echo ERROR: Failed to change directory
    pause
    exit /b 1
)

REM Check if conda is available
where conda >nul 2>nul
if errorlevel 1 (
    echo ERROR: Conda not found. Please install Anaconda or Miniconda.
    pause
    exit /b 1
)

REM Activate conda environment
echo Activating conda environment...
call conda activate RITA_PDF_EXTRACTOR

if errorlevel 1 (
    echo.
    echo ERROR: Failed to activate conda environment RITA_PDF_EXTRACTOR
    echo.
    echo Please create it with:
    echo   conda create -n RITA_PDF_EXTRACTOR python=3.10
    echo   conda activate RITA_PDF_EXTRACTOR
    echo   pip install paddleocr paddlepaddle pandas openpyxl pdf2image pillow rapidfuzz dateparser
    pause
    exit /b 1
)

echo Environment activated
echo.

REM Check if the interactive script exists
if not exist "rita_interactive.py" (
    echo ERROR: rita_interactive.py not found!
    call conda deactivate
    pause
    exit /b 1
)

REM Run the interactive extractor
echo Starting Interactive Mode...
echo.
python rita_interactive.py

REM Capture exit code
set EXIT_CODE=%errorlevel%

REM Deactivate when done
call conda deactivate

if not "%EXIT_CODE%"=="0" (
    echo.
    echo WARNING: Program exited with errors (code: %EXIT_CODE%)
)

pause
exit /b %EXIT_CODE%
