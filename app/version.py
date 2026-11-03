"""Which build of Amide is running, shown in the footer and in Settings. The image is built with AMIDE_VERSION set to its release tag (1.1.27);
a build from a branch says the branch, and a checkout run by hand has none and says "development"."""

import os

from sqlalchemy import text
from sqlalchemy.orm import Session


def app_version() -> str:
    return (os.environ.get("AMIDE_VERSION") or "").strip() or "development"


def label() -> str:
    """"v1.1.27", or the plain word for a build that is not a release."""
    v = app_version()
    return f"v{v}" if v[:1].isdigit() else v


def database_revision(session: Session) -> str | None:
    """The newest migration the database has been brought to, which shows whether the code and the data match."""
    return session.execute(text("SELECT version_num FROM alembic_version")).scalar()
