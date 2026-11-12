"""
validation.py — File format, integrity, and content validation.

Validation pipeline:
  1. Size check (max 50 MB).
  2. Magic-byte format detection — trusts file content, NOT client MIME type or extension.
  3. Deep content validation via pymupdf (PDFs) and Pillow (images).

No OCR or content extraction happens here.
"""

import io
import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)

# ── Magic byte signatures ──────────────────────────────────────────────────────
_MAGIC_PDF  = b"%PDF"
_MAGIC_JPEG = b"\xFF\xD8\xFF"
_MAGIC_PNG  = b"\x89PNG\r\n\x1a\n"

# ── Limits ─────────────────────────────────────────────────────────────────────
MAX_FILE_SIZE_BYTES = 50 * 1024 * 1024   # 50 MB
MAX_PDF_PAGES       = 30
MAX_IMAGE_DIMENSION = 10_000              # pixels (width or height)


# ── Result ─────────────────────────────────────────────────────────────────────

@dataclass
class ValidationResult:
    """Outcome of a file validation attempt."""
    valid:      bool
    mime_type:  Optional[str] = None
    extension:  Optional[str] = None   # 'pdf' | 'jpg' | 'png'
    page_count: Optional[int] = None   # PDFs only
    width_px:   Optional[int] = None   # images only
    height_px:  Optional[int] = None   # images only
    error:      Optional[str] = None


# ── Public API ─────────────────────────────────────────────────────────────────

def validate_upload(data: bytes, original_filename: str) -> ValidationResult:
    """
    Run the full validation pipeline on raw file bytes.

    Returns a ValidationResult with valid=True and populated metadata on success,
    or valid=False and an error message on any failure.
    """
    # 1. Size
    size = len(data)
    if size == 0:
        return ValidationResult(valid=False, error="File is empty.")
    if size > MAX_FILE_SIZE_BYTES:
        mb = size / (1024 * 1024)
        return ValidationResult(
            valid=False,
            error=f"File size {mb:.1f} MB exceeds the 50 MB limit.",
        )

    # 2. Format detection via magic bytes (ignores client-supplied MIME/extension)
    mime_type = _detect_mime(data)
    if mime_type is None:
        return ValidationResult(
            valid=False,
            error=(
                "Unsupported or unrecognised file format. "
                "Accepted formats: PDF, JPEG, PNG."
            ),
        )

    # 3. Deep content validation
    if mime_type == "application/pdf":
        return _validate_pdf(data, mime_type)
    return _validate_image(data, mime_type)


# ── Internal helpers ───────────────────────────────────────────────────────────

def _detect_mime(data: bytes) -> Optional[str]:
    """Infer MIME type from magic bytes; returns None if unrecognised."""
    if data[:4] == _MAGIC_PDF:   return "application/pdf"
    if data[:3] == _MAGIC_JPEG:  return "image/jpeg"
    if data[:8] == _MAGIC_PNG:   return "image/png"
    if data[:4] == b"RIFF" and len(data) >= 12 and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def _validate_pdf(data: bytes, mime_type: str) -> ValidationResult:
    """Deep PDF validation using pymupdf."""
    try:
        import pymupdf  # noqa: PLC0415
    except ImportError:
        return ValidationResult(
            valid=False,
            error="pymupdf is not installed — required for PDF validation.",
        )

    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
        page_count = len(doc)
        doc.close()
    except Exception as exc:
        logger.warning("PDF validation error for '%s': %s", "upload", exc)
        return ValidationResult(valid=False, error=f"Corrupted or invalid PDF: {exc}")

    if page_count == 0:
        return ValidationResult(valid=False, error="PDF contains no pages.")
    if page_count > MAX_PDF_PAGES:
        return ValidationResult(
            valid=False,
            error=f"PDF has {page_count} pages; maximum allowed is {MAX_PDF_PAGES}.",
        )

    return ValidationResult(
        valid=True,
        mime_type=mime_type,
        extension="pdf",
        page_count=page_count,
    )


def _validate_image(data: bytes, mime_type: str) -> ValidationResult:
    """Deep image validation using Pillow."""
    try:
        from PIL import Image, UnidentifiedImageError  # noqa: PLC0415
    except ImportError:
        return ValidationResult(
            valid=False,
            error="Pillow is not installed — required for image validation.",
        )

    ext = "jpg" if mime_type == "image/jpeg" else ("png" if mime_type == "image/png" else "webp")

    try:
        # verify() checks for corruption but exhausts the stream — re-open afterwards.
        Image.open(io.BytesIO(data)).verify()
        img = Image.open(io.BytesIO(data))
        width, height = img.size
        img.close()
    except (UnidentifiedImageError, Exception) as exc:
        logger.warning("Image validation error: %s", exc)
        return ValidationResult(valid=False, error=f"Corrupted or invalid image: {exc}")

    if width > MAX_IMAGE_DIMENSION or height > MAX_IMAGE_DIMENSION:
        return ValidationResult(
            valid=False,
            error=(
                f"Image dimensions {width}\u00d7{height} px exceed the "
                f"{MAX_IMAGE_DIMENSION}\u00d7{MAX_IMAGE_DIMENSION} px limit."
            ),
        )

    return ValidationResult(
        valid=True,
        mime_type=mime_type,
        extension=ext,
        width_px=width,
        height_px=height,
    )
