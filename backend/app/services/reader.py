"""
reader.py — Stage 4 Document Reading Service (MVP Mode).

Responsibilities:
  - Render PDF and image pages to PNG image bytes for visual inspection.
  - Send page images to a Cloud Vision API (Google Gemini 1.5 Flash or OpenAI GPT-4o).
  - Parse and normalize the response into the simplified MVP Reading IR schema:
      - page_number
      - tokens: [{"text": str, "bbox": [x1, y1, x2, y2]}]
      - tables: [{"table_index": int, "rows": int, "columns": int, "headers": [...], "cells": [...]}]
      - raw_key_values: {key: value}
  - Provide fallback / mock mode when API keys are not configured or in tests.
"""

from __future__ import annotations

import base64
import io
import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image
import pymupdf

from app.config import GEMINI_API_KEY, OPENAI_API_KEY
from app.schemas.invoice import (
    MVPPageReading,
    MVPTable,
    MVPTableCell,
    MVPToken,
)

logger = logging.getLogger(__name__)

READING_PROMPT = """You are an expert document reading vision system specialized in Accounts Payable and financial document extraction.
Inspect this invoice or financial document page carefully and extract its content verbatim.

Return ONLY a raw JSON object (no markdown code blocks, no ```json wrapper) matching this exact schema:
{
  "page_number": <int>,
  "invoice_summary": {
    "vendor_name": "<string or null>",
    "invoice_number": "<string or null>",
    "invoice_date": "<string or null>",
    "currency": "<ISO 3-letter currency code e.g. USD, GBP, INR or null>",
    "subtotal": <float or null>,
    "tax_amount": <float or null>,
    "total_amount": <float or null>
  },
  "line_items": [
    {
      "description": "<string>",
      "quantity": <float>,
      "unit_price": <float>,
      "total": <float>
    }
  ],
  "tokens": [
    {"text": "<string>", "bbox": [x1, y1, x2, y2]}
  ],
  "tables": [
    {
      "table_index": 1,
      "rows": <int>,
      "columns": <int>,
      "headers": ["<header1>", "<header2>"],
      "cells": [
        {"row_index": 1, "col_index": 1, "text": "<cell text>", "bbox": [x1, y1, x2, y2]}
      ]
    }
  ],
  "raw_key_values": {
    "<key>": "<verbatim value>"
  }
}

Rules:
- Keep all token text, table headers, cell texts, and key-values completely verbatim.
- Bounding boxes [x1, y1, x2, y2] should be normalized to the 0-1000 coordinate range where (0,0) is top-left and (1000,1000) is bottom-right.
- 'invoice_summary': Accurately extract core financial fields required for 3-way matching and PO validation. If a field is missing, set it to null.
- 'line_items': Extract all itemized rows explicitly to support granular line-item 3-way matching.
- 'raw_key_values': Capture all other explicit labeled key-value pairs printed on the page.
- Output MUST be valid JSON only. Do not include conversational markdown or explanation outside the JSON.
"""


# ── Page Rendering ─────────────────────────────────────────────────────────────

def render_page_to_png(file_path: Path, mime_type: str, page_number: int) -> bytes:
    """
    Render a single page from a PDF or image file into PNG bytes.
    page_number is 1-based.
    """
    if not file_path.exists():
        raise FileNotFoundError(f"File not found on disk: {file_path}")

    if mime_type == "application/pdf":
        doc = pymupdf.open(str(file_path))
        try:
            if page_number < 1 or page_number > len(doc):
                raise ValueError(
                    f"Requested page {page_number} exceeds document page count {len(doc)}"
                )
            page = doc[page_number - 1]
            # Render at 150 DPI for sharp OCR while keeping payload size reasonable
            pix = page.get_pixmap(dpi=150)
            return pix.tobytes("png")
        finally:
            doc.close()

    elif mime_type in ("image/jpeg", "image/png", "image/webp"):
        with Image.open(str(file_path)) as img:
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            return buf.getvalue()

    else:
        raise ValueError(f"Unsupported MIME type for page rendering: {mime_type}")


# ── Cloud Vision API Execution ─────────────────────────────────────────────────

def call_gemini_vision(image_bytes: bytes, page_number: int, api_key: str) -> Tuple[Dict[str, Any], str, str]:
    """Call Google Gemini Vision via official google-genai SDK."""
    from google import genai
    from google.genai import types

    client = genai.Client(api_key=api_key)
    model_name = os.getenv("GEMINI_MODEL", "gemini-3.5-flash")
    engine_version = "google-genai-2.33.0"

    try:
        response = client.models.generate_content(
            model=model_name,
            contents=[
                types.Part.from_bytes(data=image_bytes, mime_type="image/png"),
                READING_PROMPT,
            ],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.0,
            ),
        )
    except Exception as exc:
        logger.warning("Gemini model %s failed (%s); trying fallback gemini-3.5-flash-lite", model_name, exc)
        model_name = "gemini-3.5-flash-lite"
        response = client.models.generate_content(
            model=model_name,
            contents=[
                types.Part.from_bytes(data=image_bytes, mime_type="image/png"),
                READING_PROMPT,
            ],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.0,
            ),
        )

    raw_text = (response.text or "{}").strip()
    # Strip markdown formatting if model wrapped output in backticks
    if raw_text.startswith("```json"):
        raw_text = raw_text[7:]
    if raw_text.startswith("```"):
        raw_text = raw_text[3:]
    if raw_text.endswith("```"):
        raw_text = raw_text[:-3]
    raw_text = raw_text.strip()

    data = json.loads(raw_text)
    data["page_number"] = page_number
    return data, model_name, engine_version


