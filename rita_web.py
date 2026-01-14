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
import warnings
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
# CONFIGURATION
# =============================================================================

BASE_DIR = Path(__file__).parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"
TRACKING_FILE = OUTPUT_DIR / "processed_files.json"

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
# OCR ENGINE (Singleton)
# =============================================================================

_ocr_instance: Optional[RitaOCR] = None

def get_ocr_engine() -> RitaOCR:
    """Get or create OCR engine (singleton)."""
    global _ocr_instance
    if _ocr_instance is None:
        _ocr_instance = RitaOCR()
    return _ocr_instance

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
async def settings_page(request: Request):
    """Settings page."""
    folders = get_folder_stats()
    processed = load_processed_files()
    total_processed_files = sum(len(files) for files in processed.values())
    
    return templates.TemplateResponse("settings.html", {
        "request": request,
        "page": "settings",
        "pdf_root": str(PDF_ROOT),
        "output_dir": str(OUTPUT_DIR),
        "folders": folders,
        "processed_files": processed,
        "total_processed_files": total_processed_files,
        "stats": get_dashboard_stats()
    })


@app.post("/settings/reset")
async def reset_folder(folder: str = Form(...)):
    """Reset processing status for a folder."""
    processed = load_processed_files()
    if folder in processed:
        processed[folder] = []
        save_processed_files(processed)
    return RedirectResponse("/settings", status_code=303)


@app.post("/settings/reset-all")
async def reset_all():
    """Reset processing status for all folders."""
    save_processed_files({})
    return RedirectResponse("/settings", status_code=303)


def get_dashboard_stats() -> Dict:
    """Get dashboard statistics for sidebar."""
    df = load_approved_data()
    return {
        'total_records': len(df),
        'total_amount': f"{df['TOTAL'].sum():,.2f}" if len(df) > 0 else "0.00"
    }


# =============================================================================
# MAIN
# =============================================================================

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
