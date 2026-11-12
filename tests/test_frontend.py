"""
tests/test_frontend.py — Frontend UI & Listing Endpoint Verification Test.
"""

from fastapi.testclient import TestClient
from app.main import app
from app.database import init_db

client = TestClient(app)


def test_dashboard_page():
    """Verify Dashboard page GET / returns HTML with key elements."""
    init_db()
    res = client.get("/")
    assert res.status_code == 200
    assert "text/html" in res.headers["content-type"]
    assert "Zamp" in res.text and "AP Intelligence" in res.text
    assert "Invoice Processing History" in res.text
    assert "Process New Invoice" in res.text


def test_process_page():
    """Verify Live Process page GET /process returns HTML with multi-stage stepper."""
    res = client.get("/process")
    assert res.status_code == 200
    assert "text/html" in res.headers["content-type"]
    assert "Live Invoice Processing Pipeline" in res.text
    assert "Stage 1: File Ingestion" in res.text
    assert "Stage 2: Page Detection" in res.text
    assert "Stage 3: Document Reading" in res.text
    assert "Stage 4: Invoice Understanding" in res.text


def test_invoices_listing_api():
    """Verify GET /api/invoices returns structured invoice list."""
    res = client.get("/api/invoices")
    assert res.status_code == 200
    data = res.json()
    assert "total" in data and "invoices" in data
    assert isinstance(data["invoices"], list)
    if data["invoices"]:
        first = data["invoices"][0]
        assert "document_id" in first
        assert "original_filename" in first
        assert "validation_status" in first
