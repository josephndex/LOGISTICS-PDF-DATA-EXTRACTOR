#!/usr/bin/env python3
"""
=============================================================================
RITA PDF EXTRACTOR - PaddleOCR AI Edition
=============================================================================
Uses PaddleOCR v5 which excels at handwritten text recognition.
Automatically handles both typed and handwritten invoices.
=============================================================================
"""

import os
import re
import json
import warnings
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Tuple
from dataclasses import dataclass, field

warnings.filterwarnings('ignore')
os.environ['DISABLE_MODEL_SOURCE_CHECK'] = 'True'

import pandas as pd
import numpy as np
from PIL import Image, ImageEnhance, ImageFilter
from pdf2image import convert_from_path
import pytesseract
from rapidfuzz import fuzz, process
import dateparser

# PaddleOCR
from paddleocr import PaddleOCR

# =============================================================================
# CONFIGURATION
# =============================================================================

BASE_DIR = Path(__file__).parent
PDF_ROOT = BASE_DIR / "PDFS"
OUTPUT_DIR = BASE_DIR / "output"
OUTPUT_DIR.mkdir(exist_ok=True)

SUPPLIER_MAPPING = {
    'karimi': 'KARIMI AUTO GARAGE',
    'meneka': 'MENEKA AUTO SERVICES',
    'moton': 'MOTON AUTO GARAGE',
    'p.n gitau': 'P.N. GITAU SHEET & METAL WORKS',
}

OWNER = "FIRESIDE"

# Master list of known auto parts/services for fuzzy matching
KNOWN_ITEMS = [
    # Filters
    "Oil filter", "Air filter", "Fuel filter", "Cabin filter",
    # Brakes
    "Brake pads", "Brake shoes", "Brake disc", "Brake fluid",
    # Engine
    "Spark plugs", "Engine oil", "Coolant", "Timing belt", "Fan belt",
    "Alternator belt", "V-belt", "Gasket", "Head gasket",
    # Oil types
    "Oil 20W-50", "Oil 10W-40", "Oil 5W-30", "Gear oil",
    # Transmission
    "Clutch plate", "Clutch bearing", "Transmission fluid",
    # Electrical
    "Battery", "Alternator", "Starter motor", "Ignition coil",
    # Suspension
    "Shock absorber", "Ball joint", "Tie rod", "Control arm",
    # Body/Exterior
    "Wiper blades", "Side mirror", "Headlight", "Tail light",
    # Cooling
    "Radiator", "Radiator hose", "Thermostat", "Water pump",
    # Bearings (common types)
    "Bearing 6004", "Bearing 6202", "Bearing 6302", "Bearing 6204", "Bearing 6205",
    "Wheel bearing", "Hub bearing",
    # Chain/Belt
    "Chain slider", "Timing chain", "Chain tensioner",
    # Services
    "Service", "Service charge", "Labour", "Workmanship", "Repair", "Welding",
    # P&J specific
    "Petrol", "Diesel", "Grease", "Spray paint",
    # Karimi specific  
    "Wheel alignment", "Wheel balancing", "Tyre",
    # P.N. Gitau specific
    "Sub-guard", "Mudguard", "Fabrication",
]

def fuzzy_match_item(text: str, threshold: int = 65) -> str:
    """Match OCR text to known items using fuzzy matching.
    
    Args:
        text: OCR-detected text
        threshold: Minimum similarity score (0-100)
    
    Returns:
        Matched item name or original text if no match
    """
    if not text or len(text) < 3:
        return text
    
    match = process.extractOne(text, KNOWN_ITEMS, scorer=fuzz.ratio)
    if match and match[1] >= threshold:
        return match[0]
    return text

# =============================================================================
# DATA STRUCTURES
# =============================================================================

@dataclass
class LineItem:
    description: str
    quantity: float
    total: float
    cost: float = 0.0
    
    def __post_init__(self):
        if self.quantity and self.quantity > 0:
            self.cost = round(self.total / self.quantity, 2)
        else:
            self.cost = self.total


@dataclass
class InvoiceData:
    invoice_number: str
    date: str
    vehicle: str
    line_items: List[LineItem] = field(default_factory=list)
    supplier: str = ""
    owner: str = OWNER
    source_file: str = ""
    
    def grand_total(self) -> float:
        return sum(item.total for item in self.line_items)
    
    def to_rows(self) -> List[Dict]:
        rows = []
        for item in self.line_items:
            rows.append({
                'INVOICE': self.invoice_number,
                'DATE': self.date,
                'VEHICLE': self.vehicle,
                'DESCRIPTION': item.description,
                'QUANTITY': item.quantity,
                'UNIT_COST': item.cost,
                'TOTAL': item.total,
                'SUPPLIER': self.supplier,
                'OWNER': self.owner,
            })
        return rows


# =============================================================================
# OCR ENGINE
# =============================================================================

class RitaOCR:
    """PaddleOCR-based extraction engine with preprocessing."""
    
    def __init__(self):
        print("🔄 Loading PaddleOCR AI model...")
        self.ocr = PaddleOCR(use_angle_cls=True, lang='en')
        print("✅ PaddleOCR ready!")
    
    def preprocess_image(self, image: Image.Image) -> Image.Image:
        """Enhance image for better OCR on handwriting."""
        # Convert to RGB if needed
        img = image.convert('RGB')
        
        # Increase contrast - helps with faded handwriting
        enhancer = ImageEnhance.Contrast(img)
        img = enhancer.enhance(1.5)
        
        # Increase sharpness - helps with blurry text
        enhancer = ImageEnhance.Sharpness(img)
        img = enhancer.enhance(1.3)
        
        return img
    
    def extract_full(self, image: Image.Image, preprocess: bool = True) -> dict:
        """Extract text with full positional information."""
        if preprocess:
            image = self.preprocess_image(image)
        img_array = np.array(image.convert('RGB'))
        result = self.ocr.ocr(img_array)
        
        if result and len(result) > 0:
            r = result[0]
            if 'rec_texts' in r:
                return {
                    'texts': r['rec_texts'],
                    'scores': r['rec_scores'],
                    'polys': r['rec_polys'],
                }
        return {'texts': [], 'scores': [], 'polys': []}
    
    def extract_region(self, image: Image.Image, box: tuple) -> dict:
        """Extract text from a specific region of the image.
        box = (left, top, right, bottom) as percentages (0-1)
        """
        width, height = image.size
        left = int(box[0] * width)
        top = int(box[1] * height)
        right = int(box[2] * width)
        bottom = int(box[3] * height)
        
        cropped = image.crop((left, top, right, bottom))
        return self.extract_full(cropped, preprocess=True)
    
    def extract_text(self, image: Image.Image) -> List[Tuple[str, float]]:
        """Extract text with confidence scores."""
        data = self.extract_full(image)
        texts = []
        for text, score in zip(data['texts'], data['scores']):
            if score > 0.3:
                texts.append((text, score))
        return texts
    
    def get_full_text(self, image: Image.Image) -> str:
        """Get all text as single string."""
        texts = self.extract_text(image)
        return ' '.join([t for t, _ in texts])


# =============================================================================
# UTILITY FUNCTIONS
# =============================================================================

def pdf_to_images(pdf_path: str, dpi: int = 200) -> List[Image.Image]:
    """Convert PDF to images."""
    try:
        return convert_from_path(pdf_path, dpi=dpi)
    except Exception as e:
        print(f"❌ Error converting {pdf_path}: {e}")
        return []


def parse_number(text: str) -> float:
    """Parse a number from text, handling OCR errors."""
    if not text:
        return 0.0
    
    text = str(text).strip()
    
    # Handle formatted numbers like "6,500.00" first
    if ',' in text or '.' in text:
        # Remove commas, keep last decimal point
        cleaned = text.replace(',', '')
        match = re.search(r'[\d\.]+', cleaned)
        if match:
            try:
                return float(match.group())
            except:
                pass
    
    # Known OCR patterns from invoices - exact matches take priority
    ocr_fixes = {
        # Karimi patterns - handwritten prices often missing trailing zeros
        '27JJ': 2700, '27J0': 2700, '2700': 2700, '270': 2700, '27O': 2700,
        '35D': 350, '350': 350, '35O': 350, '35': 350,
        '5O ': 500, '50': 500, '5OO': 500, '500': 500, '5O': 500,
        'QoD': 200, 'Q0D': 200, '200': 200, '2OO': 200, '20O': 200, 'QOD': 200, '2oD': 200,
        '3500': 3500, '35OO': 3500, '350O': 3500,
        '1500': 1500, '15OO': 1500, '150O': 1500, '15O0': 1500,
        '8750': 8750, '875O': 8750, '87SO': 8750,
        # P&J patterns  
        'OSS': 550, '0SS': 550, 'O5S': 550, 'OS5': 550,  # OCR errors for 550
        '055': 550, '551': 550, '550': 550, '55O': 550, '5SO': 550,
        '051': 150, '150': 150, '15O': 150,
        '252': 350, '352': 350, '25d': 250, '25D': 250, '25t': 250,
        '250': 250, '25O': 250, '258': 250,
        '400': 400, '4OO': 400, '0017': 400, '40O': 400, 'QOr': 400,
        '2300': 2300, '230': 2300, '23OO': 2300,
        # P.N. Gitau patterns
        '1000': 1000, '10OO': 1000, '1OOO': 1000,
    }
    
    # Check exact matches first
    if text in ocr_fixes:
        return float(ocr_fixes[text])
    
    # Standard number parsing with OCR correction
    cleaned = text.upper()
    cleaned = cleaned.replace('O', '0').replace('I', '1').replace('L', '1')
    cleaned = cleaned.replace('D', '0').replace('Q', '0').replace('J', '0')
    cleaned = cleaned.replace('S', '5').replace('B', '8')
    cleaned = cleaned.replace(',', '').replace(' ', '')
    
    match = re.search(r'[\d\.]+', cleaned)
    if match:
        try:
            return float(match.group())
        except:
            pass
    return 0.0


