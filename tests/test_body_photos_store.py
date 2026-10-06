from datetime import date

import pytest
from sqlalchemy.exc import IntegrityError

from app import config
from app.models import BodyPhoto, LoginSession, User


def test_a_photo_row_round_trips_and_defaults_are_off(db, me):
    db.add(BodyPhoto(owner_id=me, taken_on=date(2026, 10, 6), angle="front", note="start", filename="a" * 32 + ".jpg"))
    db.commit()
    photo = db.query(BodyPhoto).one()
    assert (photo.angle, photo.note, photo.created_at is not None) == ("front", "start", True)
    assert db.get(User, me).photo_2fa_required is False
    assert db.query(LoginSession).first().photo_unlocked_until is None


def test_an_unknown_angle_is_refused_by_the_database(db, me):
    db.add(BodyPhoto(owner_id=me, taken_on=date(2026, 10, 6), angle="top", filename="b" * 32 + ".jpg"))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_the_photo_folder_is_created_with_the_other_upload_folders():
    config.ensure_dirs()
    assert config.BODY_PHOTO_DIR.is_dir() and config.PHOTO_UNLOCK_MINUTES == 10
