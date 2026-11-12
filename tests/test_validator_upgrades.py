"""
tests/test_validator_upgrades.py — Verify Fuzzy Vendor Matching, 5% Tolerance Buffer,
and Auditable Reasoning in Stage 5 Validator.
"""

import pytest
from app.services.validator import (
    ENTERPRISE_TOLERANCE_PERCENT,
    MOCK_PURCHASE_ORDERS,
    _clean_vendor_string,
    _match_vendor,
    validate_invoice,
)
from app.schemas.invoice import ParsedInvoiceData


@pytest.mark.parametrize(
    "input_name, expected_master",
    [
        ("Zendesk Inc", "Zendesk, Inc."),
        ("Zendesk Incorporated", "Zendesk, Inc."),
        ("Zendesk", "Zendesk, Inc."),
        ("Zendeskk Inc", "Zendesk, Inc."),
        ("Electronics Mart India Ltd", "Electronics Mart India Limited"),
        ("Electronics Mart India", "Electronics Mart India Limited"),
        ("Zylker PC Build", "Zylker PC Builds"),
        ("Zylker PC Builds LLC", "Zylker PC Builds"),
        ("Acme Corporation", "Acme Corp"),
        ("Acme Corp.", "Acme Corp"),
        ("Wayne Enterprises International", None),
        ("Stark Robotics LLC", None),
    ],
)
def test_fuzzy_vendor_matching(input_name, expected_master):
    """Verify fuzzy vendor resolution across punctuation, legal suffixes, and typos."""
    res = _match_vendor(input_name)
    if expected_master:
        assert res is not None
        master_name, po_info, match_type, conf = res
        assert master_name == expected_master
    else:
        assert res is None


def test_tolerance_buffer_acme():
    """Verify 5% allowable enterprise tolerance buffer over PO limits."""
    # 1. Exactly at limit: APPROVED
    inv_exact = ParsedInvoiceData(invoice_number="INV-EXACT", invoice_date="2026-11-12", vendor_name="Acme Corp", total_amount=2000.0)
    st, rsn = validate_invoice(inv_exact)
    assert st == "APPROVED"
    assert "within approved limit" in rsn

    # 2. Within 5% buffer (+2.5%): APPROVED
    inv_buf_low = ParsedInvoiceData(invoice_number="INV-BUF-1", invoice_date="2026-11-12", vendor_name="Acme Corp", total_amount=2050.0)
    st, rsn = validate_invoice(inv_buf_low)
    assert st == "APPROVED"
    assert "Approved within tolerance buffer" in rsn
    assert "+2.5%" in rsn

    # 3. Exactly at 5.0% ceiling: APPROVED
    inv_buf_max = ParsedInvoiceData(invoice_number="INV-BUF-2", invoice_date="2026-11-12", vendor_name="Acme Corp", total_amount=2100.0)
    st, rsn = validate_invoice(inv_buf_max)
    assert st == "APPROVED"
    assert "Approved within tolerance buffer" in rsn
    assert "+5.0%" in rsn

    # 4. Just above 5.0% ceiling: REJECTED
    inv_exceed_near = ParsedInvoiceData(invoice_number="INV-EXC-1", invoice_date="2026-11-12", vendor_name="Acme Corp", total_amount=2105.0)
    st, rsn = validate_invoice(inv_exceed_near)
    assert st == "REJECTED"
    assert "Exceeds PO tolerance" in rsn

    # 5. Far above ceiling: REJECTED
    inv_exceed_far = ParsedInvoiceData(invoice_number="INV-EXC-2", invoice_date="2026-11-12", vendor_name="Acme Corp", total_amount=2500.0)
    st, rsn = validate_invoice(inv_exceed_far)
    assert st == "REJECTED"
    assert "Exceeds PO tolerance" in rsn
    assert "+25.0%" in rsn


@pytest.mark.parametrize(
    "vendor, amount, expected_status",
    [
        ("Zendesk Inc", 28.00, "APPROVED"),
        ("Zendesk Inc", 520.00, "APPROVED"),
        ("Zendesk Inc", 535.00, "REJECTED"),
        ("Electronics Mart India", 51500.00, "APPROVED"),
        ("Zylker PC Build", 1040.00, "APPROVED"),
    ],
)
def test_live_demo_vendors_tolerance(vendor, amount, expected_status):
    """Verify live demo vendor invoices against enterprise tolerance buffers."""
    inv = ParsedInvoiceData(invoice_number="INV-LIVE", invoice_date="2026-11-12", vendor_name=vendor, total_amount=amount)
    st, rsn = validate_invoice(inv)
    assert st == expected_status
