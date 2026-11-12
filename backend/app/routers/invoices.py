"""
invoices.py — Invoice upload and retrieval endpoints.

Endpoints:
  POST /api/invoices/upload          — upload a single invoice
  POST /api/invoices/batch-upload    — upload up to 10 invoices
  GET  /api/invoices/{document_id}   — retrieve document metadata

Design notes:
  - Files are validated before storage. Validation errors return HTTP 422.
  - Duplicate files (identical SHA-256) are recorded with status='duplicate';
    no second copy is written to disk. Duplicate uploads still return HTTP 201.
  - Each file in a batch is processed independently; one failure never aborts others.
  - No OCR, PO matching, or financial logic runs here.
"""

import json
import logging
import uuid
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, File, HTTPException, UploadFile

from app.database import get_db
from app.schemas.invoice import (
    AuditTrailEntry,
    AuditTrailResponse,
    BatchUploadResult,
    DocumentAnalysisResponse,
    DocumentMetadata,
    DocumentReadingResponse,
    InvoiceListItem,
    InvoiceListResponse,
    InvoiceUnderstandingResponse,
    LineItemSchema,
    PageAnalysisResult,
    PageDimensions,
    PageReadingResultSchema,
    ParsedInvoiceData,
    TextBlockSchema,
    UploadFailure,
    UploadSuccess,
)
from app.database import append_audit_event
from app.services.detector import analyze_document_pages
from app.services.parser import parse_invoice_for_document
from app.services.reader import read_page
from app.services.storage import (
    build_stored_filename,
    compute_sha256,
    generate_document_id,
    persist_file,
)
from app.services.validation import validate_upload
from app.services.validator import validate_invoice

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/invoices", tags=["Invoices"])

_MAX_BATCH = 10


# ── Helpers ────────────────────────────────────────────────────────────────────

def _row_to_metadata(row) -> DocumentMetadata:
    """Convert a sqlite3.Row from the documents table to a DocumentMetadata."""
    return DocumentMetadata(
        document_id=row["document_id"],
        original_filename=row["original_filename"],
        stored_filename=row["stored_filename"],
        mime_type=row["mime_type"],
        file_size=row["file_size"],
        sha256_hash=row["sha256_hash"],
        page_count=row["page_count"],
        width_px=row["width_px"],
        height_px=row["height_px"],
        upload_timestamp=row["upload_timestamp"],
        batch_id=row["batch_id"],
        status=row["status"],
        duplicate_of=row["duplicate_of"],
    )


