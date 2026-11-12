# AP Intelligence

Autonomous Accounts Payable (AP) invoice processing, document understanding, and compliance validation system.

AP Intelligence is built on a decoupled architecture separating observational document reading from interpretive business logic and financial reconciliation. It processes digital and scanned PDF/image documents through multi-stage inspection, extracts financial line items, performs fuzzy purchase order (PO) matching, applies enterprise tolerance thresholds, enforces cryptographic deduplication, and generates an immutable lifecycle audit log.

---

## Architecture Overview

The system processes documents across six sequential stages:

```
[Client / UI]
      │
      ▼
Stage 1: Ingestion & Validation (Magic-byte detection, size bounds, SHA-256 deduplication)
      │
      ▼
Stage 2: Page Analysis & Routing (Digital vector vs scanned raster classification)
      │
      ▼
Stage 3: Document Reading IR (Cloud Vision via Gemini / OpenAI with RapidOCR offline fallback)
      │
      ▼
Stage 4: Semantic Financial Parsing (Key-value normalization, multi-currency float extraction, line-item grid)
      │
      ▼
Stage 5: Autonomous Validation Engine (Fuzzy PO matching, 5% tolerance buffer, 3-way line item match)
      │
      ▼
Stage 6: Persistence & Visualization (SQLite WAL ledger, immutable audit trail, interactive UI dashboard)
```

### Stage Details

1. **Stage 1: File Ingestion, Validation, and Storage**
   - Validates file format using byte signature sniffing (`%PDF`, JPEG, PNG, WebP) rather than untrusted client MIME headers.
   - Computes an in-memory SHA-256 cryptographic digest before persisting files to disk.
   - Enforces a 50 MB file size limit and structural integrity checks via PyMuPDF and Pillow.

2. **Stage 2: Page-Level Detection, Classification, and Routing**
   - Inspects each page independently using PyMuPDF.
   - Evaluates selectable vector text volume against configurable character and word thresholds.
   - Classifies pages into `digital`, `scanned`, or `image`, assigning optimal extraction routes (`["native_text", "visual"]` or `["visual"]`).

3. **Stage 3: Document Reading Intermediate Representation (IR)**
   - Renders document pages to normalized 150 DPI PNG buffers.
   - Extracts verbatim text tokens, normalized bounding boxes (`[0, 0, 1000, 1000]`), row-column table grids, and labeled key-values.
   - Executes with Google Gemini Vision (`gemini-3.5-flash`), with automated fallback to `gemini-3.5-flash-lite`, OpenAI GPT-4o, or local offline RapidOCR (ONNX Runtime) + PyMuPDF.

4. **Stage 4: Semantic Financial Field Parsing**
   - Translates raw Reading IR into structured business schemas.
   - Extracts `invoice_number`, `invoice_date`, `vendor_name`, `total_amount`, and tabular `line_items`.
   - Robustly parses diverse currency symbols ($, EUR, GBP, INR) and handles European vs US comma/decimal formats.

5. **Stage 5: Autonomous Validation and Compliance Engine**
   - Evaluates parsed data against enterprise business rules.
   - Returns deterministic statuses: `APPROVED`, `REJECTED`, or `MANUAL_REVIEW`, accompanied by a complete compliance audit note.

6. **Stage 6: Interactive Dashboard and Live Stepper**
   - Web frontend providing an invoice history ledger and an interactive live pipeline runner visualizing all stage transitions in real time.

---

## Enterprise Features

### 1. Cryptographic Duplicate Detection (SHA-256)
- **Hash Computation:** Every uploaded file is hashed using SHA-256 upon arrival.
- **Storage Deduplication:** When an identical file hash is detected, no redundant file is written to disk. The document is marked with `status='duplicate'` and linked to the original via `duplicate_of`.
- **Automated Compliance Rejection:** Stage 5 validation intercepts duplicates under Rule 0, flagging the invoice as `REJECTED` with an audit note indicating the original submission filename and timestamp.

