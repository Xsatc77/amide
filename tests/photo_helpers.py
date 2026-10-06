"""Builders for body-photo tests: tiny synthetic images only, never real photos."""

import io
import statistics

from PIL import Image, ImageDraw

try:
    from pillow_heif import from_pillow
except ImportError:  # pragma: no cover
    from_pillow = None


def gradient(size=(300, 200)):
    image = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(image)
    for x in range(0, size[0], 6):
        draw.line([(x, 0), (x, size[1])], fill=(x * 255 // size[0], 40, 200 - x * 200 // size[0]), width=3)
    for y in range(0, size[1], 10):
        draw.line([(0, y), (size[0], y)], fill=(0, 0, 0), width=1)
    return image


def jpeg(size=(300, 200), exif=False, orientation=None):
    buffer = io.BytesIO()
    kwargs = {}
    if exif or orientation:
        tags = Image.Exif()
        tags[0x010F] = "TestMake"                    # camera make
        tags[0x8825] = {1: "N", 2: (40.0, 0.0, 0.0)}  # a GPS block
        if orientation:
            tags[0x0112] = orientation
        kwargs["exif"] = tags
    gradient(size).save(buffer, "JPEG", **kwargs)
    return buffer.getvalue()


def png(size=(64, 64), alpha=False):
    buffer = io.BytesIO()
    image = gradient(size).convert("RGBA" if alpha else "RGB")
    if alpha:
        image.putalpha(0)
    image.save(buffer, "PNG")
    return buffer.getvalue()


def heic(size=(64, 64)):
    buffer = io.BytesIO()
    from_pillow(gradient(size)).save(buffer, format="HEIF")
    return buffer.getvalue()


def spread(jpeg_bytes):
    """How much detail an image holds: the standard deviation of its grey levels."""
    grey = Image.open(io.BytesIO(jpeg_bytes)).convert("L")
    return statistics.pstdev(grey.getdata())


import time
from contextlib import contextmanager
from datetime import date

import pyotp
from fastapi.testclient import TestClient

from app.body_photos import delete_file as store_delete, process_upload, store
from app.main import app
from app.models import BodyPhoto, User


@contextmanager
def other_client(username="photoother"):
    """A second signed-in user in a separate browser (not the administrator)."""
    with TestClient(app, follow_redirects=False) as c:
        assert c.post("/notice", data={"understand": "1"}).status_code == 303
        r = c.post("/register", data={"username": username, "password": "Test1!", "confirm": "Test1!"})
        assert r.status_code in (200, 303), r.text
        try:
            yield c
        finally:
            _remove_user(username)


def _remove_user(username):
    """Delete a helper user and everything pointing at it, so the next test starts clean."""
    from app.db import SessionLocal
    from app.models import LoginSession, Share
    with SessionLocal() as s:
        user = s.query(User).filter_by(username_key=username.lower()).first()
        if user is None:
            return
        for photo in s.query(BodyPhoto).filter_by(owner_id=user.id):
            store_delete(photo.filename)
        s.query(BodyPhoto).filter_by(owner_id=user.id).delete()
        s.query(Share).filter((Share.owner_id == user.id) | (Share.grantee_id == user.id)).delete()
        s.query(LoginSession).filter_by(user_id=user.id).delete()
        s.delete(user)
        s.commit()


def enable_2fa(db, user_id):
    secret = pyotp.random_base32()
    user = db.get(User, user_id)
    user.totp_secret, user.totp_enabled, user.totp_last_step = secret, True, None
    db.commit()
    return secret


def code_now(secret):
    return pyotp.TOTP(secret).at(time.time())


def make_photo(db, owner_id, taken_on=None, angle=None, note=None):
    photo = BodyPhoto(owner_id=owner_id, taken_on=taken_on or date(2026, 10, 6), angle=angle, note=note,
                      filename=store(process_upload(jpeg(size=(600, 400)))))
    db.add(photo)
    db.commit()
    return photo
