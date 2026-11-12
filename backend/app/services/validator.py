"""
validator.py — Stage 5 Business Rules & Invoice Validation Engine.

Responsibilities:
  - Implement an auditable, rules-based validation engine for invoice approvals.
  - Rule 1 (Completeness): Verify critical fields (invoice_number, vendor_name, total_amount).
  - Rule 2 (PO Matching & Tolerance): Verify vendor exists in the approved PO master database
    and total_amount is within the approved tolerance limit.
"""

from __future__ import annotations

import difflib
import logging
import re
from typing import Any, Dict, Optional, Tuple

from app.schemas.invoice import ParsedInvoiceData

logger = logging.getLogger(__name__)

# Hardcoded Mock PO Master Database (vendor -> PO details)
# Expanded to 30 realistic enterprise B2B vendor profiles across USD, INR, and EUR.
MOCK_PURCHASE_ORDERS: Dict[str, Dict[str, Any]] = {
    # ── Live Demo Vendors ──────────────────────────────────────────────────
    "Zendesk, Inc.": {
        "po_number": "PO-ZEN-2026",
        "approved_limit": 500.00,
        "currency": "GBP",
        "line_items": [
            {"description": "Guide Professional Subscription", "quantity": 1.0, "unit_price": 19.00, "total": 19.00},
            {"description": "Support Essential Subscription", "quantity": 1.0, "unit_price": 9.00, "total": 9.00},
        ],
    },
    "Electronics Mart India Limited": {
        "po_number": "PO-MART-998",
        "approved_limit": 50000.00,
        "currency": "INR",
        "line_items": [
            {"description": "APPLE IP 16 PRO MAX SL CS MGS PLM MYYW3Z", "quantity": 1.0, "unit_price": 4900.00, "total": 4900.00},
        ],
    },
    "Zylker PC Builds": {
        "po_number": "PO-ZYLK-554",
        "approved_limit": 1000.00,
        "currency": "USD",
        "line_items": [
            {"description": "Gaming Keyboard", "quantity": 1.0, "unit_price": 99.00, "total": 99.00},
            {"description": "Portable SSD", "quantity": 1.0, "unit_price": 149.00, "total": 149.00},
            {"description": "Wireless Webcam", "quantity": 1.0, "unit_price": 79.00, "total": 79.00},
        ],
    },
    "Garden repairs": {
        "po_number": "PO-GRD-2022",
        "approved_limit": 150.00,
        "currency": "NZD",
        "line_items": [
            {"description": "Sample Service", "quantity": 1.0, "unit_price": 100.00, "total": 100.00},
        ],
    },
    "Romashka Ltd": {
        "po_number": "PO-ROM-2011",
        "approved_limit": 1000.00,
        "currency": "EUR",
        "line_items": [
            {"description": "Projecting", "quantity": 1.0, "unit_price": 50.00, "total": 50.00},
            {"description": "Develop", "quantity": 17.0, "unit_price": 40.00, "total": 680.00},
            {"description": "Analysis", "quantity": 3.0, "unit_price": 35.00, "total": 105.00},
        ],
    },
    "My company": {
        "po_number": "PO-MYC-2026",
        "approved_limit": 1000.00,
        "currency": "USD",
        "line_items": [
            {"description": "Coffee", "quantity": 50.0, "unit_price": 10.00, "total": 500.00},
            {"description": "Cups", "quantity": 100.0, "unit_price": 9.00, "total": 900.00},
        ],
    },

    # ── Core Baseline Vendors ──────────────────────────────────────────────
    "Supplier Corp India Pvt Ltd": {
        "po_number": "PO-99210",
        "approved_limit": 300000.0,
        "currency": "INR",
        "line_items": [
            {"description": "Industrial Hydraulic Pump", "quantity": 2.0, "unit_price": 125000.0, "total": 250000.0},
        ],
    },
    "Acme Corp": {
        "po_number": "PO-10022",
        "approved_limit": 2000.0,
        "currency": "USD",
        "line_items": [
            {"description": "Standard Widget Unit", "quantity": 10.0, "unit_price": 195.0, "total": 1950.0},
        ],
    },
    "TechSupply Global Solutions Inc": {
        "po_number": "PO-88321",
        "approved_limit": 1500.0,
        "currency": "USD",
    },
    "Apex Enterprise Services LLC": {
        "po_number": "PO-44102",
        "approved_limit": 50000.0,
        "currency": "USD",
    },
    "Zylker Electronics Hub": {
        "po_number": "PO-ZYLK-2026",
        "approved_limit": 5000.0,
        "currency": "USD",
    },

    # ── Enterprise Cloud, SaaS & IT Infrastructure ────────────────────────
    "Logistics Cloud Solutions LLC": {
        "po_number": "PO-LCS-4011",
        "approved_limit": 45000.0,
        "currency": "USD",
    },
    "Global SaaS Integrators": {
        "po_number": "PO-GSI-3390",
        "approved_limit": 85000.0,
        "currency": "USD",
    },
    "Datastream Cloud Networks Inc": {
        "po_number": "PO-DCN-4890",
        "approved_limit": 95000.0,
        "currency": "USD",
    },
    "Quantum Scale AI Systems Inc": {
        "po_number": "PO-QSA-9521",
        "approved_limit": 175000.0,
        "currency": "USD",
    },
    "CyberShield Defense Systems Inc": {
        "po_number": "PO-CDS-9104",
        "approved_limit": 75000.0,
        "currency": "USD",
    },
    "Ironclad Cyber Operations LLC": {
        "po_number": "PO-ICO-6472",
        "approved_limit": 110000.0,
        "currency": "USD",
    },
    "Redwood Telecom Infrastructure LLC": {
        "po_number": "PO-RTI-8430",
        "approved_limit": 140000.0,
        "currency": "USD",
    },

    # ── Global Manufacturing, Automation & Industrial Equipment ───────────
    "Apex Manufacturing Partners": {
        "po_number": "PO-AMP-7820",
        "approved_limit": 250000.0,
        "currency": "USD",
    },
    "Nordic Industrial Automation AB": {
        "po_number": "PO-NIA-8812",
        "approved_limit": 120000.0,
        "currency": "EUR",
    },
    "Precision CNC Engineering GmbH": {
        "po_number": "PO-PCE-6231",
        "approved_limit": 180000.0,
        "currency": "EUR",
    },
    "Bavaria Chemical Synthetics AG": {
        "po_number": "PO-BCS-3120",
        "approved_limit": 220000.0,
        "currency": "EUR",
    },
    "Alps Heavy Machinery SAS": {
        "po_number": "PO-AHM-4478",
        "approved_limit": 290000.0,
        "currency": "EUR",
    },
    "Helix Biotech Instruments Corp": {
        "po_number": "PO-HBI-7043",
        "approved_limit": 310000.0,
        "currency": "USD",
    },

    # ── Logistics, Supply Chain & Operations ──────────────────────────────
    "Vanguard Freight Logistics Corp": {
        "po_number": "PO-VFL-1055",
        "approved_limit": 62000.0,
        "currency": "USD",
    },
    "Rotterdam Maritime Shipping B.V.": {
        "po_number": "PO-RMS-5602",
        "approved_limit": 165000.0,
        "currency": "EUR",
    },
    "Continental Fleet Logistics GmbH": {
        "po_number": "PO-CFL-1983",
        "approved_limit": 88000.0,
        "currency": "EUR",
    },
    "Chesapeake Packaging Materials LLC": {
        "po_number": "PO-CPM-3784",
        "approved_limit": 34000.0,
        "currency": "USD",
    },
    "Beacon Hill Facility Services LLC": {
        "po_number": "PO-BHF-2189",
        "approved_limit": 28000.0,
        "currency": "USD",
    },
    "Starlight Office Supplies & Paper Co": {
        "po_number": "PO-SOS-1194",
        "approved_limit": 12000.0,
        "currency": "USD",
    },
    "Pinnacle Strategic Auditing & Advisory": {
        "po_number": "PO-PSA-2015",
        "approved_limit": 55000.0,
        "currency": "USD",
    },

    # ── Indian Technology & Enterprise Solutions (INR) ─────────────────────
    "Tata Consultancy Infrastructures Ltd": {
        "po_number": "PO-TCI-5091",
        "approved_limit": 450000.0,
        "currency": "INR",
    },
    "Infosys Digital Platforms Pvt Ltd": {
        "po_number": "PO-IDP-7742",
        "approved_limit": 380000.0,
        "currency": "INR",
    },
    "Mahindra Industrial Power Equipment": {
        "po_number": "PO-MIP-6618",
        "approved_limit": 500000.0,
        "currency": "INR",
    },
    "Reliance Enterprise IT Hardware Ltd": {
        "po_number": "PO-REH-8392",
        "approved_limit": 420000.0,
        "currency": "INR",
    },
    "Wipro Cloud Infrastructure Services": {
        "po_number": "PO-WCI-9210",
        "approved_limit": 360000.0,
        "currency": "INR",
    },
}


