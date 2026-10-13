"""The body-photo upload dialog is wired for in-browser shrinking and visible errors (the script itself runs only in a browser)."""

from pathlib import Path

JS = Path(__file__).parent.parent / "app" / "static" / "js" / "body-photos.js"


def test_the_upload_dialog_has_a_visible_error_slot_and_a_save_button_the_script_can_drive(client):
    page = client.get("/measurements?tab=measurements").text
    assert "data-photo-upload-error" in page and "data-photo-save" in page


def test_the_script_shrinks_photos_and_explains_a_refused_upload():
    js = JS.read_text(encoding="utf-8")
    assert "canvas" in js and "toBlob" in js and "MAX_SIDE" in js
    assert "413" in js and "422" in js and "Could not reach Amide" in js