def _process_one(
    data: bytes,
    original_filename: str,
    batch_id: Optional[str] = None,
) -> tuple:
    """
    Full pipeline for a single uploaded file:
      validate → hash → duplicate-check → store → record.

    Returns:
      (True,  DocumentMetadata)  on success (including duplicates)
      (False, error_string)      on validation or storage failure
    """
    # 1. Validate format, size, and content
    result = validate_upload(data, original_filename)
    if not result.valid:
        return False, result.error

    # 2. Compute content hash
    sha256 = compute_sha256(data)

    # 3. Duplicate detection — check against all non-failed records
    with get_db() as conn:
        existing = conn.execute(
            "SELECT document_id FROM documents WHERE sha256_hash = ? AND status != 'failed' LIMIT 1;",
            (sha256,),
        ).fetchone()

    if existing:
        # Record the duplicate attempt without writing the file again
        document_id = generate_document_id()
        stored_filename = build_stored_filename(document_id, result.extension)
        with get_db() as conn:
            conn.execute(
                """
                INSERT INTO documents (
                    document_id, original_filename, stored_filename,
                    stored_path, mime_type, file_size, sha256_hash,
                    page_count, width_px, height_px,
                    batch_id, status, duplicate_of
                ) VALUES (?, ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, 'duplicate', ?)
                """,
                (
                    document_id,
                    original_filename,
                    stored_filename,
                    result.mime_type,
                    len(data),
                    sha256,
                    result.page_count,
                    result.width_px,
                    result.height_px,
                    batch_id,
                    existing["document_id"],
                ),
            )
            conn.commit()
            row = conn.execute(
                "SELECT * FROM documents WHERE document_id = ?", (document_id,)
            ).fetchone()
        # Audit: file received but identified as cryptographic duplicate
        append_audit_event(document_id, "UPLOADED", {
            "original_filename": original_filename,
            "mime_type": result.mime_type,
            "file_size_bytes": len(data),
        })
        append_audit_event(document_id, "HASH_VERIFIED", {
            "sha256": sha256,
            "status": "DUPLICATE",
            "duplicate_of": existing["document_id"],
        })
        append_audit_event(document_id, "DUPLICATE_DETECTED", {
            "sha256": sha256,
            "original_document_id": existing["document_id"],
            "note": "File hash matches a previously processed submission; no file written to disk.",
        })
        return True, _row_to_metadata(row)

    # 4. Store the file on disk
    document_id = generate_document_id()
    try:
        stored_path = persist_file(data, document_id, result.extension)
    except Exception as exc:
        logger.exception("Storage error for '%s'", original_filename)
        return False, f"Storage error: {exc}"

    # 5. Record in the database
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO documents (
                document_id, original_filename, stored_filename,
                stored_path, mime_type, file_size, sha256_hash,
                page_count, width_px, height_px,
                batch_id, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'uploaded')
            """,
            (
                document_id,
                original_filename,
                stored_path.name,
                str(stored_path),
                result.mime_type,
                len(data),
                sha256,
                result.page_count,
                result.width_px,
                result.height_px,
                batch_id,
            ),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM documents WHERE document_id = ?", (document_id,)
        ).fetchone()

    logger.info(
        "Stored document %s — %s (%d bytes, sha256=%.12s…)",
        document_id, original_filename, len(data), sha256,
    )
    # Audit: fresh file stored and hash verified as unique
    append_audit_event(document_id, "UPLOADED", {
        "original_filename": original_filename,
        "mime_type": result.mime_type,
        "file_size_bytes": len(data),
        "batch_id": batch_id,
    })
    append_audit_event(document_id, "HASH_VERIFIED", {
        "sha256": sha256,
        "status": "UNIQUE",
        "stored_path": str(stored_path),
    })
    return True, _row_to_metadata(row)


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.post(
    "/upload",
    response_model=UploadSuccess,
    status_code=201,
    summary="Upload a single invoice",
)
async def upload_invoice(file: UploadFile = File(...)):
    """
    Upload one invoice document (PDF, JPEG, or PNG, max 50 MB).

    - Validates format via magic bytes, not the client-supplied MIME type.
    - Returns **201 Created** on success (including duplicate detection).
    - Returns **422 Unprocessable Entity** on validation failure.
    """
    filename = file.filename or "unknown"
    data = await file.read()

    success, outcome = _process_one(data, filename)
    if not success:
        raise HTTPException(status_code=422, detail=outcome)

    return UploadSuccess(success=True, document=outcome)


@router.post(
    "/batch-upload",
    response_model=BatchUploadResult,
    status_code=207,
    summary="Upload up to 10 invoices in one request",
)
async def batch_upload_invoices(files: List[UploadFile] = File(...)):
    """
    Upload up to 10 invoice documents in a single request.

    - Each file is processed independently; one failure does not stop others.
    - Always returns **207 Multi-Status** with per-file results and a summary.
    - Assigns a shared ``batch_id`` to all files in the batch.
    """
    if len(files) > _MAX_BATCH:
        raise HTTPException(
            status_code=400,
            detail=f"Maximum batch size is {_MAX_BATCH} files; received {len(files)}.",
        )
    if len(files) == 0:
        raise HTTPException(status_code=400, detail="Batch must contain at least one file.")

    batch_id = str(uuid.uuid4())
    results = []

    for upload in files:
        filename = upload.filename or "unknown"
        try:
            data = await upload.read()
            success, outcome = _process_one(data, filename, batch_id=batch_id)
            if success:
                results.append(UploadSuccess(success=True, document=outcome))
            else:
                results.append(UploadFailure(success=False, filename=filename, error=outcome))
        except Exception as exc:
            logger.exception("Unexpected error processing '%s'", filename)
            results.append(
                UploadFailure(success=False, filename=filename, error=str(exc))
            )

    succeeded = sum(1 for r in results if r.success)

    return BatchUploadResult(
        batch_id=batch_id,
        total=len(files),
        succeeded=succeeded,
        failed=len(files) - succeeded,
        results=results,
    )


@router.get(
    "",
    response_model=InvoiceListResponse,
    summary="List all invoices with validation and processing status",
)
@router.get(
    "/",
    response_model=InvoiceListResponse,
    include_in_schema=False,
)
async def list_invoices():
    """
    Retrieve all processed invoices joining documents with their parsed understanding results.
    """
    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT 
                d.document_id,
                d.original_filename,
                d.stored_filename,
                d.mime_type,
                d.file_size,
                d.page_count,
                d.upload_timestamp,
                p.invoice_number,
                p.invoice_date,
                p.vendor_name,
                p.total_amount,
                COALESCE(p.validation_status, 'PENDING') AS validation_status,
                p.validation_reasoning,
                p.created_at AS parsed_at
            FROM documents d
            LEFT JOIN invoice_parsed_results p ON d.document_id = p.document_id
            WHERE d.status != 'failed'
            ORDER BY d.upload_timestamp DESC;
            """
        ).fetchall()

    invoices = [
        InvoiceListItem(
            document_id=r["document_id"],
            original_filename=r["original_filename"],
            stored_filename=r["stored_filename"],
            mime_type=r["mime_type"],
            file_size=r["file_size"],
            page_count=r["page_count"],
            upload_timestamp=r["upload_timestamp"],
            invoice_number=r["invoice_number"],
            invoice_date=r["invoice_date"],
            vendor_name=r["vendor_name"],
            total_amount=r["total_amount"],
            validation_status=r["validation_status"],
            validation_reasoning=r["validation_reasoning"],
            parsed_at=r["parsed_at"],
        )
        for r in rows
    ]
    return InvoiceListResponse(total=len(invoices), invoices=invoices)


