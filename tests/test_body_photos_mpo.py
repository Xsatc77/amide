"""An iPhone Portrait or depth photo is an MPO (a JPEG with a second picture attached) and is a photo like any other."""

import io

from PIL import Image

from app.body_photos import process_upload


def test_an_mpo_photo_is_accepted_and_comes_out_as_a_clean_jpeg():
    first, second = Image.new("RGB", (300, 200), "red"), Image.new("RGB", (300, 200), "blue")
    raw = io.BytesIO()
    first.save(raw, "MPO", save_all=True, append_images=[second])
    assert Image.open(io.BytesIO(raw.getvalue())).format == "MPO"
    out = Image.open(io.BytesIO(process_upload(raw.getvalue())))
    assert out.format == "JPEG" and out.size == (300, 200) and getattr(out, "n_frames", 1) == 1


def test_a_refused_photo_is_logged_with_the_reason_but_nothing_about_the_picture(caplog):
    import pytest
    from app.body_photos import PhotoError
    with caplog.at_level("WARNING", logger="app.body_photos"), pytest.raises(PhotoError):
        process_upload(b"\xff\xd8\xff\xe0 this is not really a jpeg")
    assert "photo refused" in caplog.text and "bytes" in caplog.text