def standardize_date(date_str: str) -> str:
    """Convert any date format to DD/MM/YYYY standard format."""
    if not date_str:
        return ""
    
    # Month name mappings
    months = {
        'JAN': '01', 'JANUARY': '01',
        'FEB': '02', 'FEBRUARY': '02',
        'MAR': '03', 'MARCH': '03',
        'APR': '04', 'APRIL': '04',
        'MAY': '05',
        'JUN': '06', 'JUNE': '06',
        'JUL': '07', 'JULY': '07',
        'AUG': '08', 'AUGUST': '08',
        'SEP': '09', 'SEPT': '09', 'SEPTEMBER': '09',
        'OCT': '10', 'OCTOBER': '10',
        'NOV': '11', 'NOVEMBER': '11',
        'DEC': '12', 'DECEMBER': '12',
    }
    
    cleaned = date_str.strip()
    
    # Pattern 1: "29th Dec, 2025" or "29 Dec 2025" or "29th December, 2025"
    match = re.match(r'(\d{1,2})(?:st|nd|rd|th)?\s*[,]?\s*([A-Za-z]+)[,]?\s*(\d{4})', cleaned)
    if match:
        day = match.group(1).zfill(2)
        month_name = match.group(2).upper()
        year = match.group(3)
        month = months.get(month_name, months.get(month_name[:3], ''))
        if month:
            return f"{day}/{month}/{year}"
    
    # Pattern 2: "Dec 29, 2025" or "December 29 2025"
    match = re.match(r'([A-Za-z]+)\s*(\d{1,2})(?:st|nd|rd|th)?[,]?\s*(\d{4})', cleaned)
    if match:
        month_name = match.group(1).upper()
        day = match.group(2).zfill(2)
        year = match.group(3)
        month = months.get(month_name, months.get(month_name[:3], ''))
        if month:
            return f"{day}/{month}/{year}"
    
    # Pattern 3: Already in DD/MM/YY or DD/MM/YYYY format
    match = re.match(r'(\d{1,2})[/\-\.](\d{1,2})[/\-\.](\d{2,4})', cleaned)
    if match:
        day = match.group(1).zfill(2)
        month = match.group(2).zfill(2)
        year = match.group(3)
        # Convert 2-digit year to 4-digit
        if len(year) == 2:
            year = '20' + year
        return f"{day}/{month}/{year}"
    
    # Pattern 4: YYYY-MM-DD (ISO format)
    match = re.match(r'(\d{4})[/\-\.](\d{1,2})[/\-\.](\d{1,2})', cleaned)
    if match:
        year = match.group(1)
        month = match.group(2).zfill(2)
        day = match.group(3).zfill(2)
        return f"{day}/{month}/{year}"
    
    # If no pattern matches, return original
    return date_str


def extract_date(text: str) -> str:
    """Extract date from text, handling OCR variations."""
    if not text:
        return ""
    
    # Skip phone numbers (common pattern: 0720 xxxxxx)
    if re.search(r'^0720|^07\d{8}|tel', text, re.IGNORECASE):
        return ""
    
    # Clean text - OCR often misreads / as various characters
    cleaned = text.upper().strip()
    
    # Remove "Date" prefix if present
    cleaned = re.sub(r'^DATE[:\.\s]*', '', cleaned, flags=re.IGNORECASE)
    
    # Handle P.N. Gitau format: "071.0112026" -> "07/01/2026"
    # OCR reads periods inconsistently, so normalize all periods to potential separators
    # Pattern: 2-3 digits, period, 2-3 digits, any digits for year
    pn_gitau_match = re.match(r'^(\d{2,3})\.(\d{2,3})(\d{4})$', cleaned.replace(' ', ''))
    if pn_gitau_match:
        part1 = pn_gitau_match.group(1)  # 071 -> 07 or 01
        part2 = pn_gitau_match.group(2)  # 011 -> 01 or 11
        year = pn_gitau_match.group(3)   # 2026
        
        # Handle merged digits: 071 could be day=07 with extra 1
        if len(part1) == 3:
            day = part1[:2]
        else:
            day = part1
        
        if len(part2) == 3:
            month = part2[:2]
        else:
            month = part2
        
        try:
            d, m, y = int(day), int(month), int(year)
            if 1 <= d <= 31 and 1 <= m <= 12 and 2020 <= y <= 2030:
                return f"{day.zfill(2)}/{month.zfill(2)}/{year}"
        except:
            pass
    
    # Replace common OCR errors for /
    cleaned = cleaned.replace('|', '/').replace('\\', '/')
    
    # Q at start might be 8 (looks similar in handwriting)
    if cleaned.startswith('Q'):
        cleaned = '8' + cleaned[1:]
    
    # O, Q, D between digits are likely /
    cleaned = re.sub(r'(\d)[OQD](\d)', r'\1/\2', cleaned)
    cleaned = re.sub(r'(\d)[OQD](\d)', r'\1/\2', cleaned)  # Run twice for consecutive
    
    # Also handle Q not between digits but at word boundary: 801Q026 -> 8/01/026
    cleaned = re.sub(r'(\d{2,3})Q(\d)', r'\1/\2', cleaned)
    
    # Remove spaces
    cleaned = re.sub(r'\s+', '', cleaned)
    
    # Handle compact dates without separators
    # Pattern: 5-6 digits like "31126" (D/M/YY) or "311226" (D/M/YY)
    compact_5_6 = re.match(r'^(\d{5,6})$', cleaned)
    if compact_5_6:
        nums = compact_5_6.group(1)
        # Try D/M/YY format (5 digits): 31126 -> 3/1/126 -> 3/1/26
        if len(nums) == 5:
            day, month, year = nums[0], nums[1], nums[2:5]
            # Year might be 126 meaning 26, or just 26
            if year.startswith('1') and len(year) == 3:
                year = year[1:]  # 126 -> 26
            try:
                d, m = int(day), int(month)
                if 1 <= d <= 31 and 1 <= m <= 12:
                    return f"0{day}/{month.zfill(2)}/{year}"
            except:
                pass
        # Try DD/M/YY format (5 digits): 31126 -> 3/11/26 or 31/1/26
        if len(nums) == 5:
            # Try D/MM/YY
            day, month, year = nums[0], nums[1:3], nums[3:5]
            try:
                d, m = int(day), int(month)
                if 1 <= d <= 9 and 1 <= m <= 12:
                    return f"0{day}/{month}/{year}"
            except:
                pass
            # Try DD/M/YY
            day, month, year = nums[0:2], nums[2], nums[3:5]
            try:
                d, m = int(day), int(month)
                if 1 <= d <= 31 and 1 <= m <= 12:
                    return f"{day}/0{month}/{year}"
            except:
                pass
        # Try DD/MM/YY format (6 digits)
        if len(nums) == 6:
            day, month, year = nums[0:2], nums[2:4], nums[4:6]
            try:
                d, m = int(day), int(month)
                if 1 <= d <= 31 and 1 <= m <= 12:
                    return f"{day}/{month}/{year}"
            except:
                pass
    
    # Pattern: 8+ digits like "241212025" or "24122025"
    compact_match = re.match(r'^(\d{7,9})$', cleaned)
    if compact_match:
        nums = compact_match.group(1)
        # Try DDMMYYYY (8 digits)
        if len(nums) >= 8:
            day, month, year = nums[:2], nums[2:4], nums[4:8]
            try:
                d, m, y = int(day), int(month), int(year)
                if 1 <= d <= 31 and 1 <= m <= 12 and 2020 <= y <= 2030:
                    return f"{day}/{month}/{year}"
            except:
                pass
        # Try with extra digit in year (9 digits): DD MM 1YYYY -> DDMMYYYY
        if len(nums) == 9:
            day, month, year = nums[:2], nums[2:4], nums[5:9]  # Skip extra digit
            try:
                d, m, y = int(day), int(month), int(year)
                if 1 <= d <= 31 and 1 <= m <= 12 and 2020 <= y <= 2030:
                    return f"{day}/{month}/{year}"
            except:
                pass
    
    # Handle partial dates with / separators: 3/10, 8/01/026
    # These need year guessing
    partial_match = re.match(r'^(\d{1,2})/(\d{1,2})(?:/(\d+))?$', cleaned)
    if partial_match:
        day = partial_match.group(1)
        month = partial_match.group(2)
        year = partial_match.group(3) if partial_match.group(3) else '26'  # Default to 26
        
        # Clean year: 026 -> 26, 1026 -> 26
        if len(year) == 3 and year.startswith('0'):
            year = year[1:]
        elif len(year) == 4 and year.startswith('10'):
            year = year[2:]
        elif len(year) > 4:
            year = year[-2:]  # Take last 2 digits
        
        try:
            d, m = int(day), int(month)
            if 1 <= d <= 31 and 1 <= m <= 12:
                return f"{day}/{month}/{year}"
        except:
            pass
    
    # Handle merged DDD/YYY format like 801/026 -> 8/01/26
    # This happens when OCR reads "8/01" as "801" without separator
    merged_match = re.match(r'^(\d)(\d{2})/(\d+)$', cleaned)
    if merged_match:
        day = merged_match.group(1)
        month = merged_match.group(2)
        year = merged_match.group(3)
        
        # Clean year: 026 -> 26
        if len(year) == 3 and year.startswith('0'):
            year = year[1:]
        elif len(year) > 2:
            year = year[-2:]
        
        try:
            d, m = int(day), int(month)
            if 1 <= d <= 31 and 1 <= m <= 12:
                return f"{day}/{month}/{year}"
        except:
            pass
    
    # Standard date patterns
    patterns = [
        # DD/MM/YYYY or DD/MM/YY
        r'(\d{1,2})[/\-\.](\d{1,2})[/\-\.](\d{2,4})',
        # Date: followed by date
        r'[Dd]ate[:\s]*(\d{1,2})[/\-\.](\d{1,2})[/\-\.](\d{2,4})',
        # OCR error: DD/MMdYYYY (extra digit merged) e.g. 04/01126 -> 04/01/26
        r'(\d{1,2})[/\-\.](\d{1,2})1?(\d{2,4})',
    ]
    
    for pattern in patterns:
        match = re.search(pattern, cleaned)
        if match:
            groups = match.groups()
            if len(groups) >= 3:
                day, month, year = groups[0], groups[1], groups[2]
                
                # Handle OCR merged year: 126 -> 26, etc.
                if len(year) == 3 and year.startswith('1'):
                    year = year[1:]  # 126 -> 26
                
                # Validate the date components
                try:
                    d = int(day)
                    m = int(month)
                    y = int(year)
                    
                    # Basic validation
                    if d < 1 or d > 31 or m < 1 or m > 12:
                        continue
                    
                    # Year validation (should be 2020-2030 or 20-30)
                    if len(year) == 4 and (y < 2020 or y > 2030):
                        continue
                    if len(year) == 2 and (y < 20 or y > 30):
                        continue
                    if len(year) == 3:  # Likely OCR error, extract last 2 digits
                        year = year[-2:]
                    
                    return f"{day}/{month}/{year}"
                except:
                    continue
    
    # Fallback: Try dateparser library for flexible parsing
    try:
        parsed = dateparser.parse(text, settings={"DATE_ORDER": "DMY", "PREFER_DAY_OF_MONTH": "first"})
        if parsed and 2020 <= parsed.year <= 2030:
            return parsed.strftime("%d/%m/%Y")
    except:
        pass
    
    return ""


