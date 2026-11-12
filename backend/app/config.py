"""
config.py — Centralised configuration loaded from environment variables.

All settings have safe defaults suitable for local development.
In production, override via a .env file or the host environment.
"""

import os
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv

# ── Resolve backend/ root and load environment variables ──────────────────────
# This file lives at  backend/app/config.py
# BACKEND_DIR  →      backend/
BACKEND_DIR = Path(__file__).resolve().parent.parent

# 1. Check for .env in backend/ (e.g. backend/.env)
_backend_env = BACKEND_DIR / ".env"
if _backend_env.exists():
    load_dotenv(dotenv_path=_backend_env, override=True)

# 2. Check for .env in workspace root (one level above backend/)
_root_env = BACKEND_DIR.parent / ".env"
if _root_env.exists():
    load_dotenv(dotenv_path=_root_env, override=True)

# 3. Check current working directory
_cwd_env = Path.cwd() / ".env"
if _cwd_env.exists():
    load_dotenv(dotenv_path=_cwd_env, override=True)

# 4. Fallback search
load_dotenv(override=True)


# ── Application ────────────────────────────────────────────────────────────────
APP_NAME: str = os.getenv("APP_NAME", "AP Intelligence")
APP_ENV: str = os.getenv("APP_ENV", "development")
APP_HOST: str = os.getenv("APP_HOST", "127.0.0.1")
APP_PORT: int = int(os.getenv("APP_PORT", "8000"))
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO").upper()


# ── Database ───────────────────────────────────────────────────────────────────
# DATABASE_PATH may be absolute or relative.
# If relative it is resolved relative to BACKEND_DIR so it stays inside the
# project and is never machine-specific.
_raw_db_path: str = os.getenv("DATABASE_PATH", "data/ap_intelligence.db")
DATABASE_PATH: Path = (
    Path(_raw_db_path)
    if Path(_raw_db_path).is_absolute()
    else BACKEND_DIR / _raw_db_path
)


# ── File storage ───────────────────────────────────────────────────────────────
_raw_storage_path: str = os.getenv("STORAGE_PATH", "storage")
STORAGE_PATH: Path = (
    Path(_raw_storage_path)
    if Path(_raw_storage_path).is_absolute()
    else BACKEND_DIR / _raw_storage_path
)


# ── Document Detection Thresholds ─────────────────────────────────────────────
# Configurable threshold to distinguish meaningful selectable text from artifacts
# (e.g. metadata tags, watermarks, stray OCR characters)
MEANINGFUL_TEXT_CHAR_THRESHOLD: int = int(os.getenv("MEANINGFUL_TEXT_CHAR_THRESHOLD", "50"))
MEANINGFUL_TEXT_WORD_THRESHOLD: int = int(os.getenv("MEANINGFUL_TEXT_WORD_THRESHOLD", "8"))


# ── Cloud Vision & AI Keys ────────────────────────────────────────────────────
_raw_gemini = os.getenv("GEMINI_API_KEY")
GEMINI_API_KEY: Optional[str] = _raw_gemini.strip() if _raw_gemini and _raw_gemini.strip() else None

_raw_openai = os.getenv("OPENAI_API_KEY")
OPENAI_API_KEY: Optional[str] = _raw_openai.strip() if _raw_openai and _raw_openai.strip() else None

GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-3.5-flash")


def ensure_directories() -> None:
    """Create required directories if they do not already exist."""
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STORAGE_PATH.mkdir(parents=True, exist_ok=True)
