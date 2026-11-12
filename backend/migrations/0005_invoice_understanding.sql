-- 0005_invoice_understanding.sql
-- Stage 5: Invoice Understanding & Validation schema

CREATE TABLE IF NOT EXISTS invoice_parsed_results (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id          TEXT    NOT NULL UNIQUE,
    invoice_number       TEXT,
    invoice_date         TEXT,
    vendor_name          TEXT,
    total_amount         REAL,
    line_items           TEXT    NOT NULL DEFAULT '[]',
    validation_status    TEXT    NOT NULL,  -- 'APPROVED' | 'REJECTED' | 'MANUAL_REVIEW'
    validation_reasoning TEXT    NOT NULL,  -- Auditable decision explanation
    created_at           TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    FOREIGN KEY (document_id) REFERENCES documents(document_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_invoice_parsed_doc_id
ON invoice_parsed_results (document_id);