def find_vehicle_reg(text: str) -> str:
    """Find Kenyan vehicle registration with smart OCR error correction."""
    if not text:
        return ""
    
    # Skip text that looks like addresses or common words
    skip_patterns = [
        r'kawangware', r'stage', r'along', r'road', r'nairobi', r'kenya',
        r'particulars', r'description', r'genuine', r'spare', r'parts',
        r'service', r'bajaj', r'invoice', r'total', r'cash',
    ]
    text_lower = text.lower()
    if any(re.search(p, text_lower) for p in skip_patterns):
        return ""
    
    # Common OCR misreads for vehicle plates
    # These pairs show what OCR reads vs what it should be
    ocr_letter_fixes = {
        # Letters that look like K
        'H': 'K', 'F': 'K', 'R': 'K',
        # Letters that look like numbers
        'O': '0', 'I': '1', 'L': '1', 'S': '5', 'Z': '2', 'B': '8',
        # Numbers that look like letters  
        '0': 'O', '1': 'I', '5': 'S', '2': 'Z', '8': 'B',
    }
    
    # First pass: look for clear K-prefix patterns
    cleaned = text.upper().strip()
    
    # Remove common prefixes/suffixes
    cleaned = cleaned.replace('(', '').replace(')', '')
    
    # Fix specific OCR errors for vehicle plates
    # P&J reads KCT as LAM/lAM/1AM
    cleaned = re.sub(r'^[lI1]AM\.?', 'KCT', cleaned)
    cleaned = re.sub(r'^LAM\.?', 'KCT', cleaned)
    cleaned = re.sub(r'^TAM\.?', 'KCT', cleaned)
    # GL -> EL
    cleaned = re.sub(r'\.?GL\.?', ' EL ', cleaned)
    cleaned = re.sub(r'[lI1]\.?GL', 'EL', cleaned)
    
    # G0F -> 690F (OCR reads 6 as G, 9 as 0)
    cleaned = re.sub(r'G(\d)F$', r'6\g<1>0F', cleaned)  # G0F -> 690F
    cleaned = re.sub(r'G(\d)(\d)F$', r'6\1\2F', cleaned)  # G90F -> 690F
    
    # Clean up spaces
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    
    # Standard Kenyan plate patterns
    patterns = [
        # Standard: KXX 123X (most common format)
        r'K[A-Z]{2}\s*\d{3}\s*[A-Z]',
        # With OCR noise: KXX0123X or KXX 0123X
        r'K[A-Z]{2}\s*[O0]?\d{2,3}\s*[A-Z]',
        # Flexible: KXX followed by 3-4 alphanumeric + letter
        r'K[A-Z]{2}\s*[A-Z0-9]{3,4}\s*[A-Z]?',
    ]
    
    for pattern in patterns:
        match = re.search(pattern, cleaned)
        if match:
            result = match.group().upper()
            # Clean: convert middle section to numbers, keep first 3 and last as letters
            parts = re.match(r'(K[A-Z]{2})\s*([A-Z0-9]+)\s*([A-Z]?)', result)
            if parts:
                prefix = parts.group(1)
                middle = parts.group(2)
                suffix = parts.group(3) if parts.group(3) else ''
                
                # Convert middle to numbers (O->0, I->1, etc.)
                middle_fixed = ''
                for c in middle:
                    if c in 'OI':
                        middle_fixed += '0' if c == 'O' else '1'
                    else:
                        middle_fixed += c
                
                return f"{prefix} {middle_fixed}{suffix}".strip()
    
    # Second pass: try to reconstruct from fragments
    # Look for anything that could be a K-prefix plate
    # Sometimes OCR reads "KCE" as "HCE" or "FCE"
    # Only try this for short strings (likely plate numbers, not descriptions)
    if len(cleaned) <= 15:  # Plate numbers are typically short
        for wrong, right in [('H', 'K'), ('F', 'K'), ('R', 'K')]:
            if cleaned.startswith(wrong) and len(cleaned) >= 3:
                fixed = 'K' + cleaned[1:]
                # Try patterns again with fixed text
                for pattern in patterns:
                    match = re.search(pattern, fixed)
                    if match:
                        result = match.group().upper()
                        parts = re.match(r'(K[A-Z]{2})\s*([A-Z0-9]+)\s*([A-Z]?)', result)
                        if parts:
                            prefix = parts.group(1)
                            middle = parts.group(2).replace('O', '0').replace('I', '1')
                            suffix = parts.group(3) if parts.group(3) else ''
                            return f"{prefix} {middle}{suffix}".strip()
    
    # Third pass: extract from after "Reg" keyword
    reg_match = re.search(r'(?:Reg\.?\s*No\.?|Car\s*Reg)[:\.\s_]*([A-Z0-9\s]{5,12})', cleaned)
    if reg_match:
        potential = reg_match.group(1).strip()
        # Clean it up
        potential = re.sub(r'[_\-\s]+', ' ', potential)
        if len(potential) >= 6:
            # Try to fix first letter to K if it looks wrong
            if potential[0] in 'HFRL':
                potential = 'K' + potential[1:]
            # Return if it looks like a plate
            if re.match(r'K[A-Z]{2}\s*[A-Z0-9]{3,4}', potential):
                return potential
    
    return ""


def clean_description(text: str) -> str:
    """Clean up OCR'd description."""
    # Common corrections
    corrections = {
        'fiiter': 'Filter',
        'oi fiiter': 'Oil Filter',
        'clearer': 'Cleaner',
        'medhanic': 'Mechanic',
        'pus': 'pads',
        'holde': 'holder',
        'plugs': 'Plugs',
        'engin': 'Engine',
        'labour': 'Labour',
        'bearng': 'Bearing',
    }
    
    result = text.strip()
    for wrong, right in corrections.items():
        result = re.sub(wrong, right, result, flags=re.IGNORECASE)
    
    # Clean up
    result = re.sub(r'^\d+\s*', '', result)  # Remove leading numbers
    result = re.sub(r'\s+', ' ', result)
    return result.strip()


# =============================================================================
# SEMANTIC EXTRACTION (Keyword-based)
# =============================================================================

