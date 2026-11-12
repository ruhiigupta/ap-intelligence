"""
database.py — SQLite connection management using Python's built-in sqlite3.

Responsibilities:
  - Initialize the database file and schema on first run.
  - Provide a context-managed connection helper for use across the app.
  - Expose a health-check function that verifies the DB is reachable.
  - Expose append_audit_event() for immutable audit trail writes.
"""

import json
import sqlite3
import logging
from contextlib import contextmanager
from typing import Any, Dict, Generator, Optional

from app.config import DATABASE_PATH, ensure_directories

logger = logging.getLogger(__name__)


# ── Initialisation ─────────────────────────────────────────────────────────────

def init_db() -> None:
    """
    Ensure the database file exists and the baseline schema is in place.

    Called once at application startup. Safe to run repeatedly — all DDL
    statements use IF NOT EXISTS.
    """
    ensure_directories()

    logger.info("Initialising database at: %s", DATABASE_PATH)

    with _get_raw_connection() as conn:
        cursor = conn.cursor()

        # Enable WAL mode for better concurrent read performance.
        cursor.execute("PRAGMA journal_mode=WAL;")

        # Enforce foreign-key constraints (off by default in SQLite).
        cursor.execute("PRAGMA foreign_keys=ON;")

        # ── Baseline schema ────────────────────────────────────────────────────
        # This table is intentionally minimal at Stage 1.
        # Future stages will ADD tables via migration scripts, never alter this.
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                version     TEXT    NOT NULL UNIQUE,
                applied_at  TEXT    NOT NULL DEFAULT (datetime('now'))
            );
        """)

        # Seed the initial migration record.
        cursor.execute("""
            INSERT OR IGNORE INTO schema_migrations (version)
            VALUES ('0001_initial');
        """)

        # ── 0002: Document storage table ──────────────────────────────────────
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS documents (
                id                 INTEGER PRIMARY KEY AUTOINCREMENT,
                document_id        TEXT    NOT NULL UNIQUE,
                original_filename  TEXT    NOT NULL,
                stored_filename    TEXT    NOT NULL,
                stored_path        TEXT,              -- NULL for duplicate records
                mime_type          TEXT    NOT NULL,
                file_size          INTEGER NOT NULL,  -- bytes
                sha256_hash        TEXT    NOT NULL,
                page_count         INTEGER,           -- PDFs only; NULL for images
                width_px           INTEGER,           -- images only; NULL for PDFs
                height_px          INTEGER,           -- images only; NULL for PDFs
                upload_timestamp   TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
                batch_id           TEXT,              -- NULL for single uploads
                status             TEXT    NOT NULL DEFAULT 'uploaded',
                duplicate_of       TEXT,              -- document_id of original when status='duplicate'
                FOREIGN KEY (duplicate_of) REFERENCES documents(document_id)
            );
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_documents_sha256
            ON documents (sha256_hash);
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_documents_batch_id
            ON documents (batch_id);
        """)

        cursor.execute("""
            INSERT OR IGNORE INTO schema_migrations (version)
            VALUES ('0002_documents_table');
        """)

        # ── 0003: Document pages analysis table ───────────────────────────────
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS document_pages (
                id                     INTEGER PRIMARY KEY AUTOINCREMENT,
                document_id            TEXT    NOT NULL,
                page_number            INTEGER NOT NULL,
                page_type              TEXT    NOT NULL,  -- 'digital' | 'scanned' | 'image'
                has_selectable_text    INTEGER NOT NULL,  -- 1 (True) | 0 (False)
                extracted_native_text  TEXT,              -- NULL if scanned/image or non-digital
                text_blocks            TEXT    NOT NULL,  -- JSON string of [{bbox, text}]
                extraction_routes      TEXT    NOT NULL,  -- JSON string e.g. ["native_text", "visual"]
                page_dimensions        TEXT    NOT NULL,  -- JSON string {"width": w, "height": h}
                detection_status       TEXT    NOT NULL DEFAULT 'completed',
                analyzed_at            TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
                UNIQUE(document_id, page_number),
                FOREIGN KEY (document_id) REFERENCES documents(document_id) ON DELETE CASCADE
            );
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_document_pages_doc_id
            ON document_pages (document_id);
        """)

        cursor.execute("""
            INSERT OR IGNORE INTO schema_migrations (version)
            VALUES ('0003_document_pages_table');
        """)

        # ── 0004: Page reading results table ──────────────────────────────────
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS page_reading_results (
                id                   INTEGER PRIMARY KEY AUTOINCREMENT,
                document_id          TEXT    NOT NULL,
                page_number          INTEGER NOT NULL,
                reading_engine       TEXT    NOT NULL,
                engine_version       TEXT    NOT NULL,
                status               TEXT    NOT NULL DEFAULT 'completed',
                raw_json_output      TEXT    NOT NULL,
                tokens_count         INTEGER NOT NULL DEFAULT 0,
                tables_count         INTEGER NOT NULL DEFAULT 0,
                raw_key_values_count INTEGER NOT NULL DEFAULT 0,
                execution_time_ms    INTEGER,
                created_at           TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
                FOREIGN KEY (document_id, page_number) REFERENCES document_pages(document_id, page_number) ON DELETE CASCADE,
                UNIQUE(document_id, page_number)
            );
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_reading_results_doc_page 
            ON page_reading_results (document_id, page_number);
        """)

        cursor.execute("""
            INSERT OR IGNORE INTO schema_migrations (version)
            VALUES ('0004_page_reading_results');
        """)

        # ── 0005: Invoice understanding & parsed results table ────────────────
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS invoice_parsed_results (
                id                   INTEGER PRIMARY KEY AUTOINCREMENT,
                document_id          TEXT    NOT NULL UNIQUE,
                invoice_number       TEXT,
                invoice_date         TEXT,
                vendor_name          TEXT,
                total_amount         REAL,
                line_items           TEXT    NOT NULL DEFAULT '[]', -- JSON array
                validation_status    TEXT    NOT NULL,              -- 'APPROVED' | 'REJECTED' | 'MANUAL_REVIEW'
                validation_reasoning TEXT    NOT NULL,              -- Auditable decision explanation
                created_at           TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
                FOREIGN KEY (document_id) REFERENCES documents(document_id) ON DELETE CASCADE
            );
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_invoice_parsed_doc_id
            ON invoice_parsed_results (document_id);
        """)

        cursor.execute("""
            INSERT OR IGNORE INTO schema_migrations (version)
            VALUES ('0005_invoice_understanding');
        """)

        # ── 0006: Immutable Audit Trail ───────────────────────────────────────
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS invoice_audit_trail (
                audit_id    INTEGER PRIMARY KEY AUTOINCREMENT,
                document_id TEXT    NOT NULL,
                event_type  TEXT    NOT NULL,  -- e.g. UPLOADED, HASH_VERIFIED, DUPLICATE_DETECTED,
                                               --     PAGE_ANALYZED, VISION_READ, VALIDATED
                details     TEXT    NOT NULL DEFAULT '{}',  -- JSON blob with contextual metadata
                created_at  TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
                FOREIGN KEY (document_id) REFERENCES documents(document_id)
            );
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_audit_trail_doc_id
            ON invoice_audit_trail (document_id);
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_audit_trail_event_type
            ON invoice_audit_trail (event_type);
        """)

        cursor.execute("""
            INSERT OR IGNORE INTO schema_migrations (version)
            VALUES ('0006_audit_trail');
        """)

        conn.commit()

    logger.info("Database initialised successfully.")


