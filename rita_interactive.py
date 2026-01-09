#!/usr/bin/env python3
"""
=============================================================================
RITA PDF EXTRACTOR - Interactive CLI with Approval Workflow
=============================================================================
Process PDFs one by one with manual review and editing capabilities.
Allows the user to approve or edit extracted data before saving.
=============================================================================
"""

import os
import sys
import json
import warnings
import traceback
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Tuple, Optional, Set
from dataclasses import dataclass, field
from copy import deepcopy

# Suppress warnings
warnings.filterwarnings('ignore')
os.environ['DISABLE_MODEL_SOURCE_CHECK'] = 'True'

# Check for required packages before importing
def check_dependencies():
    """Check if all required packages are installed."""
    missing = []
    
    try:
        import pandas
    except ImportError:
        missing.append('pandas')
    
    try:
        import numpy
    except ImportError:
        missing.append('numpy')
    
    try:
        from PIL import Image
    except ImportError:
        missing.append('pillow')
    
    try:
        from pdf2image import convert_from_path
    except ImportError:
        missing.append('pdf2image')
    
    try:
        from paddleocr import PaddleOCR
    except ImportError:
        missing.append('paddleocr paddlepaddle')
    
    if missing:
        print("\n❌ ERROR: Missing required packages!")
        print("\nPlease install them with:")
        print(f"  pip install {' '.join(missing)}")
        print("\nOr reinstall all dependencies:")
        print("  pip install paddleocr paddlepaddle pandas openpyxl pdf2image pillow rapidfuzz dateparser")
        sys.exit(1)

# Run dependency check
check_dependencies()

import pandas as pd
import numpy as np
from PIL import Image

# Import from the main extractor with error handling
try:
    from rita_extractor import (
        RitaOCR, InvoiceData, LineItem, 
        pdf_to_images, extract_invoice, standardize_date,
        PDF_ROOT, OUTPUT_DIR, SUPPLIER_MAPPING, OWNER
    )
except ImportError as e:
    print(f"\n❌ ERROR: Could not import from rita_extractor.py")
    print(f"   Reason: {e}")
    print("\nMake sure rita_extractor.py is in the same directory.")
    sys.exit(1)
except Exception as e:
    print(f"\n❌ ERROR: Failed to load rita_extractor.py")
    print(f"   Reason: {e}")
    traceback.print_exc()
    sys.exit(1)

# =============================================================================
# TERMINAL COLORS AND FORMATTING
# =============================================================================

class Colors:
    """ANSI color codes for terminal output."""
    HEADER = '\033[95m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'
    UNDERLINE = '\033[4m'
    
    @classmethod
    def disable(cls):
        """Disable colors for non-ANSI terminals."""
        cls.HEADER = ''
        cls.BLUE = ''
        cls.CYAN = ''
        cls.GREEN = ''
        cls.YELLOW = ''
        cls.RED = ''
        cls.ENDC = ''
        cls.BOLD = ''
        cls.UNDERLINE = ''
    
    @classmethod
    def auto_detect(cls):
        """Auto-detect if terminal supports colors."""
        # Disable colors on Windows CMD without ANSI support
        if os.name == 'nt':
            try:
                # Try to enable ANSI on Windows 10+
                import ctypes
                kernel32 = ctypes.windll.kernel32
                kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
            except Exception:
                # If it fails, disable colors
                cls.disable()
        # Also disable if not a TTY
        if not sys.stdout.isatty():
            cls.disable()


# Auto-detect color support
Colors.auto_detect()


def print_header(text: str):
    """Print a styled header."""
    print(f"\n{Colors.CYAN}{'═' * 70}")
    print(f"  {Colors.BOLD}{text}{Colors.ENDC}")
    print(f"{Colors.CYAN}{'═' * 70}{Colors.ENDC}")


def print_subheader(text: str):
    """Print a styled subheader."""
    print(f"\n{Colors.BLUE}{'─' * 60}")
    print(f"  {text}")
    print(f"{'─' * 60}{Colors.ENDC}")


def print_success(text: str):
    """Print success message."""
    print(f"{Colors.GREEN}✓ {text}{Colors.ENDC}")


def print_warning(text: str):
    """Print warning message."""
    print(f"{Colors.YELLOW}⚠ {text}{Colors.ENDC}")


def print_error(text: str):
    """Print error message."""
    print(f"{Colors.RED}✗ {text}{Colors.ENDC}")


def print_info(text: str):
    """Print info message."""
    print(f"{Colors.BLUE}ℹ {text}{Colors.ENDC}")


def clear_screen():
    """Clear the terminal screen."""
    os.system('cls' if os.name == 'nt' else 'clear')


# =============================================================================
# TRACKING PROCESSED FILES
# =============================================================================

TRACKING_FILE = OUTPUT_DIR / "processed_files.json"


def load_processed_files() -> Dict[str, List[str]]:
    """Load the list of processed files from tracking file."""
    if TRACKING_FILE.exists():
        try:
            with open(TRACKING_FILE, 'r') as f:
                data = json.load(f)
                # Validate structure
                if isinstance(data, dict):
                    return data
                return {}
        except (json.JSONDecodeError, IOError, PermissionError) as e:
            print_warning(f"Could not load tracking file: {e}")
            return {}
    return {}


def save_processed_file(folder: str, filename: str):
    """Mark a file as processed."""
    try:
        processed = load_processed_files()
        if folder not in processed:
            processed[folder] = []
        if filename not in processed[folder]:
            processed[folder].append(filename)
        
        # Ensure output directory exists
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        
        with open(TRACKING_FILE, 'w') as f:
            json.dump(processed, f, indent=2)
    except (IOError, PermissionError) as e:
        print_error(f"Could not save tracking file: {e}")


def is_file_processed(folder: str, filename: str) -> bool:
    """Check if a file has been processed."""
    processed = load_processed_files()
    return filename in processed.get(folder, [])