# ── Allowable enterprise tolerance buffer above baseline PO limit (5%) ──────────
ENTERPRISE_TOLERANCE_PERCENT: float = 0.05


def _clean_vendor_string(name: str) -> str:
    """Normalize vendor name by stripping punctuation, extra spaces, and standardizing common abbreviations."""
    s = re.sub(r"[^\w\s]", " ", name.lower())
    words = s.split()
    norm_words = []
    for w in words:
        if w in ("incorporated", "corporation", "corp"):
            norm_words.append("inc")
        elif w in ("limited", "limit"):
            norm_words.append("ltd")
        elif w in ("company",):
            norm_words.append("co")
        else:
            norm_words.append(w)
    return " ".join(norm_words)


def _match_vendor(
    vendor_name: str,
    fuzzy_threshold: float = 0.70,
) -> Optional[Tuple[str, Dict[str, Any], str, float]]:
    """
    Match vendor name against PO master database using exact, substring, or fuzzy matching.

    Returns:
      (master_vendor_name, po_info, match_type, confidence_score)
      where match_type is 'EXACT', 'SUBSTRING', or 'FUZZY'.
    """
    if not vendor_name or not vendor_name.strip():
        return None

    v_raw = vendor_name.strip().lower()
    v_norm = _clean_vendor_string(vendor_name)

    # 1. Exact raw match
    for master_name, po_info in MOCK_PURCHASE_ORDERS.items():
        if master_name.lower() == v_raw:
            return master_name, po_info, "EXACT", 1.0

    # 2. Exact normalized match
    for master_name, po_info in MOCK_PURCHASE_ORDERS.items():
        if _clean_vendor_string(master_name) == v_norm:
            return master_name, po_info, "EXACT", 1.0

    # 3. Substring / containment match
    for master_name, po_info in MOCK_PURCHASE_ORDERS.items():
        m_norm = _clean_vendor_string(master_name)
        if m_norm in v_norm or v_norm in m_norm:
            return master_name, po_info, "SUBSTRING", 0.95

    # 4. Difflib SequenceMatcher fuzzy match
    best_master = None
    best_po = None
    best_ratio = 0.0

    for master_name, po_info in MOCK_PURCHASE_ORDERS.items():
        m_norm = _clean_vendor_string(master_name)
        ratio = difflib.SequenceMatcher(None, v_norm, m_norm).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best_master = master_name
            best_po = po_info

    if best_master and best_ratio >= fuzzy_threshold:
        return best_master, best_po, "FUZZY", round(best_ratio, 3)

    return None


