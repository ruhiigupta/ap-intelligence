"""
invoice.py — Pydantic response/request schemas for the document API.

Using Pydantic v2 (installed as pydantic>=2.0).
All fields are explicitly typed to produce accurate OpenAPI documentation.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel


# ── Core metadata ──────────────────────────────────────────────────────────────

class DocumentMetadata(BaseModel):
    """Full metadata for a single stored (or duplicate-detected) document."""

    document_id:       str
    original_filename: str
    stored_filename:   str
    mime_type:         str
    file_size:         int            # bytes
    sha256_hash:       str
    page_count:        Optional[int]  # PDFs only; None for images
    width_px:          Optional[int]  # images only; None for PDFs
    height_px:         Optional[int]  # images only; None for PDFs
    upload_timestamp:  str            # ISO-8601 UTC string from SQLite
    batch_id:          Optional[str]  # None for single uploads
    status:            str            # 'uploaded' | 'duplicate'
    duplicate_of:      Optional[str]  # document_id of original when status='duplicate'


# ── Single-upload responses ────────────────────────────────────────────────────

class UploadSuccess(BaseModel):
    success:  bool = True
    document: DocumentMetadata


class UploadFailure(BaseModel):
    success:  bool = False
    filename: str
    error:    str


# ── Batch-upload response ──────────────────────────────────────────────────────

class BatchUploadResult(BaseModel):
    batch_id:  str
    total:     int
    succeeded: int
    failed:    int
    results:   List[Union[UploadSuccess, UploadFailure]]


# ── Page Analysis and Routing Schemas ─────────────────────────────────────────

class TextBlockSchema(BaseModel):
    bbox: List[float]
    text: str


class PageDimensions(BaseModel):
    width:  float
    height: float


class PageAnalysisResult(BaseModel):
    document_id:           str
    page_number:           int
    page_type:             str           # 'digital' | 'scanned' | 'image'
    has_selectable_text:   bool
    extracted_native_text: Optional[str] = None
    text_blocks:           List[TextBlockSchema] = []
    extraction_routes:     List[str]     # e.g. ["native_text", "visual"] or ["visual"]
    page_dimensions:       PageDimensions
    detection_status:      str = "completed"


class DocumentAnalysisResponse(BaseModel):
    document_id:   str
    total_pages:   int
    digital_pages: int
    scanned_pages: int
    image_pages:   int
    pages:         List[PageAnalysisResult]


# ── Stage 4 MVP Reading IR Schemas ───────────────────────────────────────────

class MVPToken(BaseModel):
    text: str
    bbox: List[float] = [0.0, 0.0, 0.0, 0.0]  # [x1, y1, x2, y2]


class MVPTableCell(BaseModel):
    row_index: int
    col_index: int
    text: str
    bbox: Optional[List[float]] = None


class MVPTable(BaseModel):
    table_index: int = 1
    rows: int = 0
    columns: int = 0
    headers: Optional[List[str]] = None
    cells: List[MVPTableCell] = []


class MVPPageReading(BaseModel):
    page_number: int
    tokens: List[MVPToken] = []
    tables: List[MVPTable] = []
    raw_key_values: Dict[str, Any] = {}


class PageReadingResultSchema(BaseModel):
    document_id:          str
    page_number:          int
    reading_engine:       str
    engine_version:       str
    status:               str = "completed"
    raw_json_output:      MVPPageReading
    tokens_count:         int = 0
    tables_count:         int = 0
    raw_key_values_count: int = 0
    execution_time_ms:    Optional[int] = None
    created_at:           str


class DocumentReadingResponse(BaseModel):
    document_id:      str
    total_pages_read: int
    pages:            List[PageReadingResultSchema]


# ── Stage 5 Invoice Understanding Schemas ─────────────────────────────────────

class LineItemSchema(BaseModel):
    item_index:  int = 1
    description: str
    quantity:    Optional[float] = None
    unit_price:  Optional[float] = None
    total:       Optional[float] = None


class ParsedInvoiceData(BaseModel):
    invoice_number: Optional[str] = None
    invoice_date:   Optional[str] = None
    vendor_name:    Optional[str] = None
    total_amount:   Optional[float] = None
    line_items:     List[LineItemSchema] = []


class InvoiceUnderstandingResponse(BaseModel):
    document_id:          str
    invoice_number:       Optional[str] = None
    invoice_date:         Optional[str] = None
    vendor_name:          Optional[str] = None
    total_amount:         Optional[float] = None
    line_items:           List[LineItemSchema] = []
    validation_status:    str  # 'APPROVED' | 'REJECTED' | 'MANUAL_REVIEW'
    validation_reasoning: str
    created_at:           str


# ── Listing Schemas ───────────────────────────────────────────────────────────

class InvoiceListItem(BaseModel):
    document_id:          str
    original_filename:    str
    stored_filename:      Optional[str] = None
    mime_type:            Optional[str] = None
    file_size:            Optional[int] = None
    page_count:           Optional[int] = None
    upload_timestamp:     str
    invoice_number:       Optional[str] = None
    invoice_date:         Optional[str] = None
    vendor_name:          Optional[str] = None
    total_amount:         Optional[float] = None
    validation_status:    str = "PENDING"
    validation_reasoning: Optional[str] = None
    parsed_at:            Optional[str] = None


class InvoiceListResponse(BaseModel):
    total:    int
    invoices: List[InvoiceListItem]


# ── Audit Trail Schemas ───────────────────────────────────────────────────────

class AuditTrailEntry(BaseModel):
    """A single immutable event in a document's lifecycle audit log."""
    audit_id:    int
    document_id: str
    event_type:  str            # e.g. UPLOADED, HASH_VERIFIED, PAGE_ANALYZED, VISION_READ, VALIDATED
    details:     Dict[str, Any] # Parsed from the stored JSON blob
    created_at:  str            # ISO-8601 UTC timestamp


class AuditTrailResponse(BaseModel):
    """Full audit trail for a document, ordered chronologically."""
    document_id:  str
    total_events: int
    events:       List[AuditTrailEntry]