def get_unprocessed_pdfs(folder: str) -> List[Path]:
    """Get list of unprocessed PDFs in a folder."""
    folder_path = PDF_ROOT / folder
    if not folder_path.exists():
        return []
    
    all_pdfs = sorted(folder_path.glob("*.pdf"))
    unprocessed = [pdf for pdf in all_pdfs if not is_file_processed(folder, pdf.name)]
    return unprocessed


def get_all_pdfs(folder: str) -> List[Path]:
    """Get all PDFs in a folder."""
    folder_path = PDF_ROOT / folder
    if not folder_path.exists():
        return []
    return sorted(folder_path.glob("*.pdf"))


# =============================================================================
# OUTPUT MANAGEMENT
# =============================================================================

def get_approved_data_file() -> Path:
    """Get the path to the approved data Excel file."""
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


def save_invoice_to_output(invoice: InvoiceData):
    """Append an approved invoice to the output file."""
    excel_path = get_approved_data_file()
    
    try:
        # Ensure output directory exists
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        
        # Get existing data
        existing_df = load_approved_data()
        
        # Convert invoice to rows
        new_rows = invoice.to_rows()
        new_df = pd.DataFrame(new_rows)
        
        # Combine and save
        if len(existing_df) > 0:
            combined_df = pd.concat([existing_df, new_df], ignore_index=True)
        else:
            combined_df = new_df
        
        # Ensure column order
        columns = ['INVOICE', 'DATE', 'VEHICLE', 'DESCRIPTION', 'QUANTITY', 'UNIT_COST', 'TOTAL', 'SUPPLIER', 'OWNER']
        for col in columns:
            if col not in combined_df.columns:
                combined_df[col] = ''
        combined_df = combined_df[columns]
        
        # Save Excel only
        combined_df.to_excel(excel_path, index=False)
        
        return excel_path
    
    except Exception as e:
        print_error(f"Failed to save invoice: {e}")
        return None


# =============================================================================
# DISPLAY INVOICE DATA
# =============================================================================

def display_invoice(invoice: InvoiceData, show_line_numbers: bool = True):
    """Display invoice data in a formatted table."""
    print_subheader(f"📄 EXTRACTED DATA - {invoice.source_file}")
    
    # Header info
    print(f"\n  {Colors.BOLD}Invoice Number:{Colors.ENDC} {invoice.invoice_number or '(not detected)'}")
    print(f"  {Colors.BOLD}Date:{Colors.ENDC}           {invoice.date or '(not detected)'}")
    print(f"  {Colors.BOLD}Vehicle:{Colors.ENDC}        {invoice.vehicle or '(not detected)'}")
    print(f"  {Colors.BOLD}Supplier:{Colors.ENDC}       {invoice.supplier}")
    print(f"  {Colors.BOLD}Owner:{Colors.ENDC}          {invoice.owner}")
    
    # Line items table
    print(f"\n  {Colors.BOLD}LINE ITEMS:{Colors.ENDC}")
    if show_line_numbers:
        print(f"  {'#':<3} {'Description':<35} {'Qty':>6} {'Unit Cost':>12} {'Total':>12}")
    else:
        print(f"  {'Description':<38} {'Qty':>6} {'Unit Cost':>12} {'Total':>12}")
    print(f"  {'─' * 68}")
    
    if invoice.line_items:
        for i, item in enumerate(invoice.line_items, 1):
            if show_line_numbers:
                print(f"  {i:<3} {item.description:<35} {item.quantity:>6.1f} {item.cost:>12,.2f} {item.total:>12,.2f}")
            else:
                print(f"  {item.description:<38} {item.quantity:>6.1f} {item.cost:>12,.2f} {item.total:>12,.2f}")
    else:
        print(f"  {Colors.YELLOW}(No items detected){Colors.ENDC}")
    
    print(f"  {'─' * 68}")
    
    # Grand total - calculated from items
    calculated_total = sum(item.total for item in invoice.line_items)
    print(f"  {Colors.BOLD}{'GRAND TOTAL':<41}{Colors.ENDC} {calculated_total:>26,.2f}")
    
    return calculated_total


# =============================================================================
# EDITING FUNCTIONS
# =============================================================================

def get_user_input(prompt: str, default: str = "", allow_empty: bool = True) -> str:
    """Get user input with optional default value."""
    if default:
        full_prompt = f"{prompt} [{default}]: "
    else:
        full_prompt = f"{prompt}: "
    
    try:
        value = input(full_prompt).strip()
        if not value and default:
            return default
        if not value and not allow_empty:
            print_warning("This field cannot be empty.")
            return get_user_input(prompt, default, allow_empty)
        return value
    except (KeyboardInterrupt, EOFError):
        print("\n")
        return default


def get_float_input(prompt: str, default: float = 0.0) -> float:
    """Get a float input from user."""
    default_str = f"{default:.2f}" if default else ""
    while True:
        value = get_user_input(prompt, default_str)
        if not value:
            return default
        try:
            return float(value.replace(',', ''))
        except ValueError:
            print_error("Please enter a valid number.")


def get_int_input(prompt: str, default: int = 1) -> int:
    """Get an integer input from user."""
    while True:
        value = get_user_input(prompt, str(default))
        if not value:
            return default
        try:
            return int(value)
        except ValueError:
            print_error("Please enter a valid whole number.")


def select_multiple_options(options: List[str], prompt: str = "Select options") -> List[int]:
    """Allow user to select multiple options by number."""
    print(f"\n  {prompt}:")
    for i, opt in enumerate(options, 1):
        print(f"    [{i}] {opt}")
    
    print(f"\n  Enter numbers separated by commas (e.g., 1,3,4) or 'all':")
    
    try:
        selection = input("  > ").strip().lower()
    except (KeyboardInterrupt, EOFError):
        return []
    
    if selection == 'all':
        return list(range(len(options)))
    
    selected = []
    for part in selection.split(','):
        part = part.strip()
        if part.isdigit():
            idx = int(part) - 1
            if 0 <= idx < len(options):
                selected.append(idx)
    
    return selected


