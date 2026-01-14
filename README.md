# 🚗 RITA PDF EXTRACTOR

**AI-Powered Vehicle Maintenance Invoice Extraction System with Interactive Approval Workflow**

RITA (Really Intelligent Text Analyzer) extracts structured data from vehicle maintenance invoices (PDFs) using PaddleOCR v5 - an AI model that excels at reading both handwritten and typed text.

---

## ✨ Features

- **🤖 AI-Powered OCR**: Uses PaddleOCR v5 for accurate text recognition
- **✍️ Handwritten & Typed**: Works with both computer-generated and handwritten invoices- **🌐 Web Interface**: FastAPI-powered web UI with side-by-side image preview- **👀 Interactive Review**: Process invoices one-by-one with approve/edit workflow
- **✏️ Multi-Field Editing**: Edit date, vehicle, invoice number, or individual line items
- **📊 Automatic Calculations**: UNIT_COST calculated as TOTAL ÷ QUANTITY
- **🔄 Duplicate Detection**: Prevents duplicate entries using INVOICE + DESCRIPTION key
- **☁️ Google Sheets Sync**: Push approved data to online Google Sheets tracker
- **🔐 Password Protection**: Reset function protected with administrator password
- **💻 Cross-Platform**: Works on both Windows and Linux

---

## 📋 Supported Invoice Formats

| Supplier | Format Type | Folder Name |
|----------|-------------|-------------|
| **Karimi Auto Garage** | Handwritten | `karimi/` |
| **Meneka Auto Services** | Typed | `meneka/` |
| **Moton Auto Garage** | Computer-generated | `moton/` |
| **P.N Gitau** | Handwritten | `p.n gitau/` |

---

## 🛠️ Installation

### 1. Create Conda Environment

```bash
conda create -n RITA_PDF_EXTRACTOR python=3.10 -y
conda activate RITA_PDF_EXTRACTOR
```

### 2. Install System Dependencies

```bash
# Ubuntu/Debian
sudo apt-get install poppler-utils -y
```

### 3. Install Python Packages

```bash
pip install pandas openpyxl pdf2image pillow
pip install paddlepaddle paddleocr
pip install gspread google-auth  # For Google Sheets sync
```

---

## 📁 Project Structure

```
LOGISTICS-PDF-DATA-EXTRACTOR/
├── rita_extractor.py         # Core OCR extraction engine
├── rita_interactive.py       # Interactive CLI with approval workflow
├── rita_web.py               # FastAPI web interface
├── run_extractor.sh          # Linux launcher script
├── run_extractor.bat         # Windows launcher script
├── RITA_Extractor.desktop    # Linux desktop shortcut
├── google_sheets_config.json # Google Sheets API configuration
├── google_credentials.json   # Google API service account key
├── templates/                # Jinja2 HTML templates
│   ├── base.html
│   ├── index.html
│   ├── process.html
│   ├── data.html
│   └── settings.html
├── static/                   # Static CSS files
│   └── style.css
├── PDFS/                     # Input PDF folders (by supplier)
│   ├── karimi/
│   ├── meneka/
│   ├── moton/
│   ├── p.n gitau/
│   └── ground_truth/         # Validation JSON files
├── output/                   # Output files
│   ├── approved_data.xlsx    # Staging area for approved invoices
│   ├── rita_approved_*.xlsx  # Final exported data (timestamped)
│   └── processed_files.json  # Tracks which PDFs have been processed
└── README.md
```

---

## 🚀 Usage

### Quick Start (Linux)

```bash
./run_extractor.sh
```

Or double-click `RITA_Extractor.desktop` from your file manager.

### Quick Start (Windows)

Double-click `run_extractor.bat`

### Manual Start

```bash
conda activate RITA_PDF_EXTRACTOR
python rita_interactive.py
```

### Web Interface

```bash
conda activate RITA_PDF_EXTRACTOR
uvicorn rita_web:app --host 0.0.0.0 --port 8000
```

Then open http://localhost:8000 in your browser.

---

## 📖 Main Menu Options

```
MAIN MENU:
  [1] 📁 Select Folder and Process PDFs
  [2] 📊 View Approved Data Summary
  [3] 🔄 Reset Processed Status
  [4] 📋 Export to Excel (final)
  [5] ☁️  Push to Google Sheets
  [6] ❌ Exit
```

### Workflow

1. **[1] Select Folder** - Choose a supplier folder and process unprocessed PDFs
2. **Review Each Invoice** - For each PDF, review extracted data and:
   - `[A]` Approve - Save to approved_data.xlsx
   - `[E]` Edit - Modify fields before approving
   - `[S]` Skip - Skip this invoice for now
   - `[Q]` Quit - Return to main menu
3. **[4] Export to Excel** - Merge approved data with existing exports, remove duplicates, sort by date
4. **[5] Push to Google Sheets** - Sync the exported Excel to online Google Sheets

---

## ✏️ Editing Invoices

When editing, you can select multiple fields:

```
EDIT OPTIONS:
  [D] Date
  [V] Vehicle Number
  [I] Invoice Number
  [1-N] Edit line item N
  [+] Add new line item
  [-] Remove line item
```

Enter multiple choices: `D,V,1` to edit Date, Vehicle, and Line 1.

---

## ☁️ Google Sheets Setup

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a project and enable **Google Sheets API** and **Google Drive API**
3. Create a **Service Account** and download the JSON key
4. Save as `google_credentials.json` in the project folder
5. Share your Google Sheet with the service account email
6. Update `google_sheets_config.json` with your spreadsheet ID

---

## 🔐 Security

- **Reset Password**: The reset function requires password `ITDONTMATTER`
- Password is stored as SHA-256 hash (not plain text)
- Reset clears processed status to allow re-processing of PDFs

---

## 📊 Data Columns

| Column | Description |
|--------|-------------|
| INVOICE | Invoice number |
| DATE | Invoice date (DD/MM/YYYY) |
| VEHICLE | Vehicle registration number |
| DESCRIPTION | Service/part description |
| QUANTITY | Number of items/services |
| UNIT_COST | Cost per unit (TOTAL ÷ QUANTITY) |
| TOTAL | Total cost for line item |
| SUPPLIER | Supplier/garage name |
| OWNER | Vehicle owner (default: FIRESIDE) |

---

## 🔧 Troubleshooting

### OCR Not Working
- Ensure PaddleOCR is installed: `pip install paddleocr paddlepaddle`
- First run downloads the model (~100MB)

### PDF Not Converting
- Install poppler: `sudo apt-get install poppler-utils`
- On Windows: Download from [poppler releases](https://github.com/oschwartz10612/poppler-windows/releases)

### Google Sheets Push Failing
- Check `google_credentials.json` exists
- Ensure the sheet is shared with the service account email
- Verify spreadsheet ID in `google_sheets_config.json`

---

## 📝 License

Internal use - Fireside Logistics Department

---

## 👨‍💻 Author

Developed by IT Department for Fireside Logistics