def match_line_items(
    extracted_items: List[Any],
    po_items: List[Dict[str, Any]],
    desc_threshold: float = 0.55,
) -> Tuple[bool, int, List[str]]:
    """
    Perform 3-way line item matching between extracted invoice items and PO items.

    Returns:
      (has_mismatch, matched_items_count, mismatch_notes)
    """
    notes = []
    matched_count = 0
    has_mismatch = False

    for item in extracted_items:
        desc = getattr(item, "description", "") or (item.get("description", "") if isinstance(item, dict) else "")
        desc = desc.strip()
        qty = getattr(item, "quantity", None) if not isinstance(item, dict) else item.get("quantity")
        price = getattr(item, "unit_price", None) if not isinstance(item, dict) else item.get("unit_price")

        # Find best matching PO item
        best_po = None
        best_ratio = 0.0
        for p in po_items:
            p_desc = p.get("description", "").strip()
            if desc.lower() in p_desc.lower() or p_desc.lower() in desc.lower():
                ratio = 0.95
            else:
                ratio = difflib.SequenceMatcher(None, desc.lower(), p_desc.lower()).ratio()
            if ratio > best_ratio:
                best_ratio = ratio
                best_po = p

        if best_po and best_ratio >= desc_threshold:
            matched_count += 1
            po_qty = best_po.get("quantity")
            po_price = best_po.get("unit_price")

            # Check quantity
            if qty is not None and po_qty is not None:
                if abs(qty - po_qty) > 0.01:
                    has_mismatch = True
                    notes.append(
                        f"Quantity mismatch on '{best_po['description']}': invoice states {qty:g}, expected {po_qty:g} from PO"
                    )

            # Check unit price (tolerance 5%)
            if price is not None and po_price is not None and po_price > 0:
                if abs(price - po_price) > 0.05 * po_price:
                    has_mismatch = True
                    notes.append(
                        f"Unit price mismatch on '{best_po['description']}': invoice states {price:,.2f}, expected {po_price:,.2f}"
                    )
        else:
            has_mismatch = True
            notes.append(f"Unrecognized line item '{desc}' not found in PO")

    return has_mismatch, matched_count, notes


