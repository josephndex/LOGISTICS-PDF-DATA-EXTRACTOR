@echo off
REM =============================================================================
REM RITA PDF EXTRACTOR - Windows Launcher
REM =============================================================================

echo.
echo ==============================================
echo    RITA PDF EXTRACTOR
echo    Vehicle Maintenance Invoice Processor
echo ==============================================
echo.

REM Get script directory
cd /d "%~dp0"

REM Activate conda environment
echo Activating conda environment...
call conda activate RITA_PDF_EXTRACTOR

if errorlevel 1 (
    echo.
    echo ERROR: Failed to activate conda environment RITA_PDF_EXTRACTOR
    echo Please run: conda create -n RITA_PDF_EXTRACTOR python=3.10
    pause
    exit /b 1
)

echo Environment activated
echo.

REM Run the Python interactive menu
python rita_extractor.py --menu

REM Deactivate when done
call conda deactivate

pause
