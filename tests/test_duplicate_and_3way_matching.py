"""
tests/test_duplicate_and_3way_matching.py — Verify Cryptographic Duplicate Detection (SHA-256)
and Granular Line-Item 3-Way Matching.
"""

import io
import hashlib
import pymupdf
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.validator import (
    MOCK_PURCHASE_ORDERS,
    match_line_items,
    validate_invoice,
)
from app.schemas.invoice import LineItemSchema, ParsedInvoiceData

client = TestClient(app)


def test_validator_rule0_duplicate_rejection():
    """Validator Rule 0: Flags duplicate invoice as REJECTED with compliance note."""
    inv_parsed = ParsedInvoiceData(
        invoice_number="INV-DUP-001",
        invoice_date="2026-11-12",
        vendor_name="Zendesk, Inc.",
        total_amount=28.00,
    )
    dup_info = {
        "original_filename": "zendesk_august.pdf",
        "upload_timestamp": "2026-11-12T04:00:00Z",
    }
    status, reasoning = validate_invoice(inv_parsed, duplicate_info=dup_info)
    assert status == "REJECTED"
    assert "Duplicate invoice detected" in reasoning
    assert "zendesk_august.pdf" in reasoning


def test_api_cryptographic_duplicate_detection():
    """Full API pipeline detects identical SHA-256 and sets duplicate_of reference."""
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((50, 50), "Enterprise Duplicate Test Invoice Content Unique Salt 42")
    unique_payload = doc.tobytes()
    doc.close()

    expected_hash = hashlib.sha256(unique_payload).hexdigest()

    # Upload original
    res1 = client.post(
        "/api/invoices/upload",
        files={"file": ("original_invoice.pdf", io.BytesIO(unique_payload), "application/pdf")},
    )
    assert res1.status_code == 201
    doc1 = res1.json()["document"]
    doc1_id = doc1["document_id"]
    assert doc1["status"] == "uploaded"
    assert doc1["sha256_hash"] == expected_hash

    # Upload duplicate file (identical bytes)
    res2 = client.post(
        "/api/invoices/upload",
        files={"file": ("duplicate_submission.pdf", io.BytesIO(unique_payload), "application/pdf")},
    )
    assert res2.status_code == 201
    doc2 = res2.json()["document"]
    doc2_id = doc2["document_id"]
    assert doc2["status"] == "duplicate"
    assert doc2["duplicate_of"] == doc1_id

    # Run understand on duplicate document
    res_under = client.post(f"/api/invoices/{doc2_id}/understand")
    assert res_under.status_code == 200
    under_data = res_under.json()
    assert under_data["validation_status"] == "REJECTED"
    assert "Duplicate invoice detected" in under_data["validation_reasoning"]
    assert "original_invoice.pdf" in under_data["validation_reasoning"]


def test_3way_line_item_matching_clean():
    """3-Way Line Matching: Exact matching items pass with verification note."""
    po = MOCK_PURCHASE_ORDERS["Zendesk, Inc."]
    po_lines = po["line_items"]

    extracted = [
        LineItemSchema(item_index=1, description="Guide Professional Subscription", quantity=1.0, unit_price=19.00, total=19.00),
        LineItemSchema(item_index=2, description="Support Essential Subscription", quantity=1.0, unit_price=9.00, total=9.00),
    ]

    has_mismatch, matched_cnt, notes = match_line_items(extracted, po_lines)
    assert not has_mismatch
    assert matched_cnt == 2
    assert len(notes) == 0

    inv = ParsedInvoiceData(
        invoice_number="INV-ZEN-PASS",
        invoice_date="2026-11-12",
        vendor_name="Zendesk, Inc.",
        total_amount=28.00,
        line_items=extracted,
    )
    status, reasoning = validate_invoice(inv)
    assert status == "APPROVED"
    assert "3-Way Line Match: Verified 2 item(s) against PO specification." in reasoning


def test_3way_line_item_matching_quantity_mismatch():
    """3-Way Line Matching: Flags quantity mismatch with compliance warning."""
    extracted = [
        LineItemSchema(item_index=1, description="Guide Professional Subscription", quantity=5.0, unit_price=19.00, total=95.00),
        LineItemSchema(item_index=2, description="Support Essential Subscription", quantity=1.0, unit_price=9.00, total=9.00),
    ]
    inv = ParsedInvoiceData(
        invoice_number="INV-ZEN-QTY",
        invoice_date="2026-11-12",
        vendor_name="Zendesk, Inc.",
        total_amount=104.00,
        line_items=extracted,
    )
    status, reasoning = validate_invoice(inv)
    assert status == "APPROVED"
    assert "3-Way Line Match Warning:" in reasoning
    assert "Quantity mismatch on 'Guide Professional Subscription': invoice states 5, expected 1 from PO" in reasoning


def test_3way_line_item_matching_unit_price_mismatch():
    """3-Way Line Matching: Flags unit price mismatch beyond allowable tolerance."""
    extracted = [
        LineItemSchema(item_index=1, description="Guide Professional Subscription", quantity=1.0, unit_price=29.00, total=29.00),
        LineItemSchema(item_index=2, description="Support Essential Subscription", quantity=1.0, unit_price=9.00, total=9.00),
    ]
    inv = ParsedInvoiceData(
        invoice_number="INV-ZEN-PRICE",
        invoice_date="2026-11-12",
        vendor_name="Zendesk, Inc.",
        total_amount=38.00,
        line_items=extracted,
    )
    status, reasoning = validate_invoice(inv)
    assert status == "APPROVED"
    assert "3-Way Line Match Warning:" in reasoning
    assert "Unit price mismatch on 'Guide Professional Subscription': invoice states 29.00, expected 19.00" in reasoning


def test_3way_line_item_matching_unrecognized_item():
    """3-Way Line Matching: Flags unrecognized line item not present in PO."""
    extracted = [
        LineItemSchema(item_index=1, description="Guide Professional Subscription", quantity=1.0, unit_price=19.00, total=19.00),
        LineItemSchema(item_index=2, description="Luxury Executive Airport Transfer", quantity=1.0, unit_price=150.00, total=150.00),
    ]
    inv = ParsedInvoiceData(
        invoice_number="INV-ZEN-EXTRA",
        invoice_date="2026-11-12",
        vendor_name="Zendesk, Inc.",
        total_amount=169.00,
        line_items=extracted,
    )
    status, reasoning = validate_invoice(inv)
    assert status == "APPROVED"
    assert "3-Way Line Match Warning:" in reasoning
    assert "Unrecognized line item 'Luxury Executive Airport Transfer' not found in PO" in reasoning