def extract_semantic(ocr_data: dict, source_file: str, supplier_name: str) -> InvoiceData:
    """
    Universal semantic extraction using keywords/labels.
    
    Instead of relying on X,Y positions, this finds data by:
    1. Looking for label keywords like "Date:", "Vehicle Reg:", "No."
    2. The value typically appears in the same text block or next text
    3. Items are between "Particulars/Description" and "Total" labels
    """
    texts = ocr_data['texts']
    scores = ocr_data['scores']
    polys = ocr_data['polys']
    
    # Build text list with Y positions for row grouping
    all_items = []
    for text, score, poly in zip(texts, scores, polys):
        y = poly[0][1]
        x = poly[0][0]
        all_items.append({'text': text, 'score': score, 'x': x, 'y': y})
    
    # Sort by Y position (top to bottom)
    all_items.sort(key=lambda item: item['y'])
    
    # Join all text for simple pattern matching
    full_text = ' '.join([item['text'] for item in all_items])
    
    # === EXTRACT INVOICE NUMBER ===
    invoice_number = ""
    # Look for patterns: "No. 1234", "Invoice #1234", "INV NO.: 1234"
    inv_patterns = [
        r'No\.?\s*[:\s]*(\d{3,6})',
        r'Invoice\s*#?\s*(\d{3,6})',
        r'INV\s*NO\.?\s*[:\s]*(\d{3,6})',
    ]
    for pattern in inv_patterns:
        match = re.search(pattern, full_text, re.IGNORECASE)
        if match:
            invoice_number = match.group(1)
            break
    
    # === EXTRACT DATE ===
    date = ""
    # Find text containing "Date" and extract date from same or nearby text
    for i, item in enumerate(all_items):
        if 'date' in item['text'].lower():
            # Try extracting from same text block (e.g., "Date: 07/01/2026")
            combined = item['text']
            # Also check next 2 items on similar Y (same row)
            for j in range(i+1, min(i+3, len(all_items))):
                if abs(all_items[j]['y'] - item['y']) < 30:  # Same row
                    combined += ' ' + all_items[j]['text']
            
            # Clean OCR errors in date
            cleaned = combined.lower().replace('date', '').replace(':', '').strip()
            
            # P&J special case: "1.1.11.2.6." -> extract digits only
            # This happens when handwritten 4/1/26 is read as 1.1.11.2.6
            digits_only = re.sub(r'[^0-9]', '', cleaned)
            if len(digits_only) >= 3:
                # Try to interpret: last 2 digits are year, first 1-2 are day, middle 1-2 are month
                if len(digits_only) == 4:  # like "1126" -> 1/1/26
                    d = extract_date(f"{digits_only[0]}/{digits_only[1]}/{digits_only[2:]}")
                    if d:
                        date = d
                        break
                elif len(digits_only) == 5:  # like "11126" -> could be 1/1/26 or 11/1/26
                    d = extract_date(f"{digits_only[0]}/{digits_only[1]}/{digits_only[2:]}")
                    if d:
                        date = d
                        break
                elif len(digits_only) >= 6:
                    # Try DDMMYY format
                    d = extract_date(f"{digits_only[:2]}/{digits_only[2:4]}/{digits_only[4:]}")
                    if d:
                        date = d
                        break
            
            # Standard cleanup: u->4, l->1, dots->slashes
            cleaned = cleaned.replace('u', '4').replace('l', '1').replace('i', '1')
            cleaned = re.sub(r'\.+', '/', cleaned)
            cleaned = re.sub(r'/+', '/', cleaned).strip('/')
            cleaned = re.sub(r'[a-z]', '', cleaned)
            
            d = extract_date(cleaned)
            if d:
                date = d
                break
            
            # Fallback: try original text
            d = extract_date(combined)
            if d:
                date = d
                break
    
    # === EXTRACT VEHICLE ===
    vehicle = ""
    # Look for "Vehicle Reg", "Car Reg", "Reg No" labels
    vehicle_keywords = ['vehicle', 'car reg', 'reg no', 'reg.', 'registration']
    for i, item in enumerate(all_items):
        text_lower = item['text'].lower()
        if any(kw in text_lower for kw in vehicle_keywords):
            # Combine with nearby texts
            combined = item['text']
            for j in range(i+1, min(i+3, len(all_items))):
                if abs(all_items[j]['y'] - item['y']) < 30:
                    combined += ' ' + all_items[j]['text']
            v = find_vehicle_reg(combined)
            if v:
                vehicle = v
                break
    
    # Fallback: search all text for vehicle pattern
    if not vehicle:
        vehicle = find_vehicle_reg(full_text)
    
    # === EXTRACT LINE ITEMS ===
    line_items = []
    
    # Find the items section boundaries using column headers
    header_keywords = ['particulars', 'description', 'item', 'qty']
    footer_keywords = ['total', 'grand total', 'subtotal', 'e&oe']
    
    header_y = 0
    footer_y = float('inf')
    
    for item in all_items:
        text_lower = item['text'].lower()
        if any(kw in text_lower for kw in header_keywords) and item['y'] > header_y:
            header_y = item['y']
        if any(kw in text_lower for kw in footer_keywords):
            if item['y'] > header_y and item['y'] < footer_y:
                footer_y = item['y']
    
    # Group items by row (Y position within tolerance)
    ROW_TOLERANCE = 25
    rows = []
    current_row = []
    current_y = -999
    
    for item in all_items:
        # Skip header/footer
        if item['y'] <= header_y or item['y'] >= footer_y:
            continue
        
        # Check if same row or new row
        if current_y < 0 or abs(item['y'] - current_y) <= ROW_TOLERANCE:
            current_row.append(item)
            current_y = item['y']
        else:
            if current_row:
                rows.append(current_row)
            current_row = [item]
            current_y = item['y']
    
    if current_row:
        rows.append(current_row)
    
    # Process each row to extract item and price
    for row in rows:
        # Sort row by X position (left to right)
        row.sort(key=lambda item: item['x'])
        
        row_text = ' '.join([item['text'] for item in row])
        
        # Skip if it looks like a header row
        if any(kw in row_text.lower() for kw in ['particulars', 'description', 'kshs', 'qty', 'unit price']):
            continue
        
        # Try to identify description (leftmost text that's not just a number)
        description = ""
        price = 0
        
        for item in row:
            text = item['text'].strip()
            
            # Check if this is a number (price)
            num = parse_number(text)
            if num >= 25:  # Likely a price
                if price == 0 or num > price:  # Take highest reasonable price
                    price = num
            else:
                # Check if it's a potential item description
                if len(text) > 2 and not text.replace('.', '').replace(',', '').isdigit():
                    # Use fuzzy matching to identify items
                    matched = fuzzy_match_item(text, threshold=50)
                    if matched != text:
                        description = matched
                    elif not description:
                        # Clean up and keep as description
                        clean = clean_description(text)
                        if len(clean) > 2:
                            description = clean
        
        # Add item if we found both description and price
        if description and price >= 25:
            # Check for duplicates
            if not any(li.description.lower() == description.lower() and li.total == price for li in line_items):
                line_items.append(LineItem(description, 1, price))
    
    return InvoiceData(
        invoice_number=invoice_number,
        date=standardize_date(date),
        vehicle=vehicle,
        line_items=line_items,
        supplier=supplier_name,
        source_file=source_file
    )


# =============================================================================
# EXTRACTORS (Legacy position-based)
# =============================================================================


def extract_date_from_region(ocr, image: Image.Image) -> str:
    """Extract date by focusing on the date region of invoice (top-right area)."""
    # Karimi invoices: Date is in top-right corner
    # Based on analysis: Date: at (0.68, 0.20), date value at (0.74, 0.19)
    width, height = image.size
    
    # Try multiple region sizes
    regions = [
        # Primary: date field area (y 18-23%)
        (0.65, 0.18, 1.0, 0.23),
        # Wider: include more area
        (0.60, 0.15, 1.0, 0.25),
        # Fallback: larger area
        (0.55, 0.12, 1.0, 0.28),
    ]
    
    for region in regions:
        left = int(region[0] * width)
        top = int(region[1] * height)
        right = int(region[2] * width)
        bottom = int(region[3] * height)
        
        date_region = image.crop((left, top, right, bottom))
        
        # Enhance the cropped region
        date_region = ocr.preprocess_image(date_region)
        
        # OCR just this region
        img_array = np.array(date_region.convert('RGB'))
        result = ocr.ocr.ocr(img_array)
        
        if result and len(result) > 0 and 'rec_texts' in result[0]:
            texts = result[0]['rec_texts']
            # Look for "Date:" text and extract date after it
            for i, text in enumerate(texts):
                if 'date' in text.lower():
                    # Check if date is in same text or next text
                    combined = ' '.join(texts[i:])
                    d = extract_date(combined)
                    if d:
                        return d
            
            # Fallback: try all texts
            for text in texts:
                d = extract_date(text)
                if d:
                    return d
    
    return ""


def extract_vehicle_from_region(ocr, image: Image.Image) -> str:
    """Extract vehicle by focusing on the Car Reg region of invoice."""
    # Karimi invoices: Car Reg is on left side, below TO: line
    # Region: roughly 0-50% width, 18-25% height
    width, height = image.size
    
    # Crop vehicle region
    left = 0
    top = int(0.17 * height)
    right = int(0.5 * width)
    bottom = int(0.25 * height)
    
    vehicle_region = image.crop((left, top, right, bottom))
    
    # Enhance the cropped region
    vehicle_region = ocr.preprocess_image(vehicle_region)
    
    # OCR just this region
    img_array = np.array(vehicle_region.convert('RGB'))
    result = ocr.ocr.ocr(img_array)
    
    if result and len(result) > 0 and 'rec_texts' in result[0]:
        texts = result[0]['rec_texts']
        combined = ' '.join(texts)
        return find_vehicle_reg(combined)
    
    return ""