def call_openai_vision(image_bytes: bytes, page_number: int, api_key: str) -> Tuple[Dict[str, Any], str, str]:
    """Call OpenAI GPT-4o Vision API via openai SDK."""
    from openai import OpenAI

    client = OpenAI(api_key=api_key)
    engine_name = "gpt-4o"
    engine_version = "openai-3.49.0"

    base64_image = base64.b64encode(image_bytes).decode("utf-8")
    response = client.chat.completions.create(
        model=engine_name,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": READING_PROMPT},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/png;base64,{base64_image}",
                            "detail": "high",
                        },
                    },
                ],
            }
        ],
        response_format={"type": "json_object"},
        temperature=0.0,
    )

    raw_text = (response.choices[0].message.content or "{}").strip()
    if raw_text.startswith("```json"):
        raw_text = raw_text[7:]
    if raw_text.startswith("```"):
        raw_text = raw_text[3:]
    if raw_text.endswith("```"):
        raw_text = raw_text[:-3]
    raw_text = raw_text.strip()

    data = json.loads(raw_text)
    data["page_number"] = page_number
    return data, engine_name, engine_version


def call_fallback_reader(
    file_path: Path,
    mime_type: str,
    page_number: int,
    image_bytes: Optional[bytes] = None,
) -> Tuple[Dict[str, Any], str, str]:
    """
    Deterministic native fallback reader when no cloud API keys are present.
    - For digital PDFs with selectable vector text: extracts tokens and lines from PyMuPDF.
    - For images (PNG, JPEG, etc.) and scanned PDFs: executes local RapidOCR on image bytes.
    """
    engine_name = "native-vector-reader"
    engine_version = f"pymupdf-{pymupdf.__version__}"

    tokens: List[Dict[str, Any]] = []
    tables: List[Dict[str, Any]] = []
    raw_key_values: Dict[str, str] = {}

    if mime_type == "application/pdf":
        doc = pymupdf.open(str(file_path))
        try:
            if 1 <= page_number <= len(doc):
                page = doc[page_number - 1]
                w = page.rect.width or 595.0
                h = page.rect.height or 842.0

                # Extract words: (x0, y0, x1, y1, word, block_no, line_no, word_no)
                words = page.get_text("words")
                for item in words:
                    x0, y0, x1, y1, text = item[0], item[1], item[2], item[3], item[4]
                    norm_bbox = [
                        round((x0 / w) * 1000, 1),
                        round((y0 / h) * 1000, 1),
                        round((x1 / w) * 1000, 1),
                        round((y1 / h) * 1000, 1),
                    ]
                    tokens.append({"text": text, "bbox": norm_bbox})

                # Extract simple key-values from common patterns
                full_text = page.get_text()
                for line in full_text.splitlines():
                    if ":" in line:
                        k, v = line.split(":", 1)
                        k_clean = k.strip()
                        v_clean = v.strip()
                        if k_clean and v_clean and len(k_clean) < 40:
                            raw_key_values[k_clean] = v_clean

        finally:
            doc.close()

    # For images or scanned documents where vector text is empty or sparse, run local RapidOCR
    if mime_type != "application/pdf" or len(tokens) < 5:
        try:
            import numpy as np
            from rapidocr_onnxruntime import RapidOCR
            ocr = RapidOCR()

            img_w, img_h = 1000.0, 1000.0
            if image_bytes is not None:
                try:
                    with Image.open(io.BytesIO(image_bytes)) as pil_img:
                        img_w, img_h = float(pil_img.width), float(pil_img.height)
                        ocr_target = np.array(pil_img.convert("RGB"))
                except Exception:
                    ocr_target = str(file_path)
            else:
                try:
                    with Image.open(str(file_path)) as pil_img:
                        img_w, img_h = float(pil_img.width), float(pil_img.height)
                        ocr_target = np.array(pil_img.convert("RGB"))
                except Exception:
                    ocr_target = str(file_path)

            ocr_res, _ = ocr(ocr_target)
            if ocr_res:
                engine_name = "rapidocr-onnx"
                engine_version = "v1.4.4"

                lines = []
                for item in ocr_res:
                    quad_box, text, conf = item[0], item[1].strip(), item[2]
                    if not text:
                        continue
                    lines.append(text)
                    xs = [pt[0] for pt in quad_box]
                    ys = [pt[1] for pt in quad_box]
                    min_x, max_x = min(xs), max(xs)
                    min_y, max_y = min(ys), max(ys)
                    norm_bbox = [
                        round((min_x / img_w) * 1000, 1),
                        round((min_y / img_h) * 1000, 1),
                        round((max_x / img_w) * 1000, 1),
                        round((max_y / img_h) * 1000, 1),
                    ]
                    tokens.append({"text": text, "bbox": norm_bbox})

                # Extract key-values from OCR lines
                for i, line in enumerate(lines):
                    if ":" in line or "：" in line:
                        parts = re.split(r"[:：]", line, maxsplit=1)
                        k_clean = parts[0].strip()
                        v_clean = parts[1].strip() if len(parts) > 1 else ""
                        if k_clean and v_clean and len(k_clean) < 40:
                            raw_key_values[k_clean] = v_clean
                        elif k_clean and not v_clean and i + 1 < len(lines):
                            next_l = lines[i + 1].strip()
                            if next_l and len(next_l) < 50:
                                raw_key_values[k_clean] = next_l
                    else:
                        lower_l = line.lower()
                        for trigger in ["invoice number", "invoice no", "inv no", "invoice#", "invoice #", "total amount", "total", "balance due", "invoice date"]:
                            if lower_l == trigger or lower_l.endswith(trigger):
                                if i + 1 < len(lines):
                                    val_next = lines[i + 1].strip()
                                    if val_next:
                                        raw_key_values[trigger.title()] = val_next
                                        break
        except Exception as exc:
            logger.warning("RapidOCR fallback encountered error: %s", exc)

    payload = {
        "page_number": page_number,
        "tokens": tokens,
        "tables": tables,
        "raw_key_values": raw_key_values,
    }
    return payload, engine_name, engine_version