### 2. Fuzzy Vendor Matching
- **Normalization:** Strips common legal suffixes (`Inc.`, `Ltd.`, `LLC`, `Pvt Ltd`, `Corporation`) and punctuation.
- **Three-Tier Resolution:**
  1. Exact string match.
  2. Substring containment match.
  3. `difflib.SequenceMatcher` fuzzy resolution with a confidence threshold of 0.70 to account for OCR variances, minor typos, or formatting shifts.

### 3. Percentage Tolerance Buffer (5.0%)
- Standard accounts payable workflows allow minor variance for shipping adjustments or currency rounding.
- Invoices where `total_amount <= approved_limit` are marked `APPROVED`.
- Invoices where `approved_limit < total_amount <= approved_limit * 1.05` are marked `APPROVED` within the tolerance buffer, generating an auditable compliance explanation specifying the baseline limit, percentage excess, and ceiling.
- Invoices exceeding the 5.0% threshold are marked `REJECTED`.

### 4. Granular Line-Item 3-Way Matching
- Compares extracted line items against purchase order specifications.
- Matches descriptions using fuzzy similarity (threshold >= 0.55).
- Verifies exact quantities and unit pricing (within a 5% unit price tolerance).
- Generates detailed sub-audit notices for quantity discrepancies, rate deviations, or unrecognized extra line items.

### 5. Immutable State Transition Audit Log
- Backed by the `invoice_audit_trail` table in SQLite with foreign-key referential integrity.
- Automatically records every lifecycle event across Stages 1 through 5:
  - `UPLOADED`: File metadata, size, MIME type.
  - `HASH_VERIFIED`: SHA-256 digest and deduplication check status (`UNIQUE` or `DUPLICATE`).
  - `DUPLICATE_DETECTED`: Cross-reference to original submission when duplicate occurs.
  - `PAGE_ANALYZED`: Page count, classification breakdown, and assigned extraction routes.
  - `VISION_READ`: Vision engine utilized, latency, token count, and table count.
  - `VALIDATED`: Final business decision, compliance reasoning, parsed totals, and line item statistics.
- Exposes a dedicated retrieval endpoint for reviewers and compliance auditing.

---

## Directory Structure

```text
Zamp.ai/
├── backend/
│   ├── app/
│   │   ├── routers/
│   │   │   └── invoices.py          # Upload, analysis, reading, validation, and audit endpoints
│   │   ├── schemas/
│   │   │   └── invoice.py           # Pydantic v2 schemas for all requests and responses
│   │   ├── services/
│   │   │   ├── detector.py          # Stage 2: Page classification & vector/raster analysis
│   │   │   ├── parser.py            # Stage 4: Financial field parsing & normalization
│   │   │   ├── reader.py            # Stage 3: Vision IR reader (Gemini / RapidOCR fallback)
│   │   │   ├── storage.py           # File persistence and SHA-256 hashing
│   │   │   ├── validation.py        # Magic-byte format and size verification
│   │   │   └── validator.py         # Stage 5: Fuzzy PO matching, tolerance & 3-way line matching
│   │   ├── templates/
│   │   │   ├── index.html           # Historical invoice dashboard view
│   │   │   └── process.html         # Real-time multi-stage pipeline stepper view
│   │   ├── config.py                # Configuration and environment variable loader
│   │   ├── database.py              # SQLite connection, WAL mode, migrations & audit trail
│   │   └── main.py                  # FastAPI application entry point
│   ├── data/                        # SQLite database file (ap_intelligence.db)
│   ├── storage/                     # Stored document files
│   ├── requirements.txt             # Pinned project dependencies
│   └── run.py                       # Application runner script
├── tests/
│   ├── test_audit_trail.py          # Audit log table and lifecycle transition tests
│   ├── test_duplicate_and_3way_matching.py # Cryptographic deduplication and 3-way matching tests
│   ├── test_environment_and_vision.py      # Env loading, Gemini client, and OCR fallback tests
│   ├── test_frontend.py             # UI templates and invoice listing endpoint tests
│   └── test_validator_upgrades.py   # Fuzzy matching and 5% tolerance buffer tests
├── .env                             # Environment configuration
├── .gitignore
├── pytest.ini                       # Pytest configuration
└── README.md
```

