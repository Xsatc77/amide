"""A vendor's Recommended tag can be taken off with one button, back to No opinion."""

import pytest

from app.db import SessionLocal
from app.models import Vendor


@pytest.fixture(autouse=True)
def clean(client, me):
    yield
    with SessionLocal() as s:
        s.query(Vendor).filter(Vendor.name.like("Clearrec%")).delete(synchronize_session=False)
        s.commit()


def vendor(flag):
    with SessionLocal() as s:
        v = Vendor(name="Clearrec Co", recommended=flag)
        s.add(v)
        s.commit()
        return v.id


def state(vid):
    with SessionLocal() as s:
        return s.get(Vendor, vid).recommended


@pytest.mark.parametrize("flag", [True, False])
def test_the_button_clears_the_tag_and_returns_to_the_vendor(client, me, flag):
    vid = vendor(flag)
    assert "clear-recommendation" in client.get(f"/vendors/{vid}").text
    r = client.post(f"/vendors/{vid}/clear-recommendation", data={"next": f"/vendors/{vid}"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == f"/vendors/{vid}"
    assert state(vid) is None
    assert "clear-recommendation" not in client.get(f"/vendors/{vid}").text


def test_the_list_offers_it_too_and_a_vendor_without_a_tag_has_no_button(client, me):
    vid = vendor(True)
    assert f"/vendors/{vid}/clear-recommendation" in client.get("/vendors").text
    client.post(f"/vendors/{vid}/clear-recommendation", follow_redirects=False)
    assert f"/vendors/{vid}/clear-recommendation" not in client.get("/vendors").text


def test_it_will_not_redirect_off_site_and_a_missing_vendor_is_404(client, me):
    vid = vendor(True)
    r = client.post(f"/vendors/{vid}/clear-recommendation", data={"next": "//evil.example"}, follow_redirects=False)
    assert r.headers["location"] == "/vendors"
    assert client.post("/vendors/999999/clear-recommendation").status_code == 404
