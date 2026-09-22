"""Runtime settings, read from environment variables.

Everything Amide stores (the SQLite database and uploaded files) lives under
AMIDE_DATA_DIR, so backing up that one folder backs up all of your data.
"""

import os
from pathlib import Path

DATA_DIR = Path(os.environ.get("AMIDE_DATA_DIR", Path(__file__).resolve().parent.parent / "data"))
DATABASE_URL = os.environ.get("AMIDE_DATABASE_URL", f"sqlite:///{(DATA_DIR / 'amide.db').as_posix()}")
UPLOAD_DIR = DATA_DIR / "uploads"
COA_DIR = UPLOAD_DIR / "coa"

# Uploads larger than this are rejected.
MAX_UPLOAD_BYTES = int(os.environ.get("AMIDE_MAX_UPLOAD_MB", "15")) * 1024 * 1024


def ensure_dirs() -> None:
    COA_DIR.mkdir(parents=True, exist_ok=True)