---

## Prerequisites

- Python 3.10 or higher
- pip package manager

---

## Local Setup and Installation

### 1. Clone the repository and navigate to root

```bash
cd Zamp.ai
```

### 2. Create and activate a virtual environment

**Windows (PowerShell):**
```powershell
python -m venv backend\venv
backend\venv\Scripts\Activate.ps1
```

**macOS / Linux:**
```bash
python3 -m venv backend/venv
source backend/venv/bin/activate
```

### 3. Install dependencies

```bash
pip install -r backend/requirements.txt
```

### 4. Configure environment variables

Ensure a `.env` file exists in `backend/` or the project root. Example configuration:

```env
APP_NAME=AP Intelligence
APP_ENV=development
APP_HOST=127.0.0.1
APP_PORT=8000
LOG_LEVEL=INFO
DATABASE_PATH=data/ap_intelligence.db
STORAGE_PATH=storage

# Cloud Vision API Keys (Optional - Local RapidOCR fallback activates automatically if omitted)
GEMINI_API_KEY=your_gemini_api_key_here
GEMINI_MODEL=gemini-3.5-flash
```

---

## Running the Application

Start the server using `run.py`:

**Windows (PowerShell):**
```powershell
python backend\run.py
```

**macOS / Linux:**
```bash
python backend/run.py
```

Once running, access the interfaces at:
- **Interactive Dashboard:** `http://127.0.0.1:8000/`
- **Live Pipeline Runner:** `http://127.0.0.1:8000/process`
- **Interactive OpenAPI Documentation:** `http://127.0.0.1:8000/docs`
- **ReDoc API Reference:** `http://127.0.0.1:8000/redoc`
- **System Health Check:** `http://127.0.0.1:8000/health`

---

## API Endpoints Summary

| Method | Endpoint | Description |
|:---|:---|:---|
| `GET` | `/health` | System health check and database connectivity verification |
| `GET` | `/` | Web dashboard rendering invoice processing history |
| `GET` | `/process` | Web interface for interactive live pipeline execution |
| `POST` | `/api/invoices/upload` | Ingest single invoice; executes format validation & SHA-256 deduplication |
| `POST` | `/api/invoices/batch-upload` | Ingest batch of up to 10 invoices with atomic multi-status reporting |
| `GET` | `/api/invoices` | List all processed invoices with financial fields and validation status |
| `GET` | `/api/invoices/{document_id}` | Retrieve document storage metadata |
| `POST` | `/api/invoices/{document_id}/analyze` | Stage 2: Page-level vector/raster analysis and route determination |
| `POST` | `/api/invoices/{document_id}/read` | Stage 3: Observational extraction of tokens, tables, and key-values into Reading IR |
| `POST` | `/api/invoices/{document_id}/understand` | Stages 4 & 5: Field parsing, fuzzy PO reconciliation, tolerance check, and 3-way matching |
| `GET` | `/api/invoices/{document_id}/understand` | Retrieve cached Stage 5 understanding and approval decision |
| `GET` | `/api/invoices/{document_id}/audit-trail` | Retrieve immutable state transition audit log for the document |

---

## Running the Test Suite

Execute the full suite using pytest:

```bash
# From workspace root
python -m pytest
```

Verbose output with per-test details:

```bash
python -m pytest -v
```

The test suite covers:
- **`tests/test_audit_trail.py`**: Immutable audit trail persistence, schema verification, lifecycle transitions, and 404 handling.
- **`tests/test_duplicate_and_3way_matching.py`**: Cryptographic SHA-256 duplicate detection, duplicate rejection rule, and granular 3-way line item reconciliation.
- **`tests/test_environment_and_vision.py`**: Environment variable loading resolution, Gemini vision integration, and offline RapidOCR fallback.
- **`tests/test_frontend.py`**: Dashboard and live stepper HTML views, branding elements, and invoice listing API.
- **`tests/test_validator_upgrades.py`**: Fuzzy vendor string normalization, typo tolerance, baseline PO limits, and 5.0% enterprise tolerance ceilings.