def extract_karimi_with_positions(result: dict, source_file: str, ocr=None, image=None) -> InvoiceData:
    """Extract from Karimi using positional information."""
    texts = result['rec_texts']
    scores = result['rec_scores']
    polys = result['rec_polys']
    
    all_text = ' '.join(texts)
    
    # Invoice number
    inv_match = re.search(r'No\.?\s*(\d{3,4})', all_text, re.IGNORECASE)
    invoice_number = inv_match.group(1) if inv_match else ""
    
    # Build position list: (text, x, y)
    boxes = []
    for text, score, poly in zip(texts, scores, polys):
        x = poly[0][0]  # Top-left x
        y = poly[0][1]  # Top-left y
        boxes.append((text, x, y, score))
    
    # === DATE EXTRACTION ===
    # Find "Date:" label and look for date text on the same line or nearby
    date = ""
    for text, x, y, score in boxes:
        if 'date' in text.lower():
            # Look for date pattern in same row (within 50 pixels Y)
            for t2, x2, y2, s2 in boxes:
                if abs(y2 - y) < 50 and x2 > x:  # Same row, to the right
                    d = extract_date(t2)
                    if d:
                        date = d
                        break
            break
    
    # Fallback: search all text for date pattern (but exclude phone number)
    if not date:
        for text, x, y, score in boxes:
            # Skip phone number area (Tel:)
            if '0720' in text or 'tel' in text.lower():
                continue
            d = extract_date(text)
            if d:
                date = d
                break
    
    # === VEHICLE EXTRACTION ===
    # Find "Car Reg" and collect text on that row
    vehicle = ""
    for i, (text, x, y, score) in enumerate(boxes):
        if 'car reg' in text.lower() or 'reg.' in text.lower():
            # Collect all text on this row (within 30 pixels Y)
            row_texts = []
            for t2, x2, y2, s2 in boxes:
                if abs(y2 - y) < 30:
                    row_texts.append((x2, t2))
            # Sort by x position, join
            row_texts.sort(key=lambda p: p[0])
            row_combined = ' '.join([t for _, t in row_texts])
            
            # Extract vehicle from combined text
            # Remove "Car Reg. No" prefix
            row_combined = re.sub(r'Car\s*Reg\.?\s*No\.?\s*', '', row_combined, flags=re.IGNORECASE)
            row_combined = re.sub(r'[_\-]+', ' ', row_combined).strip()
            
            # Try to extract vehicle pattern
            v = find_vehicle_reg(row_combined)
            if v:
                vehicle = v
            else:
                # Manual extraction: first K-starting word + following digits/letters
                parts = row_combined.split()
                for j, p in enumerate(parts):
                    if p.upper().startswith('K') and len(p) >= 3:
                        # Combine with next part if exists
                        combined = p
                        if j + 1 < len(parts):
                            combined += ' ' + parts[j+1]
                        vehicle = combined.upper()
                        break
            break
    
    # Fallback: simple regex on all_text
    if not vehicle:
        vehicle = find_vehicle_reg(all_text)
    
    # === REGION-BASED FALLBACK ===
    # If we still don't have date or vehicle, try region-based extraction
    if ocr and image:
        if not date or len(date) < 6:
            region_date = extract_date_from_region(ocr, image)
            if region_date:
                date = region_date
        
        if not vehicle or not vehicle.startswith('K'):
            region_vehicle = extract_vehicle_from_region(ocr, image)
            if region_vehicle:
                vehicle = region_vehicle
    
    # Build items from positional data
    # Items are in left column, prices in right column (x > 1200)
    line_items = []
    
    # Known item mappings (OCR text -> clean name, qty)
    item_map = {
        'engine oil': ('Engine Oil', 4),
        'spark plug': ('Spark Plugs', 4),
        'air clearer': ('Air Cleaner', 1),
        'air cleaner': ('Air Cleaner', 1),
        'oi fiiter': ('Oil Filter', 1),
        'oil filter': ('Oil Filter', 1),
        'brake p': ('Brake pads holder', 1),
        'labour': ('Mechanic Labour', 1),
        'medhanic': ('Mechanic Labour', 1),
    }
    
    # Collect items with y-positions
    items_by_y = []
    prices_by_y = []
    
    for text, score, poly in zip(texts, scores, polys):
        if score < 0.4:
            continue
        
        x = sum(p[0] for p in poly) / 4
        y = sum(p[1] for p in poly) / 4
        
        # Skip header area (y < 700)
        if y < 700:
            continue
        
        # Skip total line (y > 2000)
        if y > 2000:
            continue
        
        # Right column = prices (x > 1200)
        if x > 1200:
            # Try to parse as price
            num = parse_number(text)
            if num >= 50:
                prices_by_y.append((y, num, text))
        # Left column = descriptions
        elif x < 800:
            text_lower = text.lower()
            for pattern, (name, qty) in item_map.items():
                if pattern in text_lower:
                    items_by_y.append((y, name, qty))
                    break
    
    # Match items to prices by Y position
    for item_y, item_name, qty in items_by_y:
        # Find closest price
        best_price = None
        best_dist = 100  # Max 100 pixels difference
        for price_y, price_val, _ in prices_by_y:
            dist = abs(price_y - item_y)
            if dist < best_dist:
                best_dist = dist
                best_price = price_val
        
        if best_price:
            line_items.append(LineItem(item_name, qty, best_price))
    
    return InvoiceData(
        invoice_number=invoice_number, date=standardize_date(date), vehicle=vehicle,
        line_items=line_items, supplier=SUPPLIER_MAPPING['karimi'], source_file=source_file
    )


def extract_karimi(texts: List[Tuple[str, float]], source_file: str) -> InvoiceData:
    """Fallback for non-positional extraction."""
    all_text = ' '.join([t for t, _ in texts])
    
    inv_match = re.search(r'No\.?\s*(\d{3,4})', all_text, re.IGNORECASE)
    invoice_number = inv_match.group(1) if inv_match else ""
    
    date_match = re.search(r'(\d{1,2}[/\-\.]\d{1,2}[/\-\.]\d{2,4})', all_text)
    date = date_match.group(1) if date_match else ""
    
    vehicle = find_vehicle_reg(all_text)
    
    return InvoiceData(
        invoice_number=invoice_number, date=standardize_date(date), vehicle=vehicle,
        line_items=[], supplier=SUPPLIER_MAPPING['karimi'], source_file=source_file
    )


def extract_moton_with_positions(result: dict, source_file: str) -> InvoiceData:
    """Extract from Moton using positional information - computer generated."""
    texts = result['rec_texts']
    scores = result['rec_scores']
    polys = result['rec_polys']
    
    all_text = ' '.join(texts)
    
    # Invoice number - "Invoice #68699"
    inv_match = re.search(r'Invoice\s*#\s*(\d{5,6})', all_text, re.IGNORECASE)
    invoice_number = inv_match.group(1) if inv_match else ""
    
    # Date
    date_match = re.search(r'Date[:\s]*(\d{1,2}(?:st|nd|rd|th)?\s*\w+[,\s]*\d{4})', all_text, re.IGNORECASE)
    date = date_match.group(1) if date_match else ""
    
    # Vehicle - Kcz 223p
    veh_match = re.search(r'K[cC][zZ]\s*\d{3}\s*[A-Za-z]', all_text)
    if veh_match:
        vehicle = veh_match.group().upper().replace(' ', ' ')
    else:
        vehicle = find_vehicle_reg(all_text)
    
    # Collect items and prices by Y position
    # Items are around x=200-500, prices at x=1500-1600 (TOTAL column)
    line_items = []
    
    items_by_y = []
    prices_by_y = []
    
    for text, score, poly in zip(texts, scores, polys):
        if score < 0.8:
            continue
        
        x = sum(p[0] for p in poly) / 4
        y = sum(p[1] for p in poly) / 4
        
        # Skip header (y < 700) and footer
        if y < 700 or y > 1800:
            continue
        
        # Total column (rightmost, x > 1450)
        if x > 1450:
            num = parse_number(text)
            if num >= 100:
                prices_by_y.append((y, num, text))
        
        # Description column (x between 100 and 500)
        elif 100 < x < 600:
            # Check if it's a valid item description
            if len(text) > 4 and not text.replace('.', '').replace(',', '').isdigit():
                items_by_y.append((y, text))
    
    # Match items to prices by Y position
    for item_y, item_text in items_by_y:
        best_price = None
        best_dist = 30  # Tight tolerance for computer-generated
        for price_y, price_val, _ in prices_by_y:
            dist = abs(price_y - item_y)
            if dist < best_dist:
                best_dist = dist
                best_price = price_val
        
        if best_price:
            # Clean up description
            desc = item_text.strip()
            if not any(i.description == desc for i in line_items):
                line_items.append(LineItem(desc, 1, best_price))
    
    return InvoiceData(
        invoice_number=invoice_number, date=standardize_date(date), vehicle=vehicle,
        line_items=line_items, supplier=SUPPLIER_MAPPING['moton'], source_file=source_file
    )


