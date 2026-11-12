"""
detector.py — Document inspection, page-level classification and extraction routing.

Inspects uploaded documents at the individual page level:
  - For PDF pages: detects whether meaningful selectable text exists using
    configurable character and word thresholds.
    - If meaningful text exists: extracts native text + bounding boxes,
      marks page for BOTH ["native_text", "visual"] extraction routes.
    - If no meaningful text: classifies as scanned, marks for ["visual"] only.
  - For image uploads (JPEG/PNG): classifies page as image, marks for ["visual"] only.
"""

import json
import logging
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, List, Optional, Any

import pymupdf
from PIL import Image

from app.config import (
    MEANINGFUL_TEXT_CHAR_THRESHOLD,
    MEANINGFUL_TEXT_WORD_THRESHOLD,
)

logger = logging.getLogger(__name__)


@dataclass
class TextBlock:
    """Bounding box and text content for a single text block."""
    bbox: List[float]  # [x0, y0, x1, y1]
    text: str


@dataclass
class PageAnalysis:
    """Detection and routing details for an individual page."""
    document_id: str
    page_number: int
    page_type: str  # 'digital' | 'scanned' | 'image'
    has_selectable_text: bool
    extracted_native_text: Optional[str]
    text_blocks: List[Dict[str, Any]]
    extraction_routes: List[str]
    page_dimensions: Dict[str, float]
    detection_status: str = "completed"


def is_meaningful_text(text: str) -> bool:
    """
    Determine if text contains enough genuine alphanumeric content
    to qualify as a digital document rather than stray artifacts/watermarks.
    """
    if not text:
        return False
    stripped = text.strip()
    char_count = sum(1 for c in stripped if not c.isspace())
    words = [w for w in stripped.split() if any(c.isalnum() for c in w)]
    return (
        char_count >= MEANINGFUL_TEXT_CHAR_THRESHOLD
        and len(words) >= MEANINGFUL_TEXT_WORD_THRESHOLD
    )


def analyze_document_pages(
    document_id: str,
    file_path: Path,
    mime_type: str,
) -> List[PageAnalysis]:
    """
    Inspect every page of the document and determine extraction routing.
    """
    if not file_path.exists():
        raise FileNotFoundError(f"File not found on disk: {file_path}")

    pages: List[PageAnalysis] = []

    if mime_type == "application/pdf":
        doc = pymupdf.open(str(file_path))
        try:
            for idx, page in enumerate(doc):
                page_num = idx + 1
                rect = page.rect
                dims = {
                    "width": round(rect.width, 2),
                    "height": round(rect.height, 2),
                }

                raw_text = page.get_text() or ""
                has_meaningful = is_meaningful_text(raw_text)

                if has_meaningful:
                    # Digital page: extract text and bounding blocks
                    blocks_raw = page.get_text("blocks") or []
                    parsed_blocks: List[Dict[str, Any]] = []
                    for b in blocks_raw:
                        # block tuple: (x0, y0, x1, y1, text, block_no, block_type)
                        # block_type 0 = text, 1 = image
                        if len(b) >= 7 and b[6] == 0:
                            b_text = b[4].strip()
                            if b_text:
                                parsed_blocks.append({
                                    "bbox": [
                                        round(b[0], 2),
                                        round(b[1], 2),
                                        round(b[2], 2),
                                        round(b[3], 2),
                                    ],
                                    "text": b_text,
                                })

                    pages.append(
                        PageAnalysis(
                            document_id=document_id,
                            page_number=page_num,
                            page_type="digital",
                            has_selectable_text=True,
                            extracted_native_text=raw_text,
                            text_blocks=parsed_blocks,
                            extraction_routes=["native_text", "visual"],
                            page_dimensions=dims,
                            detection_status="completed",
                        )
                    )
                else:
                    # Scanned page (or purely graphical PDF page)
                    pages.append(
                        PageAnalysis(
                            document_id=document_id,
                            page_number=page_num,
                            page_type="scanned",
                            has_selectable_text=False,
                            extracted_native_text=None,
                            text_blocks=[],
                            extraction_routes=["visual"],
                            page_dimensions=dims,
                            detection_status="completed",
                        )
                    )
        finally:
            doc.close()

    elif mime_type in ("image/jpeg", "image/png", "image/webp"):
        with Image.open(str(file_path)) as img:
            width, height = img.size
            dims = {"width": float(width), "height": float(height)}

        pages.append(
            PageAnalysis(
                document_id=document_id,
                page_number=1,
                page_type="image",
                has_selectable_text=False,
                extracted_native_text=None,
                text_blocks=[],
                extraction_routes=["visual"],
                page_dimensions=dims,
                detection_status="completed",
            )
        )
    else:
        raise ValueError(f"Unsupported MIME type for detection: {mime_type}")

    return pages