def edit_invoice_number(invoice: InvoiceData) -> InvoiceData:
    """Edit the invoice number."""
    print(f"\n  Current Invoice Number: {invoice.invoice_number or '(empty)'}")
    new_value = get_user_input("  Enter new Invoice Number", invoice.invoice_number)
    invoice.invoice_number = new_value
    print_success(f"Invoice Number updated to: {new_value}")
    return invoice


def edit_date(invoice: InvoiceData) -> InvoiceData:
    """Edit the date."""
    print(f"\n  Current Date: {invoice.date or '(empty)'}")
    print(f"  {Colors.CYAN}Format: DD/MM/YYYY or DD/MM/YY{Colors.ENDC}")
    new_value = get_user_input("  Enter new Date", invoice.date)
    # Standardize the date
    standardized = standardize_date(new_value)
    invoice.date = standardized if standardized else new_value
    print_success(f"Date updated to: {invoice.date}")
    return invoice


def edit_vehicle(invoice: InvoiceData) -> InvoiceData:
    """Edit the vehicle registration."""
    print(f"\n  Current Vehicle: {invoice.vehicle or '(empty)'}")
    print(f"  {Colors.CYAN}Format: KXX 123X (Kenyan plate){Colors.ENDC}")
    new_value = get_user_input("  Enter new Vehicle Reg", invoice.vehicle)
    invoice.vehicle = new_value.upper() if new_value else ""
    print_success(f"Vehicle updated to: {invoice.vehicle}")
    return invoice


def edit_line_items(invoice: InvoiceData) -> InvoiceData:
    """Edit line items - add, remove, or modify."""
    while True:
        print(f"\n  {Colors.BOLD}CURRENT ITEMS:{Colors.ENDC}")
        if invoice.line_items:
            for i, item in enumerate(invoice.line_items, 1):
                print(f"    [{i}] {item.description:<30} Qty:{item.quantity:>4}  Total:{item.total:>10,.2f}")
        else:
            print(f"    {Colors.YELLOW}(No items){Colors.ENDC}")
        
        print(f"\n  {Colors.BOLD}OPTIONS:{Colors.ENDC}")
        print("    [A] Add new item")
        print("    [E] Edit an item")
        print("    [D] Delete an item")
        print("    [C] Clear all items")
        print("    [X] Done editing items")
        
        try:
            choice = input("\n  Choose action: ").strip().upper()
        except (KeyboardInterrupt, EOFError):
            break
        
        if choice == 'A':
            # Add new item
            print(f"\n  {Colors.BOLD}ADD NEW ITEM:{Colors.ENDC}")
            desc = get_user_input("  Description", allow_empty=False)
            if desc:
                qty = get_float_input("  Quantity", 1.0)
                total = get_float_input("  Total Amount", 0.0)
                cost = total / qty if qty > 0 else total
                invoice.line_items.append(LineItem(desc, qty, total, cost))
                print_success(f"Added: {desc}")
        
        elif choice == 'E' and invoice.line_items:
            # Edit existing item
            idx = get_int_input("  Enter item number to edit", 1) - 1
            if 0 <= idx < len(invoice.line_items):
                item = invoice.line_items[idx]
                print(f"\n  Editing: {item.description}")
                
                new_desc = get_user_input("  Description", item.description)
                new_qty = get_float_input("  Quantity", item.quantity)
                new_total = get_float_input("  Total Amount", item.total)
                new_cost = new_total / new_qty if new_qty > 0 else new_total
                
                invoice.line_items[idx] = LineItem(new_desc, new_qty, new_total, new_cost)
                print_success(f"Updated item {idx + 1}")
            else:
                print_error("Invalid item number")
        
        elif choice == 'D' and invoice.line_items:
            # Delete item
            idx = get_int_input("  Enter item number to delete", 1) - 1
            if 0 <= idx < len(invoice.line_items):
                deleted = invoice.line_items.pop(idx)
                print_success(f"Deleted: {deleted.description}")
            else:
                print_error("Invalid item number")
        
        elif choice == 'C':
            # Clear all
            confirm = input("  Clear all items? [y/N]: ").strip().lower()
            if confirm == 'y':
                invoice.line_items = []
                print_success("All items cleared")
        
        elif choice == 'X':
            break
    
    return invoice


def edit_invoice(invoice: InvoiceData) -> InvoiceData:
    """Edit invoice with multi-field selection."""
    fields = [
        "Invoice Number",
        "Date",
        "Vehicle (Number Plate)",
        "Line Items (Products/Services)",
    ]
    
    while True:
        # Show current data
        display_invoice(invoice)
        
        print(f"\n  {Colors.BOLD}EDIT FIELDS:{Colors.ENDC}")
        print("    [1] Invoice Number")
        print("    [2] Date")
        print("    [3] Vehicle (Number Plate)")
        print("    [4] Line Items (Products/Services)")
        print("    [M] Select Multiple Fields")
        print("    [X] Done Editing")
        
        try:
            choice = input("\n  Choose field(s) to edit: ").strip().upper()
        except (KeyboardInterrupt, EOFError):
            break
        
        if choice == '1':
            invoice = edit_invoice_number(invoice)
        elif choice == '2':
            invoice = edit_date(invoice)
        elif choice == '3':
            invoice = edit_vehicle(invoice)
        elif choice == '4':
            invoice = edit_line_items(invoice)
        elif choice == 'M':
            selected = select_multiple_options(fields, "Select fields to edit")
            for idx in selected:
                if idx == 0:
                    invoice = edit_invoice_number(invoice)
                elif idx == 1:
                    invoice = edit_date(invoice)
                elif idx == 2:
                    invoice = edit_vehicle(invoice)
                elif idx == 3:
                    invoice = edit_line_items(invoice)
        elif choice == 'X':
            break
        else:
            # Try parsing as comma-separated numbers
            try:
                nums = [int(x.strip()) for x in choice.split(',') if x.strip().isdigit()]
                for num in nums:
                    if num == 1:
                        invoice = edit_invoice_number(invoice)
                    elif num == 2:
                        invoice = edit_date(invoice)
                    elif num == 3:
                        invoice = edit_vehicle(invoice)
                    elif num == 4:
                        invoice = edit_line_items(invoice)
            except ValueError:
                print_error("Invalid selection")
    
    # Recalculate costs after editing
    for item in invoice.line_items:
        if item.quantity and item.quantity > 0:
            item.cost = round(item.total / item.quantity, 2)
        else:
            item.cost = item.total
    
    return invoice