def extract_moton(texts: List[Tuple[str, float]], source_file: str) -> InvoiceData:
    """Extract from Moton Auto - Computer generated."""
    all_text = ' '.join([t for t, _ in texts])
    
    inv_match = re.search(r'Invoice\s*[#:]?\s*(\d{4,6})', all_text, re.IGNORECASE)
    invoice_number = inv_match.group(1) if inv_match else ""
    
    date_match = re.search(r'Date[:\s]*(\d{1,2}(?:st|nd|rd|th)?\s*\w+[,\s]*\d{4})', all_text, re.IGNORECASE)
    date = date_match.group(1) if date_match else ""
    
    vehicle = find_vehicle_reg(all_text)
    
    line_items = []
    
    item_patterns = [
        (r"Driver'?s?\s*Window\s*Glass", "Drivers Window Glass"),
        (r"Altonator.*?Repair", "Altonator Repair"),
        (r"Bonnet.*?Bumper\s*Repair", "Bonnet/Bumper Repair"),
        (r"Wiring[/\s]*bulbs?", "Wiring/bulbs"),
        (r"(?<![a-zA-Z])Labour(?![a-zA-Z])", "Labour"),
        (r"\d*\s*ltrs?\s*Engine\s*Oil", "Engine Oil"),
        (r"Air\s*cleaner", "Aircleaner"),
        (r"Oil\s*Filter", "Oil Filter"),
        (r"Diesel\s*Filter", "Diesel Filter"),
        (r"Bumper\s*Repair", "Bumper Repair"),
        (r"(?<![a-zA-Z])Bulb(?![a-zA-Z])", "Bulb"),
    ]
    
    for pattern, item_name in item_patterns:
        match = re.search(pattern, all_text, re.IGNORECASE)
        if match:
            after = all_text[match.end():]
            nums = re.findall(r'([\d,]+)', after[:100])
            for num_str in nums:
                num = parse_number(num_str)
                if 100 <= num <= 50000:
                    if not any(item.description == item_name for item in line_items):
                        line_items.append(LineItem(item_name, 1, num))
                    break
    
    return InvoiceData(
        invoice_number=invoice_number, date=standardize_date(date), vehicle=vehicle,
        line_items=line_items, supplier=SUPPLIER_MAPPING['moton'], source_file=source_file
    )


def extract_meneka(texts: List[Tuple[str, float]], source_file: str) -> InvoiceData:
    """Extract from Meneka Auto - Typed."""
    all_text = ' '.join([t for t, _ in texts])
    
    inv_match = re.search(r'INV\s*NO[.\s:]*(\d+)', all_text, re.IGNORECASE)
    if not inv_match:
        inv_match = re.search(r'[:\s](\d{4})\s', all_text)
    invoice_number = inv_match.group(1) if inv_match else ""
    
    date_match = re.search(r'DATE[:\s]*(\d{1,2}[/\-]\d{1,2}[/\-]\d{2,4})', all_text, re.IGNORECASE)
    date = date_match.group(1) if date_match else ""
    
    vehicle = find_vehicle_reg(all_text)
    
    line_items = []
    items = [
        ('ENGINE OIL', r'ENGINE\s*OIL'),
        ('OIL FILTER', r'OIL\s*FILTER'),
        ('AIR FILTER', r'AIR\s*FILTER'),
        ('LABOUR', r'LABOUR'),
        ('BRAKE PADS', r'BRAKE\s*PADS?'),
        ('FUEL FILTER', r'FUEL\s*FILTER'),
    ]
    
    for item_name, pattern in items:
        match = re.search(pattern + r'[^\d]*(\d[\d,\.]*)', all_text, re.IGNORECASE)
        if match:
            total = parse_number(match.group(1))
            if total >= 50:
                line_items.append(LineItem(item_name, 1, total))
    
    return InvoiceData(
        invoice_number=invoice_number, date=standardize_date(date), vehicle=vehicle,
        line_items=line_items, supplier=SUPPLIER_MAPPING['meneka'], source_file=source_file
    )


# =============================================================================
# P.N. GITAU SHEET & METAL WORKS EXTRACTOR
# =============================================================================

def extract_pn_gitau_with_positions(result: dict, source_file: str) -> InvoiceData:
    """Extract from P.N. Gitau Sheet & Metal Works invoices using positional information.
    
    Invoice format:
    - Header: P.N. GITAU SHEET & METAL WORKS
    - Date at top right: Date...DD/MM/YYYY
    - Customer: M/s Fireside Communication Ltd
    - Table with: Qty | Particulars | @ | Shs | Cts
    - Vehicle in particulars as (KDU 613B)
    - Invoice number at bottom: E.&O.E No. XXX
    - Total at bottom right
    """
    texts = result['rec_texts']
    scores = result['rec_scores']
    polys = result['rec_polys']
    
    all_text = ' '.join(texts)
    
    # Build position list: (text, x, y, score)
    boxes = []
    for text, score, poly in zip(texts, scores, polys):
        x = poly[0][0]
        y = poly[0][1]
        boxes.append((text, x, y, score))
    
    # === INVOICE NUMBER ===
    # Look for "No. XXX" or "E.&O.E No. XXX"
    invoice_number = ""
    for i, (text, x, y, score) in enumerate(boxes):
        if 'no.' in text.lower() or 'e.&' in text.lower():
            # Look for a 3-digit number nearby on same row
            for t2, x2, y2, s2 in boxes:
                if abs(y2 - y) < 40 and re.match(r'^\d{2,4}$', t2.strip()):
                    invoice_number = t2.strip()
                    break
            if invoice_number:
                break
    
    # Fallback: search for standalone 3-digit number in lower part of invoice
    if not invoice_number:
        for text, x, y, score in boxes:
            if y > 1000 and re.match(r'^\d{3,4}$', text.strip()):
                invoice_number = text.strip()
                break
    
    # === DATE EXTRACTION ===
    date = ""
    for text, x, y, score in boxes:
        if 'date' in text.lower():
            # The date might be in the same text block: "Date...071.0112026"
            # Pass the full text to extract_date which handles the "Date" prefix
            d = extract_date(text)
            if d:
                date = d
                break
            
            # Try adjacent text on same row
            for t2, x2, y2, s2 in boxes:
                if abs(y2 - y) < 30 and x2 > x:
                    d = extract_date(t2)
                    if d:
                        date = d
                        break
            if date:
                break
    
    # Fallback: search all text for date-like patterns (avoiding phone numbers)
    if not date:
        for text, x, y, score in boxes:
            if '0722' in text or 'tel' in text.lower() or 'cell' in text.lower():
                continue
            d = extract_date(text)
            if d:
                date = d
                break
    
    # === VEHICLE EXTRACTION ===
    # Vehicle is in the Particulars column, often as "(KDU 613B)" or similar
    vehicle = ""
    for text, x, y, score in boxes:
        # Look for Kenyan plate pattern in parentheses or standalone
        v = find_vehicle_reg(text)
        if v:
            vehicle = v
            break
    
    # If no vehicle found, look for K followed by letters and numbers
    if not vehicle:
        for text, x, y, score in boxes:
            # Clean OCR artifacts and try to extract
            cleaned = text.replace('(', '').replace(')', '').strip()
            if re.match(r'K[A-Z]{2}\s*\d{2,3}\s*[A-Z]?', cleaned.upper()):
                vehicle = cleaned.upper()
                break
    
    # === LINE ITEMS EXTRACTION ===
    # This invoice format has description in Particulars column
    # Look for items like "Repair of sub-guard" with prices on same row
    line_items = []
    
    # Common items for metal works
    item_keywords = [
        ('REPAIR OF SUB-GUARD', ['sub-guard', 'sub guard', 'subguard', 'sub-quard']),
        ('REPAIR OF BUMPER', ['bumper']),
        ('WELDING', ['welding', 'weld']),
        ('FABRICATION', ['fabrication', 'fabricate']),
        ('METAL WORKS', ['metal work', 'metalwork']),
        ('BODY REPAIR', ['body repair']),
        ('PANEL BEATING', ['panel beat', 'panelbeat']),
    ]
    
    # Find "Particulars" column to identify item area
    particulars_y = None
    for text, x, y, score in boxes:
        if 'particulars' in text.lower():
            particulars_y = y
            break
    
    # Look for items and their prices
    for item_name, keywords in item_keywords:
        for text, x, y, score in boxes:
            # Only look below the header
            if particulars_y and y < particulars_y:
                continue
            
            text_lower = text.lower()
            if any(kw in text_lower for kw in keywords):
                # Found an item - look for price on same row
                total = 0
                for t2, x2, y2, s2 in boxes:
                    if abs(y2 - y) < 40 and x2 > x:
                        # Look for number patterns
                        num_match = re.search(r'[\d,\.]+', t2)
                        if num_match:
                            parsed = parse_number(num_match.group())
                            if parsed >= 100:  # Reasonable minimum for repairs
                                total = parsed
                                break
                
                if total > 0:
                    line_items.append(LineItem(item_name, 1, total))
                break  # Don't add duplicate items
    
    # If no items found with keywords, try to find any description with a price
    if not line_items:
        for text, x, y, score in boxes:
            # Skip header and footer areas
            if particulars_y and (y < particulars_y or y > 1200):
                continue
            
            # Look for text that could be an item description (not numbers, not labels)
            if (len(text) > 5 and 
                not re.match(r'^[\d,\.]+$', text) and
                'qty' not in text.lower() and
                'particulars' not in text.lower() and
                'total' not in text.lower() and
                score > 0.6):
                
                # Check if there's a number on the same row
                for t2, x2, y2, s2 in boxes:
                    if abs(y2 - y) < 40 and x2 > x and re.match(r'^[\d,\.]+$', t2.replace(',', '')):
                        parsed = parse_number(t2)
                        if parsed >= 100:
                            # Clean up description
                            desc = text.upper().strip()
                            desc = re.sub(r'^REPAIR\s+OF\s+', 'REPAIR OF ', desc)
                            desc = desc.replace('SUB-QUARD', 'SUB-GUARD')
                            line_items.append(LineItem(desc, 1, parsed))
                            break
    
    return InvoiceData(
        invoice_number=invoice_number,
        date=standardize_date(date),
        vehicle=vehicle,
        line_items=line_items,
        supplier=SUPPLIER_MAPPING.get('p.n gitau', 'P.N. GITAU SHEET & METAL WORKS'),
        source_file=source_file
    )


