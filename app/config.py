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
BODY_PHOTO_DIR = UPLOAD_DIR / "body_photos"
PHOTO_UNLOCK_MINUTES = 10
PHOTO_MAX_SIDE = 2000
PHOTO_MAX_PIXELS = 50_000_000
INGEST_DIR = UPLOAD_DIR / "ingest"
INGEST_WORKER_ENABLED = os.environ.get("AMIDE_INGEST_WORKER", "1") != "0"
REMINDERS_ENABLED = os.environ.get("AMIDE_REMINDERS", "1") != "0"           # the dose-reminder loop (it only acts for users who turned reminders on)
BACKUP_PASSPHRASE = os.environ.get("AMIDE_BACKUP_PASSPHRASE", "")             # set it and Amide writes an encrypted whole-installation backup to data/backups on a schedule
BACKUP_EVERY_DAYS = int(os.environ.get("AMIDE_BACKUP_DAYS", "7"))
BACKUP_KEEP = int(os.environ.get("AMIDE_BACKUP_KEEP", "4"))                    # how many automatic backups to keep
LINK_DESCRIPTIONS = os.environ.get("AMIDE_LINK_DESCRIPTIONS", "1") != "0"        # read a short description from a saved link's site when none was written
USDA_API_KEY = os.environ.get("AMIDE_USDA_API_KEY", "")                       # a free FoodData Central key turns on "Search USDA" in the Add food dialog
NTFY_SERVER = os.environ.get("AMIDE_NTFY_SERVER", "https://ntfy.sh")          # where reminders are posted: ntfy.sh or your own ntfy server
INGEST_SETTLE_SECONDS = 60
INGEST_CLUSTER_MINUTES = 5
INGEST_MAX_FILE_BYTES = 25 * 1024 * 1024
INGEST_MAX_FILES = 10
INGEST_MAX_TEXT = 8000
INGEST_MAX_IMAGES = 20
INGEST_RATE_PER_MINUTE = 60
INGEST_NEW_LIST_DAYS = 7
# Imported peptide cards (private): cards.json plus one image per card.
LIBRARY_DIR = DATA_DIR / "library"
CARDS_DIR = LIBRARY_DIR / "cards"
CARDS_JSON = LIBRARY_DIR / "cards.json"

BRANDING_DIR = DATA_DIR / "branding"  # optional banner.{svg,png,jpg,jpeg,webp} replaces the built-in one

# Sign-in. Minimum password length (AMIDE_PASSWORD_MIN_LENGTH); the complexity rules in app/auth/passwords.py apply on top of it.
PASSWORD_MIN_LENGTH = int(os.environ.get("AMIDE_PASSWORD_MIN_LENGTH", "8"))
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
    BODY_PHOTO_DIR.mkdir(parents=True, exist_ok=True)
    INGEST_DIR.mkdir(parents=True, exist_ok=True)