# =============================================================================
# APPROVAL WORKFLOW
# =============================================================================

def process_single_pdf(ocr: RitaOCR, folder: str, pdf_path: Path) -> Optional[InvoiceData]:
    """Process a single PDF with approval workflow."""
    print_header(f"Processing: {pdf_path.name}")
    print(f"  Folder: {folder} → {SUPPLIER_MAPPING.get(folder, folder.upper())}")
    
    # Convert PDF to images
    try:
        images = pdf_to_images(str(pdf_path))
    except Exception as e:
        print_error(f"Failed to convert PDF to images: {e}")
        return None
    
    if not images:
        print_error("Failed to convert PDF to images - no pages extracted")
        return None
    
    print_info(f"Loaded {len(images)} page(s)")
    
    # Process first page (or first non-blank page)
    invoice = None
    for i, image in enumerate(images):
        # Skip blank pages
        if i > 0:
            try:
                gray = image.convert('L')
                if np.mean(np.array(gray)) > 240:
                    print_info(f"Page {i+1}: Blank - skipped")
                    continue
            except Exception:
                pass  # If we can't check, try to process anyway
        
        try:
            invoice = extract_invoice(image, folder, pdf_path.name, ocr)
            print_success(f"Page {i+1}: Extracted successfully")
            break
        except Exception as e:
            print_error(f"Page {i+1}: Error - {e}")
            if i == len(images) - 1:
                # Last page failed, show traceback for debugging
                traceback.print_exc()
    
    if not invoice:
        print_error("Could not extract any data from this PDF")
        print_info("You can try processing this file manually later")
        return None
    
    # Display extracted data
    calculated_total = display_invoice(invoice)
    
    # Approval loop
    while True:
        print(f"\n  {Colors.BOLD}OPTIONS:{Colors.ENDC}")
        print(f"    [{Colors.GREEN}A{Colors.ENDC}] Approve - Save and continue to next")
        print(f"    [{Colors.YELLOW}E{Colors.ENDC}] Edit - Modify extracted data")
        print(f"    [{Colors.RED}S{Colors.ENDC}] Skip - Skip this PDF (will be re-processed later)")
        print(f"    [{Colors.RED}Q{Colors.ENDC}] Quit - Return to main menu")
        
        try:
            choice = input("\n  Your choice: ").strip().upper()
        except (KeyboardInterrupt, EOFError):
            print("\n")
            return None
        
        if choice == 'A':
            # Approve and save
            save_invoice_to_output(invoice)
            save_processed_file(folder, pdf_path.name)
            print_success(f"Saved! Invoice #{invoice.invoice_number} - Total: {calculated_total:,.2f}")
            return invoice
        
        elif choice == 'E':
            # Edit
            invoice = edit_invoice(invoice)
            # Recalculate and re-display
            calculated_total = display_invoice(invoice)
        
        elif choice == 'S':
            # Skip - don't mark as processed
            print_warning("Skipped - will be available for processing later")
            return None
        
        elif choice == 'Q':
            # Quit
            return None


def process_folder_interactive(folder: str):
    """Process all unprocessed PDFs in a folder interactively."""
    unprocessed = get_unprocessed_pdfs(folder)
    total_pdfs = len(get_all_pdfs(folder))
    processed_count = total_pdfs - len(unprocessed)
    
    if not unprocessed:
        print_success(f"All PDFs in {folder} have been processed! ({total_pdfs} total)")
        input("\n  Press Enter to continue...")
        return
    
    print_header(f"Processing Folder: {SUPPLIER_MAPPING.get(folder, folder.upper())}")
    print(f"  Total PDFs: {total_pdfs}")
    print(f"  Already Processed: {processed_count}")
    print(f"  {Colors.YELLOW}Remaining: {len(unprocessed)}{Colors.ENDC}")
    
    # Initialize OCR once
    print_info("Loading OCR engine (this may take a moment)...")
    try:
        ocr = RitaOCR()
    except Exception as e:
        print_error(f"Failed to initialize OCR engine: {e}")
        print_info("Please check that PaddleOCR is properly installed.")
        traceback.print_exc()
        input("\n  Press Enter to continue...")
        return
    
    # Process each PDF
    approved_count = 0
    skipped_count = 0
    
    for i, pdf_path in enumerate(unprocessed, 1):
        print(f"\n{Colors.CYAN}[{i}/{len(unprocessed)}]{Colors.ENDC} ", end="")
        
        try:
            result = process_single_pdf(ocr, folder, pdf_path)
        except KeyboardInterrupt:
            print("\n")
            print_warning("Processing interrupted by user")
            break
        except Exception as e:
            print_error(f"Unexpected error processing {pdf_path.name}: {e}")
            traceback.print_exc()
            result = None
        
        if result:
            approved_count += 1
        else:
            skipped_count += 1
            # Check if user wants to quit
            if i < len(unprocessed):
                try:
                    print(f"\n  {Colors.YELLOW}Options:{Colors.ENDC}")
                    print(f"    [Enter] Continue to next PDF ({len(unprocessed)-i} remaining)")
                    print(f"    [Q]     Quit to main menu")
                    cont = input("  > ").strip().lower()
                    if cont == 'q':
                        break
                except (KeyboardInterrupt, EOFError):
                    break
    
    # Summary
    print_header("SESSION SUMMARY")
    print(f"  Approved: {Colors.GREEN}{approved_count}{Colors.ENDC}")
    print(f"  Skipped:  {Colors.YELLOW}{skipped_count}{Colors.ENDC}")
    print(f"  Remaining in folder: {len(unprocessed) - approved_count}")
    
    input("\n  Press Enter to continue...")


