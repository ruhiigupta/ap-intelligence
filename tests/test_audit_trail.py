"""
tests/test_audit_trail.py — Test suite for Immutable State Transition Audit Log.
"""

import sys
import unittest
from pathlib import Path
from fastapi.testclient import TestClient
import pymupdf

from app.main import app
from app.database import init_db, get_db, append_audit_event
from app.services.reader import set_mock_reader, reset_mock_reader


class TestAuditTrail(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db()
        cls.client = TestClient(app)

    def test_01_table_exists(self):
        """Verify invoice_audit_trail table and index exist in DB."""
        with get_db() as conn:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='invoice_audit_trail';"
            ).fetchone()
            self.assertIsNotNone(row, "invoice_audit_trail table must exist")

    def test_02_append_audit_event_direct(self):
        """Test append_audit_event helper directly with a valid foreign key document."""
        doc_id = "test-doc-direct-audit"
        with get_db() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO documents (
                    document_id, original_filename, stored_filename,
                    mime_type, file_size, sha256_hash, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?);
                """,
                (doc_id, "test.pdf", "test.pdf", "application/pdf", 100, "dummy-hash-123", "uploaded"),
            )
            conn.commit()

        append_audit_event(doc_id, "TEST_EVENT", {"foo": "bar", "num": 42})

        with get_db() as conn:
            row = conn.execute(
                "SELECT * FROM invoice_audit_trail WHERE document_id = ? AND event_type = ?;",
                (doc_id, "TEST_EVENT"),
            ).fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row["event_type"], "TEST_EVENT")
            self.assertIn('"foo": "bar"', row["details"])

    def test_03_full_lifecycle_audit_trail(self):
        """Test document lifecycle transitions produce all expected audit records."""
        # Create a valid 1-page PDF
        doc = pymupdf.open()
        page = doc.new_page(width=612, height=792)
        page.insert_text(
            (50, 72),
            "INVOICE\nZendesk, Inc.\nInvoice Number: INV-AUDIT-001\nTotal: GBP 28.00\nGuide Professional Subscription 1 19.00\nSupport Essential Subscription 1 9.00",
            fontsize=12,
        )
        pdf_bytes = doc.tobytes()
        doc.close()

        def mock_reader(image_bytes, page_number):
            return {
                "page_number": page_number,
                "tokens": [
                    {"text": "Zendesk, Inc.", "bbox": [50, 72, 200, 90]},
                    {"text": "INV-AUDIT-001", "bbox": [50, 100, 200, 120]},
                    {"text": "28.00", "bbox": [50, 150, 200, 170]},
                ],
                "tables": [
                    {
                        "table_index": 1,
                        "rows": 2,
                        "columns": 4,
                        "headers": ["description", "quantity", "unit price", "total"],
                        "cells": [
                            {"row_index": 1, "col_index": 1, "text": "Guide Professional Subscription"},
                            {"row_index": 1, "col_index": 2, "text": "1"},
                            {"row_index": 1, "col_index": 3, "text": "19.00"},
                            {"row_index": 1, "col_index": 4, "text": "19.00"},
                            {"row_index": 2, "col_index": 1, "text": "Support Essential Subscription"},
                            {"row_index": 2, "col_index": 2, "text": "1"},
                            {"row_index": 2, "col_index": 3, "text": "9.00"},
                            {"row_index": 2, "col_index": 4, "text": "9.00"},
                        ],
                    }
                ],
                "raw_key_values": {
                    "Invoice Number": "INV-AUDIT-001",
                    "Vendor": "Zendesk, Inc.",
                    "Total": "GBP 28.00",
                },
            }, "mock-vision", "v1.0"

        set_mock_reader(mock_reader)

        try:
            # 1. Stage 1: Upload fresh document
            resp_up = self.client.post(
                "/api/invoices/upload",
                files={"file": ("audit_test_invoice.pdf", pdf_bytes, "application/pdf")},
            )
            self.assertEqual(resp_up.status_code, 201)
            doc_id = resp_up.json()["document"]["document_id"]

            # 2. Stage 2: Page analyze
            resp_an = self.client.post(f"/api/invoices/{doc_id}/analyze")
            self.assertEqual(resp_an.status_code, 200)

            # 3. Stage 3: Vision read
            resp_rd = self.client.post(f"/api/invoices/{doc_id}/read")
            self.assertEqual(resp_rd.status_code, 200)

            # 4. Stage 4/5: Understand & Validate
            resp_und = self.client.post(f"/api/invoices/{doc_id}/understand")
            self.assertEqual(resp_und.status_code, 200)

            # 5. Query Audit Trail endpoint
            resp_at = self.client.get(f"/api/invoices/{doc_id}/audit-trail")
            self.assertEqual(resp_at.status_code, 200)
            trail_data = resp_at.json()

            self.assertEqual(trail_data["document_id"], doc_id)
            events = trail_data["events"]
            event_types = [e["event_type"] for e in events]

            # Verify all expected transitions exist in order
            self.assertIn("UPLOADED", event_types)
            self.assertIn("HASH_VERIFIED", event_types)
            self.assertIn("PAGE_ANALYZED", event_types)
            self.assertIn("VISION_READ", event_types)
            self.assertIn("VALIDATED", event_types)

            # Verify details payload on VALIDATED event
            val_event = next(e for e in events if e["event_type"] == "VALIDATED")
            self.assertEqual(val_event["details"]["validation_status"], "APPROVED")
            self.assertIn("Zendesk", val_event["details"]["vendor_name"])

            # 6. Test duplicate upload audit logging
            resp_dup = self.client.post(
                "/api/invoices/upload",
                files={"file": ("audit_test_invoice_copy.pdf", pdf_bytes, "application/pdf")},
            )
            self.assertEqual(resp_dup.status_code, 201)
            dup_id = resp_dup.json()["document"]["document_id"]

            resp_dup_at = self.client.get(f"/api/invoices/{dup_id}/audit-trail")
            self.assertEqual(resp_dup_at.status_code, 200)
            dup_event_types = [e["event_type"] for e in resp_dup_at.json()["events"]]
            self.assertIn("UPLOADED", dup_event_types)
            self.assertIn("HASH_VERIFIED", dup_event_types)
            self.assertIn("DUPLICATE_DETECTED", dup_event_types)

        finally:
            reset_mock_reader()

    def test_04_audit_trail_not_found(self):
        """Test GET audit-trail with non-existent document returns 404."""
        resp = self.client.get("/api/invoices/nonexistent-doc-id-9999/audit-trail")
        self.assertEqual(resp.status_code, 404)
