"""Whether the New User page is open. The very first account is always allowed (it becomes the admin); after that registration is
closed unless the admin turned on "Allow New Users To Register" in Settings, or AMIDE_REGISTRATION=open / =closed forces it."""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import config
from app.models import InstallationSetting, User

KEY = "registration_open"


def no_accounts_yet(db: Session) -> bool:
    return db.scalar(select(User.id).limit(1)) is None


def admin_choice(db: Session) -> bool:
    row = db.get(InstallationSetting, KEY)
    return row is not None and row.value == "1"


def is_open(db: Session) -> bool:
    if no_accounts_yet(db):
        return True
    forced = config.registration_override()
    if forced is not None:
        return forced == "open"
    return admin_choice(db)


def set_open(db: Session, value: bool) -> None:
    row = db.get(InstallationSetting, KEY)
    if row is None:
        db.add(InstallationSetting(key=KEY, value="1" if value else "0"))
    else:
        row.value = "1" if value else "0"
