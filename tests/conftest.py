import os
import shutil
import tempfile

import pytest

# Point the app at a throwaway data dir *before* app modules are imported.
_TMP = tempfile.mkdtemp(prefix="amide-test-")
os.environ["AMIDE_DATA_DIR"] = _TMP

from fastapi.testclient import TestClient  # noqa: E402

from app import config  # noqa: E402
from app.db import SessionLocal  # noqa: E402
from app.main import app  # noqa: E402
from app.models import InventoryItem, Peptide, PeptideSource, Protocol, User, Vendor  # noqa: E402
from sqlalchemy import select  # noqa: E402


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
    yield
    with SessionLocal() as s:
        s.query(Vendor).delete()
        s.query(Protocol).delete()
        s.query(Peptide).filter(Peptide.source.in_((PeptideSource.CUSTOM, PeptideSource.SHEET))).delete()
        s.query(InventoryItem).delete()
        s.commit()
    for f in config.COA_DIR.glob("*"):
        f.unlink()


@pytest.fixture
def db():
    with SessionLocal() as s:
        yield s