@router.get(
    "/{document_id}",
    response_model=DocumentMetadata,
    summary="Retrieve document metadata",
)
async def get_invoice(document_id: str):
    """
    Retrieve full metadata for a previously uploaded document by its ``document_id``.

    Returns **404 Not Found** if the ID does not exist.
    """
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM documents WHERE document_id = ?",
            (document_id,),
        ).fetchone()

    if row is None:
        raise HTTPException(
            status_code=404,
            detail=f"Document '{document_id}' not found.",
        )

    return _row_to_metadata(row)


@router.post(
    "/{document_id}/analyze",
    response_model=DocumentAnalysisResponse,
    summary="Analyze document pages and determine extraction routes",
)
async def analyze_invoice(document_id: str):
    """
    Inspect every page of the document to determine how it should be processed.

    - PDF pages with meaningful selectable text: marked for BOTH native-text and visual routes.
    - PDF pages without meaningful text (scanned): marked for visual extraction only.
    - Images (JPEG/PNG): marked for visual extraction only.
    - Idempotent: repeated analysis replaces previous page records cleanly.
    """
    with get_db() as conn:
        doc = conn.execute(
            "SELECT * FROM documents WHERE document_id = ?",
            (document_id,),
        ).fetchone()

    if doc is None:
        raise HTTPException(
            status_code=404,
            detail=f"Document '{document_id}' not found.",
        )

    # Resolve physical file path (if duplicate, use the source document's file)
    stored_path_str = doc["stored_path"]
    mime_type = doc["mime_type"]
    if not stored_path_str and doc["duplicate_of"]:
        with get_db() as conn:
            orig = conn.execute(
                "SELECT stored_path, mime_type FROM documents WHERE document_id = ?",
                (doc["duplicate_of"],),
            ).fetchone()
            if orig and orig["stored_path"]:
                stored_path_str = orig["stored_path"]
                mime_type = orig["mime_type"]

    if not stored_path_str:
        raise HTTPException(
            status_code=404,
            detail=f"No stored file found for document '{document_id}'.",
        )

    file_path = Path(stored_path_str)
    if not file_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Stored file '{file_path.name}' is missing on disk.",
        )

    # Perform page detection
    try:
        pages = analyze_document_pages(
            document_id=document_id,
            file_path=file_path,
            mime_type=mime_type,
        )
    except Exception as exc:
        logger.exception("Failed to analyze document '%s'", document_id)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to analyze document: {exc}",
        )

    # Persist page analysis atomically (sensible re-analysis/idempotent update strategy)
    with get_db() as conn:
        conn.execute("DELETE FROM document_pages WHERE document_id = ?", (document_id,))
        for p in pages:
            conn.execute(
                """
                INSERT INTO document_pages (
                    document_id, page_number, page_type, has_selectable_text,
                    extracted_native_text, text_blocks, extraction_routes,
                    page_dimensions, detection_status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    p.document_id,
                    p.page_number,
                    p.page_type,
                    1 if p.has_selectable_text else 0,
                    p.extracted_native_text,
                    json.dumps(p.text_blocks),
                    json.dumps(p.extraction_routes),
                    json.dumps(p.page_dimensions),
                    p.detection_status,
                ),
            )
        conn.commit()

    # Build response
    page_results: List[PageAnalysisResult] = []
    digital_count = 0
    scanned_count = 0
    image_count = 0

    for p in pages:
        if p.page_type == "digital":
            digital_count += 1
        elif p.page_type == "scanned":
            scanned_count += 1
        elif p.page_type == "image":
            image_count += 1

        blocks = [
            TextBlockSchema(bbox=b["bbox"], text=b["text"])
            for b in p.text_blocks
        ]
        dims = PageDimensions(
            width=p.page_dimensions["width"],
            height=p.page_dimensions["height"],
        )
        page_results.append(
            PageAnalysisResult(
                document_id=p.document_id,
                page_number=p.page_number,
                page_type=p.page_type,
                has_selectable_text=p.has_selectable_text,
                extracted_native_text=p.extracted_native_text,
                text_blocks=blocks,
                extraction_routes=p.extraction_routes,
                page_dimensions=dims,
                detection_status=p.detection_status,
            )
        )

    # Audit: page detection and routing analyzed
    append_audit_event(document_id, "PAGE_ANALYZED", {
        "total_pages": len(pages),
        "digital_pages": digital_count,
        "scanned_pages": scanned_count,
        "image_pages": image_count,
        "routes": [p.extraction_routes for p in pages],
    })

    return DocumentAnalysisResponse(
        document_id=document_id,
        total_pages=len(pages),
        digital_pages=digital_count,
        scanned_pages=scanned_count,
        image_pages=image_count,
        pages=page_results,
    )


@router.post(
    "/{document_id}/read",
    response_model=DocumentReadingResponse,
    summary="Read document pages with Cloud Vision / Document Reader",
)
async def read_invoice(document_id: str):
    """
    Read all pages of a document and extract tokens, tables, and key-values into MVP Reading IR.

    - Verifies the document exists in `documents`.
    - Automatically triggers page detection / routing (Stage 3) if not already run.
    - Renders each page to image bytes and sends to the configured Vision API.
    - Persists results to `page_reading_results` with raw JSON and extraction statistics.
    """
    with get_db() as conn:
        doc = conn.execute(
            "SELECT * FROM documents WHERE document_id = ?",
            (document_id,),
        ).fetchone()

    if doc is None:
        raise HTTPException(
            status_code=404,
            detail=f"Document '{document_id}' not found.",
        )

    # Resolve physical file path (if duplicate, use the source document's file)
    stored_path_str = doc["stored_path"]
    mime_type = doc["mime_type"]
    if not stored_path_str and doc["duplicate_of"]:
        with get_db() as conn:
            orig = conn.execute(
                "SELECT stored_path, mime_type FROM documents WHERE document_id = ?",
                (doc["duplicate_of"],),
            ).fetchone()
            if orig and orig["stored_path"]:
                stored_path_str = orig["stored_path"]
                mime_type = orig["mime_type"]

    if not stored_path_str:
        raise HTTPException(
            status_code=404,
            detail=f"No stored file found for document '{document_id}'.",
        )

    file_path = Path(stored_path_str)
    if not file_path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"Stored file '{file_path.name}' is missing on disk.",
        )

    # Check if document_pages have been analyzed; if not, run analysis first
    with get_db() as conn:
        page_rows = conn.execute(
            "SELECT page_number FROM document_pages WHERE document_id = ? ORDER BY page_number ASC",
            (document_id,),
        ).fetchall()

    if not page_rows:
        # Run page analysis automatically
        pages = analyze_document_pages(
            document_id=document_id,
            file_path=file_path,
            mime_type=mime_type,
        )
        with get_db() as conn:
            conn.execute("DELETE FROM document_pages WHERE document_id = ?", (document_id,))
            for p in pages:
                conn.execute(
                    """
                    INSERT INTO document_pages (
                        document_id, page_number, page_type, has_selectable_text,
                        extracted_native_text, text_blocks, extraction_routes,
                        page_dimensions, detection_status
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        p.document_id,
                        p.page_number,
                        p.page_type,
                        1 if p.has_selectable_text else 0,
                        p.extracted_native_text,
                        json.dumps(p.text_blocks),
                        json.dumps(p.extraction_routes),
                        json.dumps(p.page_dimensions),
                        p.detection_status,
                    ),
                )
            conn.commit()
            page_rows = conn.execute(
                "SELECT page_number FROM document_pages WHERE document_id = ? ORDER BY page_number ASC",
                (document_id,),
            ).fetchall()

    reading_results: List[PageReadingResultSchema] = []

    for row in page_rows:
        page_num = row["page_number"]
        try:
            mvp_reading, engine, version, exec_time = read_page(
                file_path=file_path,
                mime_type=mime_type,
                page_number=page_num,
            )
            raw_json_str = mvp_reading.model_dump_json()

            # Persist to database (atomic upsert)
            with get_db() as conn:
                conn.execute(
                    """
                    INSERT INTO page_reading_results (
                        document_id, page_number, reading_engine, engine_version,
                        status, raw_json_output, tokens_count, tables_count,
                        raw_key_values_count, execution_time_ms
                    ) VALUES (?, ?, ?, ?, 'completed', ?, ?, ?, ?, ?)
                    ON CONFLICT(document_id, page_number) DO UPDATE SET
                        reading_engine=excluded.reading_engine,
                        engine_version=excluded.engine_version,
                        status=excluded.status,
                        raw_json_output=excluded.raw_json_output,
                        tokens_count=excluded.tokens_count,
                        tables_count=excluded.tables_count,
                        raw_key_values_count=excluded.raw_key_values_count,
                        execution_time_ms=excluded.execution_time_ms,
                        created_at=strftime('%Y-%m-%dT%H:%M:%SZ', 'now');
                    """,
                    (
                        document_id,
                        page_num,
                        engine,
                        version,
                        raw_json_str,
                        len(mvp_reading.tokens),
                        len(mvp_reading.tables),
                        len(mvp_reading.raw_key_values),
                        exec_time,
                    ),
                )
                conn.commit()

                res_row = conn.execute(
                    "SELECT * FROM page_reading_results WHERE document_id = ? AND page_number = ?",
                    (document_id, page_num),
                ).fetchone()

            reading_results.append(
                PageReadingResultSchema(
                    document_id=document_id,
                    page_number=page_num,
                    reading_engine=engine,
                    engine_version=version,
                    status=res_row["status"],
                    raw_json_output=mvp_reading,
                    tokens_count=len(mvp_reading.tokens),
                    tables_count=len(mvp_reading.tables),
                    raw_key_values_count=len(mvp_reading.raw_key_values),
                    execution_time_ms=exec_time,
                    created_at=res_row["created_at"],
                )
            )

        except Exception as exc:
            logger.exception("Failed reading page %d for document %s", page_num, document_id)
            raise HTTPException(
                status_code=500,
                detail=f"Failed reading page {page_num}: {exc}",
            )

    # Audit: vision reading and IR extraction complete
    engines_used = sorted(list({r.reading_engine for r in reading_results}))
    total_tokens = sum(r.tokens_count for r in reading_results)
    total_tables = sum(r.tables_count for r in reading_results)
    total_kvs = sum(r.raw_key_values_count for r in reading_results)
    append_audit_event(document_id, "VISION_READ", {
        "pages_read": len(reading_results),
        "engines": engines_used,
        "total_tokens": total_tokens,
        "total_tables": total_tables,
        "total_key_values": total_kvs,
    })

    return DocumentReadingResponse(
        document_id=document_id,
        total_pages_read=len(reading_results),
        pages=reading_results,
    )