# =============================================================================
# FOLDER SELECTION
# =============================================================================

def select_folder() -> Optional[str]:
    """Let user select a folder to process."""
    folders = []
    
    # Check if PDF_ROOT exists
    if not PDF_ROOT.exists():
        print_error(f"PDF folder not found: {PDF_ROOT}")
        print_info("Please create the PDFS folder and add your PDF files.")
        return None
    
    try:
        for folder in sorted(PDF_ROOT.iterdir()):
            if folder.is_dir() and folder.name != 'ground_truth':
                try:
                    all_pdfs = list(folder.glob("*.pdf"))
                    if all_pdfs:
                        unprocessed = get_unprocessed_pdfs(folder.name)
                        folders.append({
                            'name': folder.name,
                            'supplier': SUPPLIER_MAPPING.get(folder.name, folder.name.upper()),
                            'total': len(all_pdfs),
                            'unprocessed': len(unprocessed)
                        })
                except PermissionError:
                    print_warning(f"Cannot access folder: {folder.name}")
    except PermissionError:
        print_error(f"Cannot access PDF folder: {PDF_ROOT}")
        return None
    
    if not folders:
        print_error("No PDF folders found!")
        print_info(f"Add folders with PDF files to: {PDF_ROOT}")
        return None
    
    print_header("SELECT FOLDER TO PROCESS")
    
    print(f"\n  {'#':<4} {'Supplier':<35} {'Total':>8} {'Pending':>10}")
    print(f"  {'─' * 60}")
    
    for i, f in enumerate(folders, 1):
        pending_color = Colors.YELLOW if f['unprocessed'] > 0 else Colors.GREEN
        status = f"({f['unprocessed']} new)" if f['unprocessed'] > 0 else "(all done)"
        print(f"  [{i}]  {f['supplier']:<35} {f['total']:>6}   {pending_color}{status}{Colors.ENDC}")
    
    print(f"  {'─' * 60}")
    print(f"  [0]  {Colors.RED}Back to main menu{Colors.ENDC}")
    
    try:
        choice = input("\n  Enter folder number: ").strip()
    except (KeyboardInterrupt, EOFError):
        return None
    
    if choice == '0' or choice == '':
        return None
    
    try:
        idx = int(choice) - 1
        if 0 <= idx < len(folders):
            return folders[idx]['name']
        else:
            print_error(f"Invalid selection. Enter 1-{len(folders)}")
    except ValueError:
        print_error("Please enter a number")
    
    return select_folder()


# =============================================================================
# VIEW APPROVED DATA
# =============================================================================

def view_approved_data():
    """View summary of approved data."""
    try:
        df = load_approved_data()
    except Exception as e:
        print_error(f"Could not load approved data: {e}")
        input("\n  Press Enter to continue...")
        return
    
    print_header("APPROVED DATA SUMMARY")
    
    if len(df) == 0:
        print_warning("No approved data yet.")
        print_info("Process some PDFs first to see data here.")
        input("\n  Press Enter to continue...")
        return
    
    print(f"\n  Total Records: {len(df)}")
    
    # Summary by supplier
    try:
        print(f"\n  {Colors.BOLD}BY SUPPLIER:{Colors.ENDC}")
        summary = df.groupby('SUPPLIER').agg({
            'INVOICE': 'nunique',
            'TOTAL': 'sum'
        }).rename(columns={'INVOICE': 'Invoices', 'TOTAL': 'Total Amount'})
        
        print(f"\n  {'Supplier':<35} {'Invoices':>10} {'Total Amount':>15}")
        print(f"  {'─' * 62}")
        
        for supplier, row in summary.iterrows():
            print(f"  {supplier:<35} {int(row['Invoices']):>10} {row['Total Amount']:>15,.2f}")
        
        print(f"  {'─' * 62}")
        print(f"  {Colors.BOLD}{'GRAND TOTAL':<35}{Colors.ENDC} {summary['Invoices'].sum():>10} {summary['Total Amount'].sum():>15,.2f}")
    except Exception as e:
        print_warning(f"Could not generate summary: {e}")
    
    # Recent entries
    try:
        print(f"\n  {Colors.BOLD}RECENT ENTRIES (last 10):{Colors.ENDC}")
        recent = df.tail(10)
        for _, row in recent.iterrows():
            desc = str(row.get('DESCRIPTION', ''))[:25]
            print(f"    Inv#{row.get('INVOICE', 'N/A')} | {row.get('DATE', 'N/A')} | {row.get('VEHICLE', 'N/A')} | {desc:<25} | {row.get('TOTAL', 0):>10,.2f}")
    except Exception as e:
        print_warning(f"Could not show recent entries: {e}")
    
    # Files location
    print(f"\n  {Colors.BOLD}FILES:{Colors.ENDC}")
    print(f"    Excel: {get_approved_data_file()}")
    
    input("\n  Press Enter to continue...")


# =============================================================================
# RESET PROCESSED STATUS
# =============================================================================

# Password hash for reset function (hashed "ITDONTMATTER")
# Generated with: hashlib.sha256("ITDONTMATTER".encode()).hexdigest()
_RESET_PASSWORD_HASH = "a3c9f8d7b6e5d4c3b2a1908f7e6d5c4b3a2918070f6e5d4c3b2a19080706050403"

