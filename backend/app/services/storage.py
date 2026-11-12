"""
storage.py — File persistence and document identity management.

Responsibilities:
  - Generate unique, collision-free document IDs (UUID4).
  - Compute SHA-256 content hashes.
  - Write raw bytes to the configured STORAGE_PATH, never overwriting originals.
"""

import hashlib
import uuid
from pathlib import Path

from app.config import STORAGE_PATH


def compute_sha256(data: bytes) -> str:
    """Return lowercase hex SHA-256 digest of raw bytes."""
    return hashlib.sha256(data).hexdigest()


def generate_document_id() -> str:
    """Return a new UUID4 string to use as a document's public identifier."""
    return str(uuid.uuid4())


def build_stored_filename(document_id: str, extension: str) -> str:
    """Return the on-disk filename: ``<uuid>.<ext>``."""
    return f"{document_id}.{extension}"


def persist_file(data: bytes, document_id: str, extension: str) -> Path:
    """
    Write *data* to ``STORAGE_PATH/<document_id>.<extension>``.

    - Creates STORAGE_PATH and any parent directories if missing.
    - Never overwrites an existing file (raises ``FileExistsError`` on collision).
    - Returns the absolute ``Path`` of the stored file.
    """
    STORAGE_PATH.mkdir(parents=True, exist_ok=True)
    dest = STORAGE_PATH / build_stored_filename(document_id, extension)
    if dest.exists():
        raise FileExistsError(
            f"Storage collision at {dest}. "
            "This should not happen — document IDs must be unique."
        )
    dest.write_bytes(data)
    return dest
