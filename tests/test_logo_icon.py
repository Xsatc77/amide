"""The Amide logo for dashboards such as Homepage: shipped at /app/public/icons/amide.png in the image and served at /static/img/amide.png."""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PNG = b"\x89PNG\r\n\x1a\n"


def test_the_logo_is_a_png_in_the_image_and_in_static():
    for path in (ROOT / "public" / "icons" / "amide.png", ROOT / "app" / "static" / "img" / "amide.png"):
        assert path.read_bytes().startswith(PNG)
    assert "COPY public ./public" in (ROOT / "Dockerfile").read_text(encoding="utf-8")


def test_the_logo_is_served_without_signing_in():
    from fastapi.testclient import TestClient
    from app.main import app
    r = TestClient(app).get("/static/img/amide.png")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png" and r.content.startswith(PNG)