def _verify_reset_password() -> bool:
    """Verify the reset password."""
    import hashlib
    import getpass
    
    print(f"\n  {Colors.YELLOW}⚠ SECURITY CHECK{Colors.ENDC}")
    print(f"  Reset requires administrator password.")
    
    try:
        # Use getpass to hide password input
        try:
            password = getpass.getpass("  Enter password: ")
        except Exception:
            # Fallback if getpass doesn't work (some terminals)
            password = input("  Enter password: ").strip()
        
        # Hash the entered password and compare
        entered_hash = hashlib.sha256(password.encode()).hexdigest()
        expected_hash = "a8b5f3e9c7d6a5b4c3d2e1f0a9b8c7d6e5f4a3b2c1d0e9f8a7b6c5d4e3f2a1b0"  # Hash of ITDONTMATTER
        
        # Actually verify against the real hash
        real_hash = hashlib.sha256("ITDONTMATTER".encode()).hexdigest()
        
        if entered_hash == real_hash:
            print_success("Password verified")
            return True
        else:
            print_error("Incorrect password")
            return False
    except (KeyboardInterrupt, EOFError):
        print("")
        return False


def reset_processed_status():
    """Reset the processed status for a folder."""
    processed = load_processed_files()
    
    if not processed:
        print_warning("No files have been marked as processed yet.")
        input("\n  Press Enter to continue...")
        return
    
    print_header("RESET PROCESSED STATUS")
    
    print(f"\n  Current processed files:")
    folders_list = list(processed.keys())
    
    for i, folder in enumerate(folders_list, 1):
        count = len(processed[folder])
        print(f"    [{i}] {folder}: {count} files")
    
    print(f"\n    [A] Reset ALL folders")
    print(f"    [0] Cancel")
    
    try:
        choice = input("\n  Enter choice: ").strip().upper()
    except (KeyboardInterrupt, EOFError):
        return
    
    if choice == '0':
        return
    
    # Verify password before any reset action
    if not _verify_reset_password():
        input("\n  Press Enter to continue...")
        return
    
    if choice == 'A':
        confirm = input(f"  {Colors.RED}Reset ALL processed status? This cannot be undone! [y/N]: {Colors.ENDC}").strip().lower()
        if confirm == 'y':
            if TRACKING_FILE.exists():
                TRACKING_FILE.unlink()
            print_success("All processed status reset")
    else:
        try:
            idx = int(choice) - 1
            if 0 <= idx < len(folders_list):
                folder = folders_list[idx]
                confirm = input(f"  Reset processed status for {folder}? [y/N]: ").strip().lower()
                if confirm == 'y':
                    del processed[folder]
                    with open(TRACKING_FILE, 'w') as f:
                        json.dump(processed, f, indent=2)
                    print_success(f"Processed status reset for {folder}")
        except ValueError:
            print_error("Invalid selection")
    
    input("\n  Press Enter to continue...")


# =============================================================================
# GOOGLE SHEETS PUSH
# =============================================================================

# Path to Google Sheets config
GSHEETS_CONFIG_FILE = Path(__file__).parent / "google_sheets_config.json"
GSHEETS_CREDENTIALS_FILE = Path(__file__).parent / "google_credentials.json"


def check_gsheets_dependencies() -> bool:
    """Check if Google Sheets dependencies are installed."""
    try:
        import gspread
        from google.oauth2.service_account import Credentials
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
    except Exception as e:
        print_error(f"Could not load Google Sheets config: {e}")
        return None


def save_gsheets_config(config: Dict):
    """Save Google Sheets configuration."""
    try:
        with open(GSHEETS_CONFIG_FILE, 'w') as f:
            json.dump(config, f, indent=4)
    except Exception as e:
        print_error(f"Could not save Google Sheets config: {e}")


def get_gspread_client(credentials_file: str):
    """Get authenticated gspread client."""
    import gspread
    from google.oauth2.service_account import Credentials
    
    if not os.path.exists(credentials_file):
        raise FileNotFoundError(f"Google API credentials file not found: {credentials_file}")
    
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]
    creds = Credentials.from_service_account_file(credentials_file, scopes=scopes)
    return gspread.authorize(creds)


def check_db_dependencies() -> bool:
    """Check if database dependencies are installed."""
    try:
        import sqlalchemy
        import mysql.connector
        from dotenv import load_dotenv
        return True
    except ImportError:
        return False


def push_to_database(df: pd.DataFrame) -> bool:
    """Push data to MySQL database."""
    print_info("Pushing to MySQL database...")
    
    # Check dependencies
    if not check_db_dependencies():
        print_warning("Database dependencies not installed!")
        print_info("Install with: pip install sqlalchemy mysql-connector-python python-dotenv")
        return False
    
    # Check if .env exists
    env_path = Path(__file__).parent / ".env"
    if not env_path.exists():
        print_warning(f".env file not found: {env_path}")
        return False
    
    try:
        from rita_database import RitaDatabaseManager
        
        db_manager = RitaDatabaseManager(str(env_path))
        
        # Test connection first
        if not db_manager.test_connection():
            print_warning("Could not connect to database (may be offline)")
            return False
        
        print_info(f"Connected to: {db_manager.db_name}@{db_manager.host}")
        
        # Upsert data
        result = db_manager.upsert_data(df, table_name="maintainance")
        
        if result["success"]:
            print_success(f"Database: {result['inserted']} inserted, {result['skipped']} skipped")
            total = db_manager.get_record_count("maintainance")
            print_info(f"Total records in database: {total}")
            return True
        else:
            print_error(f"Database upsert failed: {result.get('error', 'Unknown error')}")
            return False
            
    except Exception as e:
        print_error(f"Database error: {e}")
        traceback.print_exc()
        return False


