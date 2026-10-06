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


import io

from PIL import Image

from app.body_photos import PhotoError, blurred_jpeg, delete_file, photo_path, process_upload, store
from photo_helpers import gradient, heic, jpeg, png, spread


def opened(data):
    return Image.open(io.BytesIO(data))


def test_metadata_is_stripped_and_the_result_is_a_jpeg():
    cleaned = process_upload(jpeg(exif=True))
    image = opened(cleaned)
    assert image.format == "JPEG" and len(image.getexif()) == 0 and "exif" not in image.info


def test_orientation_is_applied_not_just_dropped():
    cleaned = process_upload(jpeg(size=(300, 200), orientation=6))     # 6 = rotate 90 degrees
    assert opened(cleaned).size == (200, 300)


def test_a_large_image_is_shrunk_to_the_longest_side_limit():
    big = io.BytesIO()
    Image.new("RGB", (3000, 1000), "gray").save(big, "JPEG")
    assert max(opened(process_upload(big.getvalue())).size) == 2000


def test_png_with_transparency_is_flattened_onto_white():
    cleaned = opened(process_upload(png(alpha=True))).convert("RGB")
    assert cleaned.getpixel((5, 5)) == (255, 255, 255)


def test_an_iphone_heic_photo_is_converted():
    assert opened(process_upload(heic())).format == "JPEG"


@pytest.mark.parametrize("data", [b"not an image at all", b"", jpeg()[:40], b"%PDF-1.4 fake"])
def test_files_that_are_not_readable_photos_are_refused_with_a_plain_message(data):
    with pytest.raises(PhotoError, match="not a photo"):
        process_upload(data)


def test_an_oversized_file_is_refused(monkeypatch):
    monkeypatch.setattr(config, "MAX_UPLOAD_BYTES", 100)
    with pytest.raises(PhotoError, match="larger"):
        process_upload(jpeg())


def test_a_tiny_file_with_huge_dimensions_is_refused_before_decoding(monkeypatch):
    monkeypatch.setattr(config, "PHOTO_MAX_PIXELS", 1000)
    with pytest.raises(PhotoError, match="dimensions"):
        process_upload(png(size=(64, 64)))


def test_store_writes_a_random_jpeg_name_and_delete_removes_it():
    name = store(process_upload(jpeg()))
    assert photo_path(name).read_bytes()[:3] == bytes([0xFF, 0xD8, 0xFF]) and name.endswith(".jpg") and len(name) == 36
    assert store(process_upload(jpeg())) != name
    delete_file(name)
    assert not photo_path(name).exists()
    delete_file(name)          # deleting twice, or deleting nothing, is harmless
    delete_file(None)


@pytest.mark.parametrize("name", ["../x.jpg", "a.jpg", "A" * 32 + ".jpg", "a" * 32 + ".png", "a" * 32 + ".jpg/../x", ""])
def test_only_names_this_app_generated_can_be_turned_into_a_path(name):
    with pytest.raises(ValueError):
        photo_path(name)


def test_the_preview_is_blurred_small_and_never_the_original():
    name = store(process_upload(jpeg(size=(600, 400))))
    original = photo_path(name).read_bytes()
    preview = blurred_jpeg(photo_path(name))
    assert preview != original and max(opened(preview).size) <= 480
    assert spread(preview) < spread(original) * 0.6