@router.post(
    "/{document_id}/understand",
    response_model=InvoiceUnderstandingResponse,
    summary="Understand and validate invoice (Stage 5)",
)
async def understand_invoice(document_id: str):
    """
    Stage 5: Invoice Understanding & Validation.

    - Checks document existence in `documents`.
    - If `page_reading_results` do not exist, runs `read_invoice` first.
    - Aggregates reading results across pages and maps them to structured business fields:
      `invoice_number`, `invoice_date`, `vendor_name`, `total_amount`, and `line_items`.
    - Executes the validation engine against completeness and PO master database rules.
    - Persists results to `invoice_parsed_results`.
    - Returns structured invoice data and auditable approval decision.
    """
    with get_db() as conn:
        doc = conn.execute(
            "SELECT * FROM documents WHERE document_id = ?",
            (document_id,),
        ).fetchone()

    if doc is None:
        raise HTTPException(
            status_code=404,
            detail=f"Document '{document_id}' not found.",
        )

    # Check if page reading results exist; if not, invoke Stage 4 reading first
    with get_db() as conn:
        reading_rows = conn.execute(
            "SELECT COUNT(*) as count FROM page_reading_results WHERE document_id = ?",
            (document_id,),
        ).fetchone()

    if not reading_rows or reading_rows["count"] == 0:
        await read_invoice(document_id)

    # Parse reading outputs into structured business fields
    try:
        parsed_data = parse_invoice_for_document(document_id)
    except Exception as exc:
        logger.exception("Failed parsing invoice for document %s", document_id)
        raise HTTPException(
            status_code=500,
            detail=f"Failed parsing invoice data: {exc}",
        )

    # Check for cryptographic duplicate detection (SHA-256)
    duplicate_info = None
    with get_db() as conn:
        if doc["duplicate_of"]:
            orig = conn.execute(
                "SELECT original_filename, upload_timestamp FROM documents WHERE document_id = ?",
                (doc["duplicate_of"],),
            ).fetchone()
            if orig:
                duplicate_info = {
                    "original_filename": orig["original_filename"],
                    "upload_timestamp": orig["upload_timestamp"],
                }
        elif doc["sha256_hash"]:
            orig = conn.execute(
                "SELECT original_filename, upload_timestamp FROM documents WHERE sha256_hash = ? AND document_id != ? AND id < ? ORDER BY id ASC LIMIT 1",
                (doc["sha256_hash"], document_id, doc["id"]),
            ).fetchone()
            if orig:
                duplicate_info = {
                    "original_filename": orig["original_filename"],
                    "upload_timestamp": orig["upload_timestamp"],
                }

    # Run validation engine
    validation_status, validation_reasoning = validate_invoice(parsed_data, duplicate_info=duplicate_info)

    line_items_json = json.dumps([item.model_dump() for item in parsed_data.line_items])

    # Persist to database (atomic upsert)
    with get_db() as conn:
        conn.execute(
            """
            INSERT INTO invoice_parsed_results (
                document_id, invoice_number, invoice_date, vendor_name,
                total_amount, line_items, validation_status, validation_reasoning
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(document_id) DO UPDATE SET
                invoice_number=excluded.invoice_number,
                invoice_date=excluded.invoice_date,
                vendor_name=excluded.vendor_name,
                total_amount=excluded.total_amount,
                line_items=excluded.line_items,
                validation_status=excluded.validation_status,
                validation_reasoning=excluded.validation_reasoning,
                created_at=strftime('%Y-%m-%dT%H:%M:%SZ', 'now');
            """,
            (
                document_id,
                parsed_data.invoice_number,
                parsed_data.invoice_date,
                parsed_data.vendor_name,
                parsed_data.total_amount,
                line_items_json,
                validation_status,
                validation_reasoning,
            ),
        )
        conn.commit()

        row = conn.execute(
            "SELECT * FROM invoice_parsed_results WHERE document_id = ?",
            (document_id,),
        ).fetchone()

    # Audit: Stage 5 validation decision and parsed fields recorded
    append_audit_event(document_id, "VALIDATED", {
        "validation_status": validation_status,
        "validation_reasoning": validation_reasoning,
        "invoice_number": parsed_data.invoice_number,
        "vendor_name": parsed_data.vendor_name,
        "total_amount": parsed_data.total_amount,
        "line_items_count": len(parsed_data.line_items),
        "is_duplicate": duplicate_info is not None,
    })

    return InvoiceUnderstandingResponse(
        document_id=document_id,
        invoice_number=row["invoice_number"],
        invoice_date=row["invoice_date"],
        vendor_name=row["vendor_name"],
        total_amount=row["total_amount"],
        line_items=parsed_data.line_items,
        validation_status=row["validation_status"],
        validation_reasoning=row["validation_reasoning"],
        created_at=row["created_at"],
    )