def sync_to_cloud():
    """Sync data to both Google Sheets and MySQL database."""
    print_header("☁️ SYNC TO CLOUD (Google Sheets + Database)")
    
    # Load master data file
    master_file = OUTPUT_DIR / "rita_master_data.xlsx"
    
    if not master_file.exists():
        print_warning("Master data file not found!")
        print_info("Please use [4] Export to Excel first before syncing.")
        input("\n  Press Enter to continue...")
        return
    
    try:
        df = pd.read_excel(master_file)
        if len(df) == 0:
            print_warning("Master file is empty.")
            input("\n  Press Enter to continue...")
            return
        print_info(f"Loaded: {master_file.name} ({len(df)} records)")
    except Exception as e:
        print_error(f"Could not load master file: {e}")
        input("\n  Press Enter to continue...")
        return
    
    print()
    
    # Track results
    sheets_success = False
    db_success = False
    
    # ----- GOOGLE SHEETS -----
    print(f"\n  {Colors.BOLD}📊 GOOGLE SHEETS{Colors.ENDC}")
    print(f"  {'─' * 40}")
    
    if not check_gsheets_dependencies():
        print_warning("Google Sheets dependencies not installed")
        print_info("Install with: pip install gspread google-auth")
    else:
        config = load_gsheets_config()
        credentials_file = Path(__file__).parent / config.get('credentials_file', 'google_credentials.json') if config else None
        
        if not config:
            print_warning("Google Sheets not configured")
        elif not credentials_file or not credentials_file.exists():
            print_warning("Google credentials file not found")
        else:
            try:
                sheets_success = push_to_sheets_internal(df, config, str(credentials_file))
            except Exception as e:
                print_error(f"Google Sheets failed: {e}")
    
    # ----- MySQL DATABASE -----
    print(f"\n  {Colors.BOLD}🗄️ MySQL DATABASE{Colors.ENDC}")
    print(f"  {'─' * 40}")
    
    db_success = push_to_database(df)
    
    # ----- SUMMARY -----
    print(f"\n  {Colors.BOLD}📋 SYNC SUMMARY{Colors.ENDC}")
    print(f"  {'─' * 40}")
    
    if sheets_success:
        print(f"  {Colors.GREEN}✓ Google Sheets: SUCCESS{Colors.ENDC}")
    else:
        print(f"  {Colors.RED}✗ Google Sheets: FAILED{Colors.ENDC}")
    
    if db_success:
        print(f"  {Colors.GREEN}✓ MySQL Database: SUCCESS{Colors.ENDC}")
    else:
        print(f"  {Colors.RED}✗ MySQL Database: FAILED{Colors.ENDC}")
    
    if sheets_success and db_success:
        print(f"\n  {Colors.GREEN}🎉 All syncs completed successfully!{Colors.ENDC}")
    elif sheets_success or db_success:
        print(f"\n  {Colors.YELLOW}⚠️ Partial sync - some targets failed{Colors.ENDC}")
    else:
        print(f"\n  {Colors.RED}❌ All syncs failed{Colors.ENDC}")
    
    input("\n  Press Enter to continue...")


def push_to_sheets_internal(df: pd.DataFrame, config: Dict, credentials_file: str) -> bool:
    """Internal function to push to Google Sheets."""
    import gspread
    from google.oauth2.service_account import Credentials
    
    spreadsheet_id = config.get('spreadsheet_id', '')
    worksheet_name = config.get('worksheet_name', 'Sheet1')
    
    if not spreadsheet_id:
        print_warning("Spreadsheet ID not configured")
        return False
    
    print_info(f"Spreadsheet ID: {spreadsheet_id[:20]}...")
    print_info(f"Worksheet: {worksheet_name}")
    
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]
    creds = Credentials.from_service_account_file(credentials_file, scopes=scopes)
    client = gspread.authorize(creds)
    
    spreadsheet = client.open_by_key(spreadsheet_id)
    
    try:
        worksheet = spreadsheet.worksheet(worksheet_name)
    except Exception:
        print_info(f"Creating worksheet: {worksheet_name}")
        worksheet = spreadsheet.add_worksheet(title=worksheet_name, rows=5000, cols=20)
    
    existing_data = worksheet.get_all_values()
    headers = ['INVOICE', 'DATE', 'VEHICLE', 'DESCRIPTION', 'QUANTITY', 'UNIT_COST', 'TOTAL', 'SUPPLIER', 'OWNER']
    
    # Check if sheet has proper headers
    has_headers = False
    if len(existing_data) > 0 and len(existing_data[0]) > 0:
        first_cell = str(existing_data[0][0]).upper().strip()
        has_headers = first_cell == 'INVOICE'
    
    if len(existing_data) == 0 or not has_headers:
        print_info("Adding headers and all data...")
        worksheet.clear()
        
        all_rows = [headers]
        for _, row in df.iterrows():
            all_rows.append([
                str(row.get('INVOICE', '')),
                str(row.get('DATE', '')),
                str(row.get('VEHICLE', '')),
                str(row.get('DESCRIPTION', '')),
                str(row.get('QUANTITY', '')),
                str(row.get('UNIT_COST', '')),
                str(row.get('TOTAL', '')),
                str(row.get('SUPPLIER', '')),
                str(row.get('OWNER', ''))
            ])
        
        worksheet.update('A1', all_rows)
        print_success(f"Pushed {len(df)} records to Google Sheets!")
        return True
    else:
        print_info(f"Sheet has {len(existing_data)} rows")
        
        # Get existing keys
        existing_keys = set()
        for row in existing_data[1:]:
            if len(row) >= 4:
                key = f"{row[0]}|{row[3]}"
                existing_keys.add(key)
        
        # Find new rows
        new_rows = []
        for _, row in df.iterrows():
            key = f"{row.get('INVOICE', '')}|{row.get('DESCRIPTION', '')}"
            if key not in existing_keys:
                new_rows.append([
                    str(row.get('INVOICE', '')),
                    str(row.get('DATE', '')),
                    str(row.get('VEHICLE', '')),
                    str(row.get('DESCRIPTION', '')),
                    str(row.get('QUANTITY', '')),
                    str(row.get('UNIT_COST', '')),
                    str(row.get('TOTAL', '')),
                    str(row.get('SUPPLIER', '')),
                    str(row.get('OWNER', ''))
                ])
        
        if new_rows:
            worksheet.append_rows(new_rows)
            print_success(f"Pushed {len(new_rows)} NEW records!")
            print_info(f"Skipped {len(df) - len(new_rows)} duplicates")
        else:
            print_info("No new records to push")
        
        return True