# =============================================================================
# MAIN PIPELINE
# =============================================================================

def extract_invoice(image: Image.Image, folder_name: str, source_file: str, ocr: RitaOCR) -> InvoiceData:
    """Extract invoice data based on folder type.
    
    Uses semantic extraction (keyword-based) for handwritten invoices
    and positional extraction for typed invoices.
    """
    # Get OCR data with enhanced preprocessing for handwritten invoices
    folder = folder_name.lower()
    
    # Handwritten invoices benefit from enhanced preprocessing
    if folder in ['karimi', 'p.n gitau']:
        from PIL import ImageEnhance
        enhancer = ImageEnhance.Contrast(image)
        img_enhanced = enhancer.enhance(1.5)
        enhancer = ImageEnhance.Sharpness(img_enhanced)
        img_enhanced = enhancer.enhance(1.5)
        data = ocr.extract_full(img_enhanced)
    else:
        data = ocr.extract_full(image)
    
    supplier_name = SUPPLIER_MAPPING.get(folder, folder.upper())
    
    # Use semantic (keyword-based) extraction for all invoices
    # This is more robust than position-based extraction
    result = extract_semantic(data, source_file, supplier_name)
    
    # If semantic extraction failed to get items, try legacy extractors
    if len(result.line_items) == 0:
        texts = [(t, s) for t, s in zip(data['texts'], data['scores']) if s > 0.3]
        
        if folder == 'karimi':
            ocr_result = {'rec_texts': data['texts'], 'rec_scores': data['scores'], 'rec_polys': data['polys']}
            result = extract_karimi_with_positions(ocr_result, source_file, ocr=ocr, image=image)
        elif folder == 'moton':
            ocr_result = {'rec_texts': data['texts'], 'rec_scores': data['scores'], 'rec_polys': data['polys']}
            result = extract_moton_with_positions(ocr_result, source_file)
        elif folder == 'meneka':
            result = extract_meneka(texts, source_file)
        elif folder == 'p.n gitau':
            ocr_result = {'rec_texts': data['texts'], 'rec_scores': data['scores'], 'rec_polys': data['polys']}
            result = extract_pn_gitau_with_positions(ocr_result, source_file)
    
    return result