# ── Connection helpers ─────────────────────────────────────────────────────────

def _get_raw_connection() -> sqlite3.Connection:
    """Return a raw sqlite3 connection. Caller is responsible for closing it."""
    conn = sqlite3.connect(str(DATABASE_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row  # rows accessible by column name
    conn.execute("PRAGMA foreign_keys = ON;")  # must be set per-connection in SQLite
    return conn


@contextmanager
def get_db() -> Generator[sqlite3.Connection, None, None]:
    """
    Context manager that yields an open SQLite connection and guarantees
    the connection is closed afterwards — even if an exception is raised.

    Usage:
        with get_db() as conn:
            conn.execute(...)
    """
    conn = _get_raw_connection()
    try:
        yield conn
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# ── Health check ───────────────────────────────────────────────────────────────

def check_db_health() -> dict:
    """
    Verify the database is reachable and return a status dict.

    Returns:
        {
            "status": "ok" | "error",
            "database_path": str,
            "detail": str           # human-readable message or error text
        }
    """
    try:
        with get_db() as conn:
            row = conn.execute("SELECT sqlite_version() AS version;").fetchone()
            version = row["version"] if row else "unknown"

        return {
            "status": "ok",
            "database_path": str(DATABASE_PATH),
            "detail": f"SQLite {version} — connection healthy",
        }

    except Exception as exc:
        logger.exception("Database health check failed.")
        return {
            "status": "error",
            "database_path": str(DATABASE_PATH),
            "detail": str(exc),
        }


# ── Audit Trail helper ─────────────────────────────────────────────────────────

def append_audit_event(
    document_id: str,
    event_type: str,
    details: Optional[Dict[str, Any]] = None,
) -> None:
    """
    Append a single, immutable audit event for a document lifecycle transition.

    This is a fire-and-forget write; failures are logged but never re-raised
    so a transient audit-write error never aborts a critical pipeline stage.

    Args:
        document_id: UUID of the document the event belongs to.
        event_type:  Stage-level event name, e.g. UPLOADED, HASH_VERIFIED,
                     DUPLICATE_DETECTED, PAGE_ANALYZED, VISION_READ, VALIDATED.
        details:     Optional dict of contextual metadata serialized to JSON.
    """
    payload = json.dumps(details or {}, ensure_ascii=False, default=str)
    try:
        with get_db() as conn:
            conn.execute(
                """
                INSERT INTO invoice_audit_trail (document_id, event_type, details)
                VALUES (?, ?, ?);
                """,
                (document_id, event_type, payload),
            )
            conn.commit()
    except Exception:
        logger.exception(
            "Failed to write audit event [%s] for document %s — pipeline continues.",
            event_type, document_id,
        )
