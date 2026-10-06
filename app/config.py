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
PRICE_LIST_DIR = UPLOAD_DIR / "price_lists"
LAB_REPORT_DIR = UPLOAD_DIR / "lab_reports"
WORKOUT_PDF_DIR = UPLOAD_DIR / "workout_pdfs"
WALLET_QR_DIR = UPLOAD_DIR / "wallet_qr"
# Imported peptide cards (private): cards.json plus one image per card.
LIBRARY_DIR = DATA_DIR / "library"
CARDS_DIR = LIBRARY_DIR / "cards"
CARDS_JSON = LIBRARY_DIR / "cards.json"

BRANDING_DIR = DATA_DIR / "branding"  # optional banner.{svg,png,jpg,jpeg,webp} replaces the built-in one

# Sign-in. Minimum password length is kept low for now; raise it with AMIDE_PASSWORD_MIN_LENGTH.
PASSWORD_MIN_LENGTH = int(os.environ.get("AMIDE_PASSWORD_MIN_LENGTH", "4"))
SESSION_IDLE_MINUTES = 10  # no Amide tab open this long -> signed out, legal notice again
LOCKOUT_ATTEMPTS = 5
LOCKOUT_MINUTES = 15

# Uploads larger than this are rejected.
MAX_UPLOAD_BYTES = int(os.environ.get("AMIDE_MAX_UPLOAD_MB", "15")) * 1024 * 1024

# A backup file (built in memory) larger than this is refused, both when creating and when opening one.
MAX_BACKUP_BYTES = int(os.environ.get("AMIDE_MAX_BACKUP_MB", "512")) * 1024 * 1024


def ensure_dirs() -> None:
    COA_DIR.mkdir(parents=True, exist_ok=True)
    PRICE_LIST_DIR.mkdir(parents=True, exist_ok=True)
    LAB_REPORT_DIR.mkdir(parents=True, exist_ok=True)
    WORKOUT_PDF_DIR.mkdir(parents=True, exist_ok=True)
    WALLET_QR_DIR.mkdir(parents=True, exist_ok=True)