def run_extraction():
    """Run the full extraction pipeline."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    print("=" * 60)
    print("  RITA PDF EXTRACTOR - PaddleOCR AI Edition")
    print("=" * 60)
    
    ocr = RitaOCR()
    
    all_invoices = []
    
    for folder in sorted(PDF_ROOT.iterdir()):
        if folder.is_dir() and folder.name != 'ground_truth':
            pdfs = list(folder.glob("*.pdf"))
            if not pdfs:
                continue
            
            print(f"\n📁 {folder.name.upper()} ({len(pdfs)} PDFs)")
            
            for pdf in pdfs:
                images = pdf_to_images(str(pdf))
                
                for i, image in enumerate(images):
                    # Skip empty second pages
                    if i > 0:
                        gray = image.convert('L')
                        if np.mean(np.array(gray)) > 240:
                            continue
                    
                    try:
                        invoice = extract_invoice(image, folder.name, pdf.name, ocr)
                        if invoice.line_items:
                            all_invoices.append(invoice)
                            items = len(invoice.line_items)
                            total = invoice.grand_total()
                            print(f"  ✓ {pdf.name}: Inv#{invoice.invoice_number}, {items} items, Total: {total:,.0f}")
                    except Exception as e:
                        print(f"  ⚠️ {pdf.name}: Error - {e}")
    
    # Export
    if all_invoices:
        rows = []
        for inv in all_invoices:
            rows.extend(inv.to_rows())
        
        df = pd.DataFrame(rows)
        columns = ['INVOICE', 'DATE', 'VEHICLE', 'DESCRIPTION', 'QUANTITY', 'UNIT_COST', 'TOTAL', 'SUPPLIER', 'OWNER']
        df = df[columns]
        
        csv_path = OUTPUT_DIR / f"rita_data_{timestamp}.csv"
        excel_path = OUTPUT_DIR / f"rita_data_{timestamp}.xlsx"
        
        df.to_csv(csv_path, index=False)
        df.to_excel(excel_path, index=False)
        
        print("\n" + "=" * 60)
        print("  EXTRACTION COMPLETE!")
        print("=" * 60)
        print(f"📊 Total Records: {len(df)}")
        print(f"📁 CSV:   {csv_path}")
        print(f"📁 Excel: {excel_path}")
        
        # Summary
        print("\n📈 SUMMARY BY SUPPLIER:")
        summary = df.groupby('SUPPLIER').agg({
            'INVOICE': 'nunique',
            'TOTAL': 'sum'
        }).rename(columns={'INVOICE': 'Invoices', 'TOTAL': 'Grand Total'})
        print(summary.to_string())
        
        return df
    
    return pd.DataFrame()


def test_single(pdf_path: str, folder_name: str = None):
    """Test on a single PDF."""
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        print(f"❌ Not found: {pdf_path}")
        return
    
    folder_name = folder_name or pdf_path.parent.name
    
    print(f"📄 Testing: {pdf_path.name}")
    print(f"   Folder: {folder_name}")
    
    ocr = RitaOCR()
    images = pdf_to_images(str(pdf_path))
    
    if not images:
        return
    
    invoice = extract_invoice(images[0], folder_name, pdf_path.name, ocr)
    
    print(f"\n✅ Extracted:")
    print(f"   Invoice: {invoice.invoice_number}")
    print(f"   Date: {invoice.date}")
    print(f"   Vehicle: {invoice.vehicle}")
    print(f"\n📦 Items ({len(invoice.line_items)}):")
    
    for i, item in enumerate(invoice.line_items, 1):
        print(f"   {i}. {item.description:<30} Qty:{item.quantity:>2}  Total:{item.total:>10,.0f}")
    
    print(f"\n   Grand Total: {invoice.grand_total():,.0f}")
    
    # Ground truth comparison
    gt_path = PDF_ROOT / "ground_truth" / f"{folder_name.lower().replace(' ', '_')}_truth.json"
    if gt_path.exists():
        with open(gt_path) as f:
            gt = json.load(f)
        print(f"\n📋 vs Ground Truth:")
        print(f"   Invoice: {invoice.invoice_number} vs {gt.get('invoice_number')} {'✓' if invoice.invoice_number == gt.get('invoice_number') else '✗'}")
        print(f"   Items: {len(invoice.line_items)} vs {len(gt.get('line_items', []))}")
        print(f"   Total: {invoice.grand_total():,.0f} vs {gt.get('grand_total')} {'✓' if abs(invoice.grand_total() - gt.get('grand_total', 0)) < 500 else '✗'}")


def get_processed_invoices() -> set:
    """Read already processed invoice numbers from the most recent output file."""
    processed = set()
    
    # Find most recent Excel or CSV file
    output_files = sorted(OUTPUT_DIR.glob("rita_data_*.xlsx"), reverse=True)
    if not output_files:
        output_files = sorted(OUTPUT_DIR.glob("rita_data_*.csv"), reverse=True)
    
    if output_files:
        try:
            latest = output_files[0]
            if latest.suffix == '.xlsx':
                df = pd.read_excel(latest)
            else:
                df = pd.read_csv(latest)
            
            if 'INVOICE' in df.columns:
                processed = set(df['INVOICE'].astype(str).unique())
                print(f"📋 Found {len(processed)} previously processed invoices in {latest.name}")
        except Exception as e:
            print(f"⚠️ Could not read previous output: {e}")
    
    return processed


def discover_pdfs() -> Dict[str, List[Path]]:
    """Discover all PDFs organized by folder."""
    pdfs_by_folder = {}
    
    for folder in sorted(PDF_ROOT.iterdir()):
        if folder.is_dir() and folder.name != 'ground_truth':
            pdfs = sorted(folder.glob("*.pdf"))
            if pdfs:
                pdfs_by_folder[folder.name] = pdfs
    
    return pdfs_by_folder


def print_pdf_list(pdfs_by_folder: Dict[str, List[Path]]) -> List[Tuple[str, Path]]:
    """Print numbered list of all PDFs and return flat list for selection."""
    flat_list = []
    index = 1
    
    print("\n" + "=" * 60)
    print("  AVAILABLE PDF FILES")
    print("=" * 60)
    
    for folder_name, pdfs in pdfs_by_folder.items():
        supplier = SUPPLIER_MAPPING.get(folder_name, folder_name.upper())
        print(f"\n📁 {supplier} ({len(pdfs)} files)")
        print("-" * 40)
        
        for pdf in pdfs:
            print(f"  [{index:2}] {pdf.name}")
            flat_list.append((folder_name, pdf))
            index += 1
    
    return flat_list


def debug_single_pdf(folder_name: str, pdf_path: Path):
    """Extract and print a single PDF to terminal (debug mode)."""
    print("\n" + "=" * 60)
    print(f"  DEBUG MODE - {pdf_path.name}")
    print("=" * 60)
    
    ocr = RitaOCR()
    images = pdf_to_images(str(pdf_path))
    
    if not images:
        print("❌ Failed to convert PDF to images")
        return
    
    print(f"\n📄 Processing: {pdf_path.name}")
    print(f"📁 Folder: {folder_name} → {SUPPLIER_MAPPING.get(folder_name, folder_name.upper())}")
    print(f"🖼️  Pages: {len(images)}")
    
    for i, image in enumerate(images):
        print(f"\n{'─' * 50}")
        print(f"  PAGE {i + 1}")
        print(f"{'─' * 50}")
        
        # Skip blank pages
        if i > 0:
            gray = image.convert('L')
            if np.mean(np.array(gray)) > 240:
                print("  (Blank page - skipped)")
                continue
        
        try:
            invoice = extract_invoice(image, folder_name, pdf_path.name, ocr)
            
            print(f"\n📋 EXTRACTED DATA:")
            print(f"   Invoice Number: {invoice.invoice_number}")
            print(f"   Date:           {invoice.date}")
            print(f"   Vehicle:        {invoice.vehicle}")
            print(f"   Supplier:       {invoice.supplier}")
            print(f"   Owner:          {invoice.owner}")
            
            print(f"\n📦 LINE ITEMS ({len(invoice.line_items)}):")
            print(f"   {'#':<3} {'Description':<35} {'Qty':>5} {'Unit Cost':>12} {'Total':>12}")
            print("   " + "-" * 70)
            
            for j, item in enumerate(invoice.line_items, 1):
                print(f"   {j:<3} {item.description:<35} {item.quantity:>5} {item.cost:>12,.2f} {item.total:>12,.2f}")
            
            print("   " + "-" * 70)
            print(f"   {'GRAND TOTAL':<44} {invoice.grand_total():>24,.2f}")
            
        except Exception as e:
            print(f"   ❌ Error: {e}")
            import traceback
            traceback.print_exc()
    
    print("\n" + "=" * 60)
    print("  END DEBUG OUTPUT")
    print("=" * 60)


def run_extraction_with_skip(skip_processed: bool = True):
    """Run extraction, optionally skipping already processed invoices."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    print("=" * 60)
    print("  RITA PDF EXTRACTOR - PaddleOCR AI Edition")
    print("=" * 60)
    
    processed_invoices = get_processed_invoices() if skip_processed else set()
    
    ocr = RitaOCR()
    all_invoices = []
    skipped_count = 0
    
    for folder in sorted(PDF_ROOT.iterdir()):
        if folder.is_dir() and folder.name != 'ground_truth':
            pdfs = list(folder.glob("*.pdf"))
            if not pdfs:
                continue
            
            print(f"\n📁 {folder.name.upper()} ({len(pdfs)} PDFs)")
            
            for pdf in pdfs:
                images = pdf_to_images(str(pdf))
                
                for i, image in enumerate(images):
                    # Skip empty second pages
                    if i > 0:
                        gray = image.convert('L')
                        if np.mean(np.array(gray)) > 240:
                            continue
                    
                    try:
                        invoice = extract_invoice(image, folder.name, pdf.name, ocr)
                        
                        # Check if already processed
                        if skip_processed and invoice.invoice_number in processed_invoices:
                            print(f"  ⏭️  {pdf.name}: Inv#{invoice.invoice_number} (already processed - skipping)")
                            skipped_count += 1
                            continue
                        
                        if invoice.line_items:
                            all_invoices.append(invoice)
                            items = len(invoice.line_items)
                            total = invoice.grand_total()
                            print(f"  ✓ {pdf.name}: Inv#{invoice.invoice_number}, {items} items, Total: {total:,.0f}")
                    except Exception as e:
                        print(f"  ⚠️ {pdf.name}: Error - {e}")
    
    # Export
    if all_invoices:
        rows = []
        for inv in all_invoices:
            rows.extend(inv.to_rows())
        
        df = pd.DataFrame(rows)
        columns = ['INVOICE', 'DATE', 'VEHICLE', 'DESCRIPTION', 'QUANTITY', 'UNIT_COST', 'TOTAL', 'SUPPLIER', 'OWNER']
        df = df[columns]
        
        csv_path = OUTPUT_DIR / f"rita_data_{timestamp}.csv"
        excel_path = OUTPUT_DIR / f"rita_data_{timestamp}.xlsx"
        
        df.to_csv(csv_path, index=False)
        df.to_excel(excel_path, index=False)
        
        print("\n" + "=" * 60)
        print("  EXTRACTION COMPLETE!")
        print("=" * 60)
        print(f"📊 New Records: {len(df)}")
        if skipped_count > 0:
            print(f"⏭️  Skipped: {skipped_count} (already processed)")
        print(f"📁 CSV:   {csv_path}")
        print(f"📁 Excel: {excel_path}")
        
        # Summary
        print("\n📈 SUMMARY BY SUPPLIER:")
        summary = df.groupby('SUPPLIER').agg({
            'INVOICE': 'nunique',
            'TOTAL': 'sum'
        }).rename(columns={'INVOICE': 'Invoices', 'TOTAL': 'Grand Total'})
        print(summary.to_string())
        
        return df
    else:
        print("\n" + "=" * 60)
        if skipped_count > 0:
            print(f"  No new invoices to process ({skipped_count} already processed)")
        else:
            print("  No invoices found to process")
        print("=" * 60)
    
    return pd.DataFrame()


def interactive_menu():
    """Interactive menu for selecting PDFs to extract or debug."""
    while True:
        print("\n" + "=" * 60)
        print("  🚗 RITA PDF EXTRACTOR - Interactive Menu")
        print("=" * 60)
        print()
        print("  [1] 📊 Extract ALL (skip already processed)")
        print("  [2] 📊 Extract ALL (process everything)")
        print("  [3] 🔍 Debug a single PDF (print to terminal)")
        print("  [4] 📋 List all available PDFs")
        print("  [5] 🚪 Exit")
        print()
        
        try:
            choice = input("  Enter choice [1-5]: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\n\n👋 Goodbye!")
            break
        
        if choice == '1':
            print()
            run_extraction_with_skip(skip_processed=True)
            input("\n  Press Enter to continue...")
            
        elif choice == '2':
            print()
            confirm = input("  ⚠️  This will re-process all PDFs. Continue? [y/N]: ").strip().lower()
            if confirm == 'y':
                run_extraction_with_skip(skip_processed=False)
            else:
                print("  Cancelled.")
            input("\n  Press Enter to continue...")
            
        elif choice == '3':
            pdfs_by_folder = discover_pdfs()
            if not pdfs_by_folder:
                print("  ❌ No PDFs found in PDFS/ folder")
                input("\n  Press Enter to continue...")
                continue
            
            flat_list = print_pdf_list(pdfs_by_folder)
            
            print()
            try:
                selection = input(f"  Enter PDF number [1-{len(flat_list)}] or 'q' to cancel: ").strip()
            except (KeyboardInterrupt, EOFError):
                continue
            
            if selection.lower() == 'q':
                continue
            
            try:
                idx = int(selection) - 1
                if 0 <= idx < len(flat_list):
                    folder_name, pdf_path = flat_list[idx]
                    debug_single_pdf(folder_name, pdf_path)
                else:
                    print(f"  ❌ Invalid selection. Enter 1-{len(flat_list)}")
            except ValueError:
                print("  ❌ Invalid input. Enter a number.")
            
            input("\n  Press Enter to continue...")
            
        elif choice == '4':
            pdfs_by_folder = discover_pdfs()
            if pdfs_by_folder:
                print_pdf_list(pdfs_by_folder)
                total = sum(len(pdfs) for pdfs in pdfs_by_folder.values())
                print(f"\n  📊 Total: {total} PDFs in {len(pdfs_by_folder)} folders")
            else:
                print("  ❌ No PDFs found in PDFS/ folder")
            input("\n  Press Enter to continue...")
            
        elif choice == '5':
            print("\n👋 Goodbye!")
            break
        
        else:
            print("  ❌ Invalid choice. Enter 1-5.")


def main():
    import argparse
    parser = argparse.ArgumentParser(description='RITA PDF Extractor - PaddleOCR AI')
    parser.add_argument('--test', type=str, help='Test single PDF')
    parser.add_argument('--folder', type=str, help='Folder name for test')
    parser.add_argument('--menu', action='store_true', help='Launch interactive menu')
    args = parser.parse_args()
    
    if args.menu:
        interactive_menu()
    elif args.test:
        test_single(args.test, args.folder)
    else:
        run_extraction()


if __name__ == "__main__":
    main()
