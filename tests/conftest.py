import os
import shutil
import tempfile

import pytest

# Point the app at a throwaway data dir *before* app modules are imported.
_TMP = tempfile.mkdtemp(prefix="amide-test-")
os.environ["AMIDE_DATA_DIR"] = _TMP
os.environ["AMIDE_INGEST_WORKER"] = "0"
os.environ["AMIDE_REMINDERS"] = "0"
os.environ["AMIDE_LINK_DESCRIPTIONS"] = "0"      # tests never read real sites; they call the reader with a fake page
os.environ["AMIDE_PASSWORD_MIN_LENGTH"] = "4"      # the suite signs in with short throwaway passwords; the shipped default is tested in test_auth_rules

from fastapi.testclient import TestClient  # noqa: E402

from app import config  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.models import (  # noqa: E402
    BodyMeasurement, BodyPhoto, DashboardDismissal, FitnessTestResult, IngestItem, IngestSource, IngestToken, IngestTopic, Food, FoodLog, LoginSession, InventoryItem, Peptide, PeptideSource, PriceAlertIgnore, PriceList, Protocol, User, Vendor, WorkoutLog, WorkoutPlan,
)
from sqlalchemy import select, update  # noqa: E402


TEST_USER = "Tester"
TEST_PASSWORD = "Test1!"


@pytest.fixture(scope="session")
def client():
    """A browser that accepted the legal notice and registered the first account (the admin)."""
    with TestClient(app) as c:  # runs startup: creates dirs + migrates
        assert c.post("/notice", data={"understand": "1"}).status_code == 200
        r = c.post("/register", data={"username": TEST_USER, "password": TEST_PASSWORD, "confirm": TEST_PASSWORD})
        assert r.status_code == 200 and r.url.path == "/dashboard", r.text
        yield c
    shutil.rmtree(_TMP, ignore_errors=True)


@pytest.fixture
def me(client):
    """The signed-in test user's id."""
    with SessionLocal() as s:
        return s.scalar(select(User.id).where(User.username_key == TEST_USER.lower()))


@pytest.fixture(autouse=True)
def clean(client):
    from app.ingest import tokens
    tokens.limiter._hits.clear()          # the per-token request counter must not leak from one test into the next
    yield
    with SessionLocal() as s:
        s.query(PriceAlertIgnore).delete()
        s.query(PriceList).delete()
        s.query(Vendor).delete()
        s.query(Protocol).delete()
        s.query(Peptide).filter(Peptide.source.in_((PeptideSource.CUSTOM, PeptideSource.SHEET))).delete()
        s.query(InventoryItem).delete()
        s.query(WorkoutLog).delete()
        s.query(BodyMeasurement).delete()
        s.query(BodyPhoto).delete()
        s.query(IngestItem).delete()
        s.query(IngestTopic).delete()
        s.query(IngestSource).delete()
        s.query(IngestToken).delete()
        s.query(DashboardDismissal).delete()
        s.query(FoodLog).delete()
        s.query(Food).delete()
        s.query(WorkoutPlan).delete()
        s.query(FitnessTestResult).delete()
        s.execute(update(User).where(User.username_key == TEST_USER.lower()).values(
            totp_enabled=False, totp_secret=None, totp_last_step=None, photo_2fa_required=False,
            failed_attempts=0, locked_until=None))
        s.execute(update(LoginSession).values(photo_unlocked_until=None))
        s.commit()
    for f in config.COA_DIR.glob("*"):
        f.unlink()
    for f in config.WALLET_QR_DIR.glob("*"):
        f.unlink()
    for f in config.BODY_PHOTO_DIR.glob("*"):
        f.unlink()
    for f in config.INGEST_DIR.glob("*"):
        f.unlink()


@pytest.fixture
def db():
    with SessionLocal() as s:
        yield s
