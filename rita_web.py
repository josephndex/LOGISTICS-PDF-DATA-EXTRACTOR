#!/usr/bin/env python3
"""
=============================================================================
RITA PDF EXTRACTOR - FastAPI Web Interface
=============================================================================
Web-based UI for processing vehicle maintenance invoices with side-by-side
image preview and data editing capabilities.
=============================================================================

Run with: uvicorn rita_web:app --reload --host 0.0.0.0 --port 8000
"""

import os
import sys
import json
import base64
import hashlib
import warnings
import threading
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional
from io import BytesIO

# Suppress warnings
warnings.filterwarnings('ignore')
os.environ['DISABLE_MODEL_SOURCE_CHECK'] = 'True'

from fastapi import FastAPI, Request, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import pandas as pd
from PIL import Image

# Import from the main extractor
try:
    from rita_extractor import (
        RitaOCR, InvoiceData, LineItem,
        pdf_to_images, extract_invoice, standardize_date,
        PDF_ROOT, OUTPUT_DIR, SUPPLIER_MAPPING, OWNER
    )
except ImportError as e:
    print(f"❌ Could not import from rita_extractor.py: {e}")
    sys.exit(1)

# =============================================================================
# RESET PASSWORDS (hashed for security - passwords not stored in code)
# =============================================================================
RESET_PASSWORDS = {
    'partial': '75458e160eebce5cc6737cdcf7555b92fe146dd825cdb318dc1d44dd4c60f99e',  # Partial reset
    'full': 'a4a97c2dc6167c15348e65c16ebaedc334f6e6c9c2f8be51868ec5f1b2760cb5',     # Full reset
}

# =============================================================================
# CONFIGURATION
# =============================================================================

BASE_DIR = Path(__file__).parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"
TRACKING_FILE = OUTPUT_DIR / "processed_files.json"
GSHEETS_CONFIG_FILE = BASE_DIR / "google_sheets_config.json"
GSHEETS_CREDENTIALS_FILE = BASE_DIR / "google_credentials.json"

# Ensure directories exist
TEMPLATES_DIR.mkdir(exist_ok=True)
STATIC_DIR.mkdir(exist_ok=True)
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# FastAPI app
app = FastAPI(title="RITA PDF Extractor", description="AI-Powered Invoice Extraction")

# Mount static files
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# Jinja2 templates
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# =============================================================================
# OCR ENGINE (Singleton with Background Preloading)
# =============================================================================

_ocr_instance: Optional[RitaOCR] = None
_ocr_loading: bool = False
_ocr_ready: bool = False

def preload_ocr_engine():
    """Preload OCR engine in background thread for faster processing."""
    global _ocr_instance, _ocr_loading, _ocr_ready
    if _ocr_instance is None and not _ocr_loading:
        _ocr_loading = True
        print("🔄 Preloading OCR engine in background...")
        try:
            _ocr_instance = RitaOCR()
            _ocr_ready = True
            print("✅ OCR engine ready!")
        except Exception as e:
            print(f"❌ OCR preload failed: {e}")
        finally:
            _ocr_loading = False

def get_ocr_engine() -> RitaOCR:
    """Get or create OCR engine (singleton)."""
    global _ocr_instance, _ocr_ready
    if _ocr_instance is None:
        print("⏳ Loading OCR engine (first time)...")
        _ocr_instance = RitaOCR()
        _ocr_ready = True
    return _ocr_instance

def is_ocr_ready() -> bool:
    """Check if OCR engine is ready."""
    return _ocr_ready

# Start preloading OCR in background when app starts
@app.on_event("startup")
async def startup_event():
    """Preload OCR engine on startup for faster first extraction."""
    thread = threading.Thread(target=preload_ocr_engine, daemon=True)
    thread.start()

# =============================================================================
# IN-MEMORY SESSION STORAGE
# =============================================================================

# Simple in-memory storage for current processing session
session_data: Dict = {
    'current_invoice': None,
    'current_image_b64': None,
    'current_pdf_path': None,
    'selected_folder': None
}

# =============================================================================
# TRACKING FILES
# =============================================================================

