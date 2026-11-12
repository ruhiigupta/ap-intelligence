"""
run.py — Development launcher for AP Intelligence backend.

Ensures the backend/ directory is on sys.path before uvicorn boots,
making this reliable on Windows regardless of how or from where it is invoked.

Usage (from workspace root, with venv active):
    python backend/run.py
  or (from inside backend/):
    python run.py
"""

import sys
import os

# Pin backend/ onto sys.path so `import app.*` always resolves correctly.
BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

# Now it is safe to import config (which loads .env).
from app.config import APP_HOST, APP_PORT, LOG_LEVEL  # noqa: E402

import uvicorn  # noqa: E402

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host=APP_HOST,
        port=APP_PORT,
        reload=True,
        log_level=LOG_LEVEL.lower(),
    )