# ── Unified Reading Entrypoint ─────────────────────────────────────────────────

# Optional mock hook for unit testing
_mock_reader_override = None


def set_mock_reader(fn):
    """Set a custom mock reader function for testing: fn(image_bytes, page_number) -> (dict, engine, version)."""
    global _mock_reader_override
    _mock_reader_override = fn


def reset_mock_reader():
    """Reset the mock reader override."""
    global _mock_reader_override
    _mock_reader_override = None


def read_page(
    file_path: Path,
    mime_type: str,
    page_number: int,
) -> Tuple[MVPPageReading, str, str, int]:
    """
    Read a single document page using the configured Cloud Vision provider.
    
    Order of provider selection:
      1. Mock reader override (if set via set_mock_reader).
      2. Google Gemini (if GEMINI_API_KEY is in environment).
      3. OpenAI (if OPENAI_API_KEY is in environment).
      4. Deterministic native vector reader / RapidOCR (fallback).

    Returns:
      (MVPPageReading, engine_name, engine_version, execution_time_ms)
    """
    t0 = time.perf_counter()

    # 1. Render page image
    image_bytes = render_page_to_png(file_path, mime_type, page_number)

    # 2. Call appropriate reader
    if _mock_reader_override is not None:
        raw_data, engine, version = _mock_reader_override(image_bytes, page_number)
    else:
        gemini_key = (os.getenv("GEMINI_API_KEY") or GEMINI_API_KEY or "").strip()
        openai_key = (os.getenv("OPENAI_API_KEY") or OPENAI_API_KEY or "").strip()

        if gemini_key:
            try:
                raw_data, engine, version = call_gemini_vision(image_bytes, page_number, gemini_key)
            except Exception as exc:
                logger.warning("Gemini Vision call failed (%s); falling back to native reader.", exc)
                raw_data, engine, version = call_fallback_reader(file_path, mime_type, page_number, image_bytes=image_bytes)
        elif openai_key:
            try:
                raw_data, engine, version = call_openai_vision(image_bytes, page_number, openai_key)
            except Exception as exc:
                logger.warning("OpenAI Vision call failed (%s); falling back to native reader.", exc)
                raw_data, engine, version = call_fallback_reader(file_path, mime_type, page_number, image_bytes=image_bytes)
        else:
            raw_data, engine, version = call_fallback_reader(file_path, mime_type, page_number, image_bytes=image_bytes)

    execution_time_ms = int((time.perf_counter() - t0) * 1000)

    # 3. Normalize into validated MVPPageReading Pydantic model
    tokens = [
        MVPToken(text=t.get("text", ""), bbox=t.get("bbox", [0.0, 0.0, 0.0, 0.0]))
        for t in raw_data.get("tokens", [])
        if t.get("text")
    ]

    tables: List[MVPTable] = []
    for tbl in raw_data.get("tables", []):
        cells = [
            MVPTableCell(
                row_index=c.get("row_index", 1),
                col_index=c.get("col_index", 1),
                text=c.get("text", ""),
                bbox=c.get("bbox"),
            )
            for c in tbl.get("cells", [])
        ]
        tables.append(
            MVPTable(
                table_index=tbl.get("table_index", 1),
                rows=tbl.get("rows", len(cells)),
                columns=tbl.get("columns", 1),
                headers=tbl.get("headers"),
                cells=cells,
            )
        )

    raw_key_values = raw_data.get("raw_key_values", {})
    if not isinstance(raw_key_values, dict):
        raw_key_values = {}

    reading = MVPPageReading(
        page_number=page_number,
        tokens=tokens,
        tables=tables,
        raw_key_values=raw_key_values,
    )

    return reading, engine, version, execution_time_ms