def load_processed_files() -> Dict[str, List[str]]:
    """Load the list of processed files."""
    if TRACKING_FILE.exists():
        try:
            with open(TRACKING_FILE, 'r') as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_processed_files(processed: Dict[str, List[str]]):
    """Save the processed files dictionary."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(TRACKING_FILE, 'w') as f:
        json.dump(processed, f, indent=2)

def save_processed_file(folder: str, filename: str):
    """Mark a file as processed."""
    processed = load_processed_files()
    if folder not in processed:
        processed[folder] = []
    if filename not in processed[folder]:
        processed[folder].append(filename)
    save_processed_files(processed)

def is_file_processed(folder: str, filename: str) -> bool:
    """Check if a file has been processed."""
    processed = load_processed_files()
    return filename in processed.get(folder, [])

def get_unprocessed_pdfs(folder: str) -> List[Path]:
    """Get list of unprocessed PDFs."""
    folder_path = PDF_ROOT / folder
    if not folder_path.exists():
        return []
    all_pdfs = sorted(folder_path.glob("*.pdf"))
    return [pdf for pdf in all_pdfs if not is_file_processed(folder, pdf.name)]

def get_all_pdfs(folder: str) -> List[Path]:
    """Get all PDFs in folder."""
    folder_path = PDF_ROOT / folder
    if not folder_path.exists():
        return []
    return sorted(folder_path.glob("*.pdf"))

# =============================================================================
# DATA MANAGEMENT
# =============================================================================

def get_approved_data_file() -> Path:
    """Get path to approved data file."""
    return OUTPUT_DIR / "approved_data.xlsx"

def load_approved_data() -> pd.DataFrame:
    """Load existing approved data."""
    excel_path = get_approved_data_file()
    if excel_path.exists():
        try:
            return pd.read_excel(excel_path)
        except Exception:
            return pd.DataFrame()
    return pd.DataFrame()

def save_invoice_to_output(invoice: InvoiceData) -> bool:
    """Save approved invoice to output file."""
    try:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        existing_df = load_approved_data()
        new_rows = invoice.to_rows()
        new_df = pd.DataFrame(new_rows)
        
        if len(existing_df) > 0:
            combined_df = pd.concat([existing_df, new_df], ignore_index=True)
        else:
            combined_df = new_df
        
        columns = ['INVOICE', 'DATE', 'VEHICLE', 'DESCRIPTION', 'QUANTITY', 'UNIT_COST', 'TOTAL', 'SUPPLIER', 'OWNER']
        for col in columns:
            if col not in combined_df.columns:
                combined_df[col] = ''
        combined_df = combined_df[columns]
        combined_df.to_excel(get_approved_data_file(), index=False)
        return True
    except Exception as e:
        print(f"Failed to save: {e}")
        return False

# =============================================================================
# HELPER FUNCTIONS
# =============================================================================

def get_folder_stats() -> List[Dict]:
    """Get statistics for all folders."""
    folders = []
    if PDF_ROOT.exists():
        for folder in sorted(PDF_ROOT.iterdir()):
            if folder.is_dir() and folder.name != 'ground_truth':
                all_pdfs = list(folder.glob("*.pdf"))
                if all_pdfs:
                    unprocessed = get_unprocessed_pdfs(folder.name)
                    folders.append({
                        'name': folder.name,
                        'supplier': SUPPLIER_MAPPING.get(folder.name, folder.name.upper()),
                        'total': len(all_pdfs),
                        'pending': len(unprocessed),
                        'processed': len(all_pdfs) - len(unprocessed),
                        'progress': int(((len(all_pdfs) - len(unprocessed)) / len(all_pdfs)) * 100) if all_pdfs else 0
                    })
    return folders

def image_to_base64(image: Image.Image) -> str:
    """Convert PIL Image to base64 string."""
    buffer = BytesIO()
    image.save(buffer, format='PNG')
    return base64.b64encode(buffer.getvalue()).decode('utf-8')

# =============================================================================
# ROUTES
# =============================================================================

@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    """Home page with dashboard overview."""
    folders = get_folder_stats()
    df = load_approved_data()
    
    total_pdfs = sum(f['total'] for f in folders)
    total_pending = sum(f['pending'] for f in folders)
    total_processed = total_pdfs - total_pending
    total_amount = df['TOTAL'].sum() if len(df) > 0 else 0
    
    return templates.TemplateResponse("index.html", {
        "request": request,
        "page": "home",
        "folders": folders,
        "total_pdfs": total_pdfs,
        "total_pending": total_pending,
        "total_processed": total_processed,
        "total_records": len(df),
        "total_amount": f"{total_amount:,.2f}"
    })


@app.get("/process", response_class=HTMLResponse)
async def process_page(request: Request, folder: Optional[str] = None):
    """PDF processing page."""
    folders = get_folder_stats()
    selected_folder = folder or (folders[0]['name'] if folders else None)
    
    unprocessed = get_unprocessed_pdfs(selected_folder) if selected_folder else []
    all_pdfs = get_all_pdfs(selected_folder) if selected_folder else []
    
    # Check if we have current session data
    current_invoice = session_data.get('current_invoice')
    current_image_b64 = session_data.get('current_image_b64')
    
    return templates.TemplateResponse("process.html", {
        "request": request,
        "page": "process",
        "folders": folders,
        "selected_folder": selected_folder,
        "unprocessed_pdfs": [pdf.name for pdf in unprocessed],
        "total_pdfs": len(all_pdfs),
        "pending_count": len(unprocessed),
        "current_invoice": current_invoice,
        "current_image_b64": current_image_b64,
        "stats": get_dashboard_stats()
    })


@app.post("/process/extract")
async def extract_pdf(request: Request, folder: str = Form(...), pdf_name: str = Form(...)):
    """Extract data from a PDF file."""
    pdf_path = PDF_ROOT / folder / pdf_name
    
    if not pdf_path.exists():
        return JSONResponse({"error": f"PDF not found: {pdf_name}"}, status_code=404)
    
    try:
        ocr = get_ocr_engine()
        images = pdf_to_images(str(pdf_path))
        
        if not images:
            return JSONResponse({"error": "Failed to convert PDF to images"}, status_code=500)
        
        # Find first non-blank page and extract
        invoice = None
        image = None
        for i, img in enumerate(images):
            try:
                invoice = extract_invoice(img, folder, pdf_name, ocr)
                image = img
                break
            except Exception as e:
                if i == len(images) - 1:
                    return JSONResponse({"error": f"Failed to extract: {str(e)}"}, status_code=500)
        
        if invoice and image:
            # Store in session
            session_data['current_invoice'] = {
                'invoice_number': invoice.invoice_number or '',
                'date': invoice.date or '',
                'vehicle': invoice.vehicle or '',
                'supplier': invoice.supplier,
                'owner': invoice.owner,
                'line_items': [
                    {
                        'description': item.description,
                        'quantity': item.quantity,
                        'total': item.total,
                        'cost': item.cost
                    }
                    for item in invoice.line_items
                ]
            }
            session_data['current_image_b64'] = image_to_base64(image)
            session_data['current_pdf_path'] = str(pdf_path)
            session_data['selected_folder'] = folder
            
        return RedirectResponse(f"/process?folder={folder}", status_code=303)
        
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/process/approve")
async def approve_invoice(
    request: Request,
    invoice_number: str = Form(...),
    date: str = Form(...),
    vehicle: str = Form(...),
    descriptions: List[str] = Form(default=[]),
    quantities: List[str] = Form(default=[]),
    totals: List[str] = Form(default=[])
):
    """Approve and save the current invoice."""
    if not session_data.get('current_invoice'):
        return RedirectResponse("/process", status_code=303)
    
    folder = session_data.get('selected_folder', '')
    pdf_path = session_data.get('current_pdf_path', '')
    
    # Build invoice object
    invoice = InvoiceData(
        invoice_number=invoice_number,
        date=standardize_date(date),
        vehicle=vehicle.upper() if vehicle else '',
        supplier=session_data['current_invoice'].get('supplier', ''),
        owner=session_data['current_invoice'].get('owner', OWNER),
        line_items=[]
    )
    
    # Parse line items
    for i in range(len(descriptions)):
        desc = descriptions[i] if i < len(descriptions) else ''
        qty = float(quantities[i]) if i < len(quantities) and quantities[i] else 1.0
        total = float(totals[i]) if i < len(totals) and totals[i] else 0.0
        
        if desc and total > 0:
            cost = total / qty if qty > 0 else total
            invoice.line_items.append(LineItem(desc, qty, total, cost))
    
    # Save invoice
    if save_invoice_to_output(invoice):
        pdf_name = Path(pdf_path).name if pdf_path else ''
        if folder and pdf_name:
            save_processed_file(folder, pdf_name)
    
    # Clear session
    session_data['current_invoice'] = None
    session_data['current_image_b64'] = None
    session_data['current_pdf_path'] = None
    
    return RedirectResponse(f"/process?folder={folder}", status_code=303)


@app.post("/process/skip")
async def skip_invoice():
    """Skip the current invoice."""
    folder = session_data.get('selected_folder', '')
    
    # Clear session
    session_data['current_invoice'] = None
    session_data['current_image_b64'] = None
    session_data['current_pdf_path'] = None
    
    return RedirectResponse(f"/process?folder={folder}", status_code=303)


@app.get("/data", response_class=HTMLResponse)
async def view_data(
    request: Request,
    supplier: Optional[str] = None,
    vehicle: Optional[str] = None,
    search: Optional[str] = None
):
    """View approved data page."""
    df = load_approved_data()
    
    if len(df) == 0:
        return templates.TemplateResponse("data.html", {
            "request": request,
            "page": "data",
            "has_data": False,
            "stats": get_dashboard_stats()
        })
    
    # Get filter options
    suppliers = sorted(df['SUPPLIER'].unique().tolist())
    vehicles = sorted(df['VEHICLE'].dropna().unique().tolist())
    
    # Apply filters
    filtered_df = df.copy()
    if supplier:
        filtered_df = filtered_df[filtered_df['SUPPLIER'] == supplier]
    if vehicle:
        filtered_df = filtered_df[filtered_df['VEHICLE'] == vehicle]
    if search:
        filtered_df = filtered_df[filtered_df['DESCRIPTION'].str.contains(search, case=False, na=False)]
    
    # Summary by supplier
    summary = df.groupby('SUPPLIER').agg({
        'INVOICE': 'nunique',
        'TOTAL': 'sum'
    }).reset_index()
    summary.columns = ['Supplier', 'Invoices', 'Total Amount']
    
    return templates.TemplateResponse("data.html", {
        "request": request,
        "page": "data",
        "has_data": True,
        "data": filtered_df.to_dict('records'),
        "columns": filtered_df.columns.tolist(),
        "total_records": len(df),
        "filtered_count": len(filtered_df),
        "unique_invoices": df['INVOICE'].nunique(),
        "unique_vehicles": df['VEHICLE'].nunique(),
        "total_amount": f"{df['TOTAL'].sum():,.2f}",
        "suppliers": suppliers,
        "vehicles": vehicles,
        "selected_supplier": supplier or '',
        "selected_vehicle": vehicle or '',
        "search_query": search or '',
        "summary": summary.to_dict('records'),
        "stats": get_dashboard_stats()
    })


@app.get("/export/excel")
async def export_excel():
    """Export data to Excel."""
    df = load_approved_data()
    if len(df) == 0:
        return JSONResponse({"error": "No data to export"}, status_code=400)
    
    filename = f"rita_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
    filepath = OUTPUT_DIR / filename
    df.to_excel(filepath, index=False)
    
    return FileResponse(
        filepath,
        filename=filename,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )


@app.get("/export/csv")
async def export_csv():
    """Export data to CSV."""
    df = load_approved_data()
    if len(df) == 0:
        return JSONResponse({"error": "No data to export"}, status_code=400)
    
    filename = f"rita_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    filepath = OUTPUT_DIR / filename
    df.to_csv(filepath, index=False)
    
    return FileResponse(
        filepath,
        filename=filename,
        media_type="text/csv"
    )


@app.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request, message: Optional[str] = None, error: Optional[str] = None):
    """Settings page."""
    folders = get_folder_stats()
    processed = load_processed_files()
    total_processed_files = sum(len(files) for files in processed.values())
    
    # Check cloud sync status
    gsheets_configured = GSHEETS_CONFIG_FILE.exists() and GSHEETS_CREDENTIALS_FILE.exists()
    db_configured = (BASE_DIR / ".env").exists()
    
    # Count files in output directory
    output_files = list(OUTPUT_DIR.glob("*.xlsx")) + list(OUTPUT_DIR.glob("*.csv"))
    
    return templates.TemplateResponse("settings.html", {
        "request": request,
        "page": "settings",
        "pdf_root": str(PDF_ROOT),
        "output_dir": str(OUTPUT_DIR),
        "folders": folders,
        "processed_files": processed,
        "total_processed_files": total_processed_files,
        "stats": get_dashboard_stats(),
        "gsheets_configured": gsheets_configured,
        "db_configured": db_configured,
        "output_file_count": len(output_files),
        "ocr_ready": is_ocr_ready(),
        "message": message,
        "error": error
    })


def verify_password(password: str, password_type: str) -> bool:
    """Verify password against stored hash."""
    entered_hash = hashlib.sha256(password.encode()).hexdigest()
    return entered_hash == RESET_PASSWORDS.get(password_type, '')


@app.post("/settings/reset")
async def reset_folder(folder: str = Form(...), password: str = Form(...)):
    """Reset processing status for a folder."""
    if not verify_password(password, 'partial'):
        return RedirectResponse("/settings?error=Invalid+password", status_code=303)
    
    processed = load_processed_files()
    if folder in processed:
        processed[folder] = []
        save_processed_files(processed)
    return RedirectResponse(f"/settings?message=Folder+{folder}+reset+successfully", status_code=303)


@app.post("/settings/reset-processed")
async def reset_processed(password: str = Form(...)):
    """Reset only processed_files.json."""
    if not verify_password(password, 'partial'):
        return RedirectResponse("/settings?error=Invalid+password", status_code=303)
    
    # Only delete the tracking file
    if TRACKING_FILE.exists():
        TRACKING_FILE.unlink()
    save_processed_files({})
    return RedirectResponse("/settings?message=Processed+files+tracking+cleared!", status_code=303)


@app.post("/settings/reset-all")
async def reset_all(password: str = Form(...)):
    """Full reset - clears everything."""
    if not verify_password(password, 'full'):
        return RedirectResponse("/settings?error=Invalid+password", status_code=303)
    
    # Clear processed files tracking
    if TRACKING_FILE.exists():
        TRACKING_FILE.unlink()
    save_processed_files({})
    
    # Clear approved data
    approved_file = OUTPUT_DIR / "approved_data.xlsx"
    if approved_file.exists():
        approved_file.unlink()
    
    # Clear master data
    master_file = OUTPUT_DIR / "rita_master_data.xlsx"
    if master_file.exists():
        master_file.unlink()
    
    # Clear all exported files
    for f in OUTPUT_DIR.glob("rita_export_*.xlsx"):
        f.unlink()
    for f in OUTPUT_DIR.glob("rita_export_*.csv"):
        f.unlink()
    
    return RedirectResponse("/settings?message=Full+reset+complete!+All+data+cleared.", status_code=303)


# =============================================================================
# EXPORT FUNCTIONALITY (Full Excel Export with Master File)
# =============================================================================

@app.get("/export/master")
async def export_master_excel():
    """Export to master Excel file (like CLI option 4) - merges and dedupes."""
    df_approved = load_approved_data()
    if len(df_approved) == 0:
        return JSONResponse({"error": "No approved data to export"}, status_code=400)
    
    # Load existing master file
    master_file = OUTPUT_DIR / "rita_master_data.xlsx"
    
    if master_file.exists():
        try:
            df_existing = pd.read_excel(master_file)
        except Exception:
            df_existing = pd.DataFrame()
    else:
        df_existing = pd.DataFrame()
    
    # Combine both dataframes
    if len(df_existing) > 0:
        combined_df = pd.concat([df_existing, df_approved], ignore_index=True)
    else:
        combined_df = df_approved.copy()
    
    # Remove duplicates based on INVOICE + DESCRIPTION
    original_count = len(combined_df)
    combined_df['_dup_key'] = combined_df['INVOICE'].astype(str) + '|' + combined_df['DESCRIPTION'].astype(str)
    combined_df = combined_df.drop_duplicates(subset=['_dup_key'], keep='first')
    combined_df = combined_df.drop(columns=['_dup_key'])
    duplicates_removed = original_count - len(combined_df)
    
    # Sort by DATE (descending - newest first)
    try:
        combined_df['_date_sort'] = pd.to_datetime(combined_df['DATE'], errors='coerce')
        combined_df = combined_df.sort_values('_date_sort', ascending=False, na_position='last')
        combined_df = combined_df.drop(columns=['_date_sort'])
    except Exception:
        pass
    
    # Ensure column order
    columns = ['INVOICE', 'DATE', 'VEHICLE', 'DESCRIPTION', 'QUANTITY', 'UNIT_COST', 'TOTAL', 'SUPPLIER', 'OWNER']
    for col in columns:
        if col not in combined_df.columns:
            combined_df[col] = ''
    combined_df = combined_df[columns]
    
    # Save to master file
    combined_df.to_excel(master_file, index=False)
    
    # Clear approved data after successful export
    approved_file = OUTPUT_DIR / "approved_data.xlsx"
    if approved_file.exists():
        approved_file.unlink()
    
    return FileResponse(
        master_file,
        filename=f"rita_master_data_{datetime.now().strftime('%Y%m%d')}.xlsx",
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )


# =============================================================================
# CLOUD SYNC (Google Sheets + Database)
# =============================================================================

def check_gsheets_dependencies() -> bool:
    """Check if Google Sheets dependencies are installed."""
    try:
        import gspread
        from google.oauth2.service_account import Credentials
        return True
    except ImportError:
        return False

def check_db_dependencies() -> bool:
    """Check if database dependencies are installed."""
    try:
        import sqlalchemy
        import mysql.connector
        from dotenv import load_dotenv
        return True
    except ImportError:
        return False

def load_gsheets_config() -> Optional[Dict]:
    """Load Google Sheets configuration."""
    if not GSHEETS_CONFIG_FILE.exists():
        return None
    try:
        with open(GSHEETS_CONFIG_FILE, 'r') as f:
            return json.load(f)
    except Exception:
        return None


@app.get("/sync", response_class=HTMLResponse)
async def sync_page(request: Request):
    """Cloud sync page - Google Sheets and Database."""
    # Check what's available
    master_file = OUTPUT_DIR / "rita_master_data.xlsx"
    has_master_data = master_file.exists()
    
    master_records = 0
    if has_master_data:
        try:
            df = pd.read_excel(master_file)
            master_records = len(df)
        except Exception:
            pass
    
    # Check services status
    gsheets_available = check_gsheets_dependencies()
    gsheets_configured = GSHEETS_CONFIG_FILE.exists() and GSHEETS_CREDENTIALS_FILE.exists()
    
    db_available = check_db_dependencies()
    db_configured = (BASE_DIR / ".env").exists()
    
    return templates.TemplateResponse("sync.html", {
        "request": request,
        "page": "sync",
        "stats": get_dashboard_stats(),
        "has_master_data": has_master_data,
        "master_records": master_records,
        "gsheets_available": gsheets_available,
        "gsheets_configured": gsheets_configured,
        "db_available": db_available,
        "db_configured": db_configured,
    })


@app.post("/sync/sheets")
async def sync_to_google_sheets():
    """Sync data to Google Sheets."""
    master_file = OUTPUT_DIR / "rita_master_data.xlsx"
    
    if not master_file.exists():
        return JSONResponse({
            "success": False,
            "error": "Master data file not found. Please export to Excel first."
        })
    
    if not check_gsheets_dependencies():
        return JSONResponse({
            "success": False,
            "error": "Google Sheets libraries not installed. Run: pip install gspread google-auth"
        })
    
    if not GSHEETS_CONFIG_FILE.exists() or not GSHEETS_CREDENTIALS_FILE.exists():
        return JSONResponse({
            "success": False,
            "error": "Google Sheets not configured. Add google_sheets_config.json and google_credentials.json"
        })
    
    try:
        import gspread
        from google.oauth2.service_account import Credentials
        
        df = pd.read_excel(master_file)
        config = load_gsheets_config()
        
        if not config:
            return JSONResponse({
                "success": False,
                "error": "Could not load Google Sheets configuration"
            })
        
        spreadsheet_id = config.get('spreadsheet_id', '')
        worksheet_name = config.get('worksheet_name', 'Sheet1')
        
        scopes = [
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive"
        ]
        creds = Credentials.from_service_account_file(str(GSHEETS_CREDENTIALS_FILE), scopes=scopes)
        client = gspread.authorize(creds)
        
        spreadsheet = client.open_by_key(spreadsheet_id)
        
        try:
            worksheet = spreadsheet.worksheet(worksheet_name)
        except Exception:
            worksheet = spreadsheet.add_worksheet(title=worksheet_name, rows=5000, cols=20)
        
        # Get existing data
        existing_data = worksheet.get_all_values()
        headers = ['INVOICE', 'DATE', 'VEHICLE', 'DESCRIPTION', 'QUANTITY', 'UNIT_COST', 'TOTAL', 'SUPPLIER', 'OWNER']
        
        # Check if sheet has headers
        has_headers = len(existing_data) > 0 and existing_data[0] and str(existing_data[0][0]).upper().strip() == 'INVOICE'
        
        if not has_headers:
            # Fresh push
            worksheet.clear()
            all_rows = [headers]
            for _, row in df.iterrows():
                all_rows.append([str(row.get(col, '')) for col in headers])
            worksheet.update('A1', all_rows)
            
            return JSONResponse({
                "success": True,
                "message": f"Pushed {len(df)} records to Google Sheets!",
                "inserted": len(df),
                "skipped": 0
            })
        else:
            # Incremental push
            existing_keys = set()
            for row in existing_data[1:]:
                if len(row) >= 4:
                    existing_keys.add(f"{row[0]}|{row[3]}")
            
            new_rows = []
            for _, row in df.iterrows():
                key = f"{row.get('INVOICE', '')}|{row.get('DESCRIPTION', '')}"
                if key not in existing_keys:
                    new_rows.append([str(row.get(col, '')) for col in headers])
            
            if new_rows:
                worksheet.append_rows(new_rows)
            
            return JSONResponse({
                "success": True,
                "message": f"Pushed {len(new_rows)} new records to Google Sheets!",
                "inserted": len(new_rows),
                "skipped": len(df) - len(new_rows)
            })
            
    except Exception as e:
        return JSONResponse({
            "success": False,
            "error": f"Google Sheets error: {str(e)}"
        })


@app.post("/sync/database")
async def sync_to_database():
    """Sync data to MySQL database."""
    master_file = OUTPUT_DIR / "rita_master_data.xlsx"
    
    if not master_file.exists():
        return JSONResponse({
            "success": False,
            "error": "Master data file not found. Please export to Excel first."
        })
    
    if not check_db_dependencies():
        return JSONResponse({
            "success": False,
            "error": "Database libraries not installed. Run: pip install sqlalchemy mysql-connector-python python-dotenv"
        })
    
    env_file = BASE_DIR / ".env"
    if not env_file.exists():
        return JSONResponse({
            "success": False,
            "error": "Database not configured. Create a .env file with DB_HOST, DB_NAME, DB_USER, DB_PASSWORD"
        })
    
    try:
        from rita_database import RitaDatabaseManager
        
        df = pd.read_excel(master_file)
        db_manager = RitaDatabaseManager(str(env_file))
        
        # Test connection
        if not db_manager.test_connection():
            return JSONResponse({
                "success": False,
                "error": "Could not connect to database. Check your .env settings."
            })
        
        # Upsert data
        result = db_manager.upsert_data(df, table_name="maintainance")
        
        if result["success"]:
            total = db_manager.get_record_count("maintainance")
            return JSONResponse({
                "success": True,
                "message": f"Database sync complete!",
                "inserted": result["inserted"],
                "skipped": result["skipped"],
                "total_in_db": total
            })
        else:
            return JSONResponse({
                "success": False,
                "error": f"Database error: {result.get('error', 'Unknown error')}"
            })
            
    except Exception as e:
        return JSONResponse({
            "success": False,
            "error": f"Database error: {str(e)}"
        })


@app.post("/sync/all")
async def sync_all():
    """Sync to both Google Sheets and Database."""
    sheets_result = await sync_to_google_sheets()
    db_result = await sync_to_database()
    
    # Parse JSON responses
    sheets_data = json.loads(sheets_result.body) if hasattr(sheets_result, 'body') else {}
    db_data = json.loads(db_result.body) if hasattr(db_result, 'body') else {}
    
    return JSONResponse({
        "google_sheets": sheets_data,
        "database": db_data
    })


@app.get("/api/ocr-status")
async def get_ocr_status():
    """Check if OCR engine is ready."""
    return JSONResponse({
        "ready": is_ocr_ready(),
        "loading": _ocr_loading
    })


def get_dashboard_stats() -> Dict:
    """Get dashboard statistics for sidebar."""
    df = load_approved_data()
    return {
        'total_records': len(df),
        'total_amount': f"{df['TOTAL'].sum():,.2f}" if len(df) > 0 else "0.00",
        'ocr_ready': is_ocr_ready()
    }


# =============================================================================
# MAIN
# =============================================================================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