@router.get(
    "/{document_id}/understand",
    response_model=InvoiceUnderstandingResponse,
    summary="Get invoice understanding results",
)
async def get_invoice_understanding(document_id: str):
    """Retrieve existing Stage 5 understanding & validation results for a document."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM invoice_parsed_results WHERE document_id = ?",
            (document_id,),
        ).fetchone()

    if row is None:
        raise HTTPException(
            status_code=404,
            detail=f"Understanding results not found for document '{document_id}'. Run POST /api/invoices/{document_id}/understand first.",
        )

    try:
        raw_items = json.loads(row["line_items"]) if row["line_items"] else []
        items = [LineItemSchema(**item) for item in raw_items]
    except Exception:
        items = []

    return InvoiceUnderstandingResponse(
        document_id=document_id,
        invoice_number=row["invoice_number"],
        invoice_date=row["invoice_date"],
        vendor_name=row["vendor_name"],
        total_amount=row["total_amount"],
        line_items=items,
        validation_status=row["validation_status"],
        validation_reasoning=row["validation_reasoning"],
        created_at=row["created_at"],
    )


@router.get(
    "/{document_id}/audit-trail",
    response_model=AuditTrailResponse,
    summary="Get immutable lifecycle audit trail for an invoice",
)
async def get_invoice_audit_trail(document_id: str):
    """
    Retrieve the complete, immutable lifecycle audit trail for a document.

    Returns all state transitions recorded in chronological order:
    - UPLOADED
    - HASH_VERIFIED
    - DUPLICATE_DETECTED (if applicable)
    - PAGE_ANALYZED
    - VISION_READ
    - VALIDATED

    Returns HTTP 404 if the document_id does not exist.
    """
    with get_db() as conn:
        doc = conn.execute(
            "SELECT document_id FROM documents WHERE document_id = ?",
            (document_id,),
        ).fetchone()

    if doc is None:
        raise HTTPException(
            status_code=404,
            detail=f"Document '{document_id}' not found.",
        )

    with get_db() as conn:
        rows = conn.execute(
            """
            SELECT audit_id, document_id, event_type, details, created_at
            FROM invoice_audit_trail
            WHERE document_id = ?
            ORDER BY audit_id ASC;
            """,
            (document_id,),
        ).fetchall()

    events: List[AuditTrailEntry] = []
    for r in rows:
        try:
            details_dict = json.loads(r["details"]) if r["details"] else {}
        except Exception:
            details_dict = {"raw": r["details"]}

        events.append(
            AuditTrailEntry(
                audit_id=r["audit_id"],
                document_id=r["document_id"],
                event_type=r["event_type"],
                details=details_dict,
                created_at=r["created_at"],
            )
        )

    return AuditTrailResponse(
        document_id=document_id,
        total_events=len(events),
        events=events,
    )
