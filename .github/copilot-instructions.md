# RITA PDF Extractor - Copilot Instructions

## Project Overview

RITA PDF Extractor is an AI-powered invoice data extraction system for vehicle maintenance records. It uses PaddleOCR to read handwritten and typed invoices, provides an interactive CLI for human review, and syncs data to Google Sheets.

## Technology Stack

- **Language**: Python 3.10
- **Environment**: Conda (`RITA_PDF_EXTRACTOR`)
- **OCR Engine**: PaddleOCR v5 (handles handwritten + typed text)
- **Data Processing**: pandas, openpyxl
- **Cloud Sync**: gspread, google-auth (Google Sheets API)
- **PDF Processing**: pdf2image, Pillow

## Project Structure

```
rita_extractor.py       # Core OCR extraction logic, InvoiceData/LineItem classes
rita_interactive.py     # Interactive CLI, approval workflow, Google Sheets push
run_extractor.sh/bat    # Launcher scripts for Linux/Windows
google_sheets_config.json  # Google Sheets API configuration
PDFS/                   # Input PDFs organized by supplier folder
output/                 # Excel output files
```

## Key Classes and Functions

### rita_extractor.py
- `InvoiceData` - Dataclass holding invoice header info + line items
- `LineItem` - Dataclass for each invoice line (description, quantity, total)
- `RitaOCR` - Singleton OCR engine wrapper
- `extract_invoice(pdf_path, supplier)` - Main extraction function
- `extract_*_with_positions()` - Supplier-specific extractors

### rita_interactive.py
- `main_menu()` - Main CLI loop
- `process_folder_interactive(folder)` - Process PDFs one-by-one
- `display_invoice(invoice)` - Pretty-print extracted data
- `edit_invoice(invoice)` - Multi-field editing interface
- `save_invoice_to_output(invoice)` - Save to approved_data.xlsx
- `push_to_google_sheets()` - Sync to Google Sheets
- `load_processed_files()` / `save_processed_files()` - Track processed PDFs

## Data Flow

1. PDFs in `PDFS/{supplier}/` → OCR extraction
2. User reviews → Approves/Edits → `output/approved_data.xlsx`
3. Export to Excel → Merges, dedupes, sorts → `output/rita_approved_*.xlsx`
4. Push to Google Sheets → Uses latest `rita_approved_*.xlsx`

## Duplicate Detection Logic

Uses `INVOICE|DESCRIPTION` as unique key:
```python
df['_dup_key'] = df['INVOICE'].astype(str) + '|' + df['DESCRIPTION'].astype(str)
df = df.drop_duplicates(subset=['_dup_key'], keep='first')
```

## Data Columns

| Column | Description |
|--------|-------------|
| INVOICE | Invoice number |
| DATE | Invoice date (DD/MM/YYYY) |
| VEHICLE | Vehicle registration (e.g., KCZ 223P) |
| DESCRIPTION | Service/part description |
| QUANTITY | Number of items |
| UNIT_COST | TOTAL ÷ QUANTITY |
| TOTAL | Line item total cost |
| SUPPLIER | Garage/supplier name |
| OWNER | Vehicle owner (default: FIRESIDE) |

## Coding Conventions

- Use `Path` from pathlib for all file paths (portable across OS)
- Use relative paths from `Path(__file__).parent` for portability
- Color output using `Colors` class (auto-detects terminal support)
- Error handling with `print_error()`, `print_warning()`, `print_success()`, `print_info()`
- Excel files preferred over CSV for output
- All user input should handle KeyboardInterrupt gracefully

## Security

- Reset password: `ITDONTMATTER` (stored as SHA-256 hash)
- Google credentials stored in `google_credentials.json` (not committed to git)

## Common Tasks

### Adding a New Supplier
1. Create folder in `PDFS/{supplier_name}/`
2. Add extraction function in `rita_extractor.py`
3. Update `extract_invoice()` to detect the new supplier

### Modifying Data Columns
1. Update `InvoiceData.to_rows()` in rita_extractor.py
2. Update column lists in rita_interactive.py (save, export, push functions)

### Testing OCR
```python
from rita_extractor import extract_invoice
invoice = extract_invoice("PDFS/karimi/test.pdf", "karimi")
print(invoice)
```

## Environment Setup

```bash
conda activate RITA_PDF_EXTRACTOR
pip install pandas openpyxl pdf2image pillow paddlepaddle paddleocr gspread google-auth
```

## Running the Application

```bash
conda activate RITA_PDF_EXTRACTOR
python rita_interactive.py
```

Or use launcher scripts: `./run_extractor.sh` (Linux) or `run_extractor.bat` (Windows)