def validate_invoice(
    invoice: ParsedInvoiceData,
    duplicate_info: Optional[Dict[str, Any]] = None,
) -> Tuple[str, str]:
    """
    Validate a parsed invoice against business criteria.

    Features:
      - Rule 0 (Duplicate Detection): Cryptographic SHA-256 duplicate submission rejection.
      - Rule 1 (Completeness): Validates presence of critical fields.
      - Rule 2 (Fuzzy PO Matching): Matches vendor names using difflib fuzzy resolution.
      - Rule 3 (Tolerance Buffers): 5.0% enterprise tolerance buffer above baseline PO limits.
      - Rule 4 (3-Way Line Matching): Granular line-item description and quantity verification.

    Returns:
      (validation_status, validation_reasoning)
      where status is one of: 'APPROVED', 'REJECTED', 'MANUAL_REVIEW'
    """
    # ── Rule 0: Cryptographic Duplicate Detection (SHA-256) ───────────────────
    if duplicate_info:
        orig_fn = duplicate_info.get("original_filename", "submission")
        orig_ts = duplicate_info.get("upload_timestamp", "earlier date")
        return (
            "REJECTED",
            f"Duplicate invoice detected: file matches previously processed submission [{orig_fn} / {orig_ts}].",
        )

    # ── Rule 1: Completeness Check ─────────────────────────────────────────────
    missing_fields = []
    if not invoice.invoice_number or not str(invoice.invoice_number).strip():
        missing_fields.append("invoice_number")
    if not invoice.vendor_name or not str(invoice.vendor_name).strip():
        missing_fields.append("vendor_name")
    if invoice.total_amount is None:
        missing_fields.append("total_amount")

    if missing_fields:
        return (
            "MANUAL_REVIEW",
            f"Missing critical fields: {', '.join(missing_fields)}.",
        )

    # Sanity check for negative or non-numeric totals
    if invoice.total_amount <= 0:
        return (
            "REJECTED",
            f"Invalid invoice total amount: {invoice.total_amount}. Invoices must have positive totals.",
        )

    # ── Rule 2 & 3: Mock PO Matching with Fuzzy Resolution & Tolerance Buffer ────
    match = _match_vendor(invoice.vendor_name)
    if not match:
        return (
            "MANUAL_REVIEW",
            f"Unknown vendor '{invoice.vendor_name}'. No active purchase order found in PO master database.",
        )

    master_vendor, po_info, match_type, confidence = match
    approved_limit = float(po_info["approved_limit"])
    po_number = po_info["po_number"]
    currency = po_info.get("currency", "USD")

    # Format vendor resolution note for compliance trail
    if match_type == "EXACT":
        vendor_audit = f"vendor '{master_vendor}'"
    elif match_type == "SUBSTRING":
        vendor_audit = f"vendor '{master_vendor}' (matched '{invoice.vendor_name}')"
    else:
        vendor_audit = f"vendor '{master_vendor}' (fuzzy match confidence: {confidence:.0%} on '{invoice.vendor_name}')"

    # Tolerance calculations (5% buffer)
    tolerance_buffer = round(approved_limit * ENTERPRISE_TOLERANCE_PERCENT, 2)
    max_allowed = round(approved_limit + tolerance_buffer, 2)

    if invoice.total_amount <= approved_limit:
        base_status = "APPROVED"
        base_reasoning = (
            f"Matched PO limit: Document matches approved PO {po_number} for {vendor_audit}. "
            f"Invoice total ({invoice.total_amount:,.2f} {currency}) is within approved limit ({approved_limit:,.2f} {currency})."
        )
    elif invoice.total_amount <= max_allowed:
        delta = invoice.total_amount - approved_limit
        pct_over = (delta / approved_limit) * 100.0
        base_status = "APPROVED"
        base_reasoning = (
            f"Approved within tolerance buffer: Document matches approved PO {po_number} for {vendor_audit}. "
            f"Invoice total ({invoice.total_amount:,.2f} {currency}) exceeds baseline limit ({approved_limit:,.2f} {currency}) "
            f"by {delta:,.2f} {currency} (+{pct_over:.1f}%), which is within the allowable 5.0% enterprise tolerance buffer ({max_allowed:,.2f} {currency})."
        )
    else:
        delta = invoice.total_amount - approved_limit
        pct_over = (delta / approved_limit) * 100.0
        excess_over_buffer = invoice.total_amount - max_allowed
        base_status = "REJECTED"
        base_reasoning = (
            f"Exceeds PO tolerance: Document matches PO {po_number} for {vendor_audit}, "
            f"but invoice total ({invoice.total_amount:,.2f} {currency}) exceeds approved limit ({approved_limit:,.2f} {currency}) "
            f"and allowable 5.0% enterprise tolerance ceiling ({max_allowed:,.2f} {currency}) by {excess_over_buffer:,.2f} {currency} "
            f"(+{pct_over:.1f}% over baseline limit)."
        )

    # ── Rule 4: Granular Line-Item 3-Way Matching ──────────────────────────────
    po_line_items = po_info.get("line_items", [])
    if po_line_items and invoice.line_items:
        has_line_mismatch, matched_count, line_mismatch_notes = match_line_items(
            invoice.line_items, po_line_items
        )
        if has_line_mismatch:
            line_audit = f" | 3-Way Line Match Warning: {'; '.join(line_mismatch_notes)}."
            base_reasoning += line_audit
        else:
            base_reasoning += f" | 3-Way Line Match: Verified {matched_count} item(s) against PO specification."

    return (base_status, base_reasoning)