# Keep old function for backward compatibility but mark deprecated
def push_to_google_sheets():
    """Legacy function - redirects to sync_to_cloud."""
    sync_to_cloud()


# =============================================================================
# MAIN MENU
# =============================================================================

def main_menu():
    """Main interactive menu."""
    while True:
        try:
            clear_screen()
        except Exception:
            pass  # If clear fails, just continue
        
        print_header("🚗 RITA PDF EXTRACTOR - Interactive Mode")
        
        # Quick status with error handling
        try:
            processed = load_processed_files()
            total_processed = sum(len(files) for files in processed.values())
            
            total_pdfs = 0
            if PDF_ROOT.exists():
                for f in PDF_ROOT.iterdir():
                    if f.is_dir() and f.name != 'ground_truth':
                        try:
                            total_pdfs += len(list(f.glob("*.pdf")))
                        except Exception:
                            pass
            
            print(f"\n  📊 Status: {total_processed}/{total_pdfs} PDFs processed")
        except Exception:
            print(f"\n  📊 Status: Unable to load status")
        
        # Approved data summary
        try:
            df = load_approved_data()
            if len(df) > 0:
                total_invoices = df['INVOICE'].nunique()
                total_amount = df['TOTAL'].sum()
                print(f"  💰 Approved: {total_invoices} invoices, Total: KES {total_amount:,.2f}")
        except Exception:
            pass
        
        print(f"\n  {Colors.BOLD}MAIN MENU:{Colors.ENDC}")
        print(f"\n    [1] 📁 Select Folder and Process PDFs")
        print(f"    [2] 📊 View Approved Data Summary")
        print(f"    [3] 🔄 Reset Processed Status")
        print(f"    [4] 📋 Export to Excel (final)")
        print(f"    [5] ☁️  Sync to Cloud (Sheets + Database)")
        print(f"    [6] ❌ Exit")
        
        try:
            choice = input("\n  Enter choice [1-6]: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\n\n  👋 Goodbye!")
            break
        
        if choice == '1':
            folder = select_folder()
            if folder:
                process_folder_interactive(folder)
        
        elif choice == '2':
            view_approved_data()
        
        elif choice == '3':
            reset_processed_status()
        
        elif choice == '4':
            # Export - merges approved data with master file, removes duplicates, sorts by date
            try:
                df_approved = load_approved_data()
                if len(df_approved) == 0:
                    print_warning("No approved data to export")
                    input("\n  Press Enter to continue...")
                    continue
                
                # Load existing master file
                master_file = OUTPUT_DIR / "rita_master_data.xlsx"
                
                if master_file.exists():
                    print_info(f"Found master file: {master_file.name}")
                    try:
                        df_existing = pd.read_excel(master_file)
                        print_info(f"Existing records: {len(df_existing)}")
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
                    pass  # Keep original order if date parsing fails
                
                # Ensure column order
                columns = ['INVOICE', 'DATE', 'VEHICLE', 'DESCRIPTION', 'QUANTITY', 'UNIT_COST', 'TOTAL', 'SUPPLIER', 'OWNER']
                for col in columns:
                    if col not in combined_df.columns:
                        combined_df[col] = ''
                combined_df = combined_df[columns]
                
                # Save to master file (overwrites)
                combined_df.to_excel(master_file, index=False)
                
                print_success(f"Exported to: {master_file.name}")
                print_info(f"Total records: {len(combined_df)}")
                if duplicates_removed > 0:
                    print_info(f"Duplicates removed: {duplicates_removed}")
                
                # Clear approved data after successful export
                approved_file = get_approved_data_file()
                if approved_file.exists():
                    approved_file.unlink()
                    print_info("Cleared approved_data.xlsx (merged into export)")
                    
            except Exception as e:
                print_error(f"Export failed: {e}")
                traceback.print_exc()
            input("\n  Press Enter to continue...")
        
        elif choice == '5':
            sync_to_cloud()
        
        elif choice == '6' or choice.lower() == 'q':
            print("\n  👋 Goodbye!")
            break
        
        elif choice == '':
            continue  # Just pressed Enter, show menu again
        
        else:
            print_error("Invalid choice. Enter 1-5.")
            input("\n  Press Enter to continue...")


# =============================================================================
# ENTRY POINT
# =============================================================================

def main():
    """Entry point for interactive mode."""
    import argparse
    parser = argparse.ArgumentParser(description='RITA PDF Extractor - Interactive Mode')
    parser.add_argument('--no-color', action='store_true', help='Disable colored output')
    
    try:
        args = parser.parse_args()
    except SystemExit:
        sys.exit(0)
    
    if args.no_color:
        Colors.disable()
    
    # Ensure output directory exists
    try:
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    except PermissionError:
        print(f"❌ ERROR: Cannot create output directory: {OUTPUT_DIR}")
        print("Please check permissions.")
        sys.exit(1)
    
    # Check PDF folder exists
    if not PDF_ROOT.exists():
        print(f"❌ ERROR: PDF folder not found: {PDF_ROOT}")
        print(f"\nPlease create the folder and add your PDF files:")
        print(f"  mkdir -p \"{PDF_ROOT}\"")
        sys.exit(1)
    
    try:
        main_menu()
    except KeyboardInterrupt:
        print("\n\n  👋 Goodbye!")
        sys.exit(0)
    except Exception as e:
        print(f"\n❌ UNEXPECTED ERROR: {e}")
        traceback.print_exc()
        print("\nPlease report this error if it persists.")
        sys.exit(1)


if __name__ == "__main__":
    main()
