"""Amide can be installed from the browser to a phone's or computer's home screen: manifest, icons and a service worker."""

import json
import struct
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app

STATIC = Path(__file__).resolve().parent.parent / "app" / "static"


def png_size(path: Path) -> tuple[int, int]:
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    return struct.unpack(">II", data[16:24])


def test_the_manifest_names_the_app_and_points_at_real_icons():
    manifest = json.loads((STATIC / "manifest.webmanifest").read_text(encoding="utf-8"))
    assert manifest["name"] == "Amide" and manifest["display"] == "standalone" and manifest["start_url"] == "/dashboard" and manifest["scope"] == "/"
    sizes = {i["sizes"]: i for i in manifest["icons"]}
    assert {"192x192", "512x512"} <= set(sizes)
    for icon in manifest["icons"]:
        w, h = icon["sizes"].split("x")
        assert png_size(STATIC / icon["src"].removeprefix("/static/")) == (int(w), int(h))
    assert any("maskable" in i.get("purpose", "") for i in manifest["icons"])


def test_every_page_links_the_manifest_and_icons_and_registers_the_service_worker(client, db):
    page = client.get("/dashboard").text
    assert 'rel="manifest" href="/static/manifest.webmanifest"' in page or "manifest.webmanifest" in page
    assert 'name="theme-color"' in page and 'rel="apple-touch-icon"' in page and "serviceWorker.register" in page


def test_the_manifest_and_service_worker_are_reachable_without_signing_in():
    with TestClient(app) as anon:
        assert anon.get("/static/manifest.webmanifest").status_code == 200
        sw = anon.get("/sw.js")
        assert sw.status_code == 200 and "javascript" in sw.headers["content-type"] and sw.headers["service-worker-allowed"] == "/"
        assert "fetch" in sw.text
