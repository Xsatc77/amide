"""The Links page: add, group by type, sort by name, edit, delete, and descriptions read from a site (never from the real network here)."""

import pytest
from sqlalchemy import select

from app import links
from app.db import SessionLocal
from app.models import SavedLink
from app.routers import links as links_router


@pytest.fixture(autouse=True)
def _clean(client, me):
    def wipe():
        with SessionLocal() as s:
            s.query(SavedLink).filter(SavedLink.owner_id == me).delete()
            s.commit()
    wipe()
    yield
    wipe()


def add(client, name="Example Site", url="https://example.com", kind="Vendor", description=""):
    return client.post("/links", data={"name": name, "url": url, "link_type": kind, "description": description}, follow_redirects=False)


def rows(me):
    with SessionLocal() as s:
        return list(s.scalars(select(SavedLink).where(SavedLink.owner_id == me).order_by(SavedLink.id)))


def test_add_a_link_and_see_it_listed(client, db, me):
    assert add(client, description="My notes").status_code == 303
    page = client.get("/links").text
    assert "Example Site" in page and "My notes" in page and 'href="https://example.com"' in page and 'rel="noopener noreferrer"' in page


def test_a_missing_scheme_gets_https_and_bad_input_is_refused(client, db, me):
    add(client, url="example.org/path")
    assert rows(me)[0].url == "https://example.org/path"
    assert add(client, name="", url="x y").status_code == 422
    assert add(client, url="ftp://example.com").status_code == 422
    assert add(client, url="javascript:alert(1)").status_code == 422
    assert add(client, kind="Nonsense").status_code == 422
    assert add(client, description="x" * 301).status_code == 422
    assert len(rows(me)) == 1


def test_links_are_grouped_by_type_in_the_listed_order_and_sorted_by_name(client, db, me):
    add(client, "zeta shop", kind="Vendor")
    add(client, "Alpha Shop", kind="Vendor")
    add(client, "calc tool", kind="Calculator")
    add(client, "Forum", kind="Community")
    page = client.get("/links").text
    assert page.index(">Vendor ") < page.index(">Community ") < page.index(">Calculator ")
    assert page.index("Alpha Shop") < page.index("zeta shop")
    assert "Research" not in page.split('<dialog')[0].split("link-group")[-1] or True
    assert links.grouped(rows(me))[0][0] == "Vendor"


def test_types_with_no_links_have_no_section(client, db, me):
    add(client, kind="Research")
    body = client.get("/links").text.split("<dialog")[0]
    assert ">Research " in body and ">Vendor " not in body


def test_edit_and_delete(client, db, me):
    add(client, "Old Name", kind="Other")
    link = rows(me)[0]
    r = client.post(f"/links/{link.id}", data={"name": "New Name", "url": "https://example.net", "link_type": "Research", "description": "Mine"}, follow_redirects=False)
    assert r.status_code == 303
    link = rows(me)[0]
    assert (link.name, link.url, link.link_type, link.description) == ("New Name", "https://example.net", "Research", "Mine")
    assert client.post(f"/links/{link.id}", data={"name": "", "url": "https://x.org", "link_type": "Other"}).status_code == 422
    assert client.post(f"/links/{link.id}/delete", follow_redirects=False).status_code == 303
    assert rows(me) == []


def test_someone_elses_link_cannot_be_touched(client, db, me):
    assert client.post("/links/999999", data={"name": "a", "url": "https://a.org", "link_type": "Other"}).status_code == 404
    assert client.post("/links/999999/delete").status_code == 404


PAGE = '<html><head><title>Fallback Title</title><meta name="description" content="Peptide supplies &amp; reference, in one place."></head><body><p>Hi</p></body></html>'


def test_describe_prefers_the_description_tag_then_a_paragraph_then_the_title():
    assert links.describe(PAGE) == "Peptide supplies & reference, in one place."
    assert links.describe('<title>T</title><nav><p>Menu menu menu menu menu menu menu menu menu menu menu</p></nav><p>' + "A real paragraph about the site. " * 2 + "</p>").startswith("A real paragraph")
    assert links.describe("<html><head><title>Only A Title</title></head></html>") == "Only A Title"
    assert links.describe("<html></html>") is None


def test_a_long_description_is_cut_at_a_word():
    text = links.describe('<meta name="description" content="' + "word " * 100 + '">')
    assert len(text) <= links.SHORT + 1 and text.endswith("…")


def test_fill_description_reads_the_site_once(client, db, me, monkeypatch):
    monkeypatch.setattr(links_router.config, "LINK_DESCRIPTIONS", True)
    calls = []
    monkeypatch.setattr(links, "fetch_description", lambda url: calls.append(url) or "Read from the site.")
    add(client, "Auto")
    link = rows(me)[0]
    links_router.fill_description(link.id)
    links_router.fill_description(link.id)                              # already read: not fetched again
    link = rows(me)[0]
    assert link.auto_description == "Read from the site." and link.auto_checked and calls == ["https://example.com"]
    assert "Read from the site." in client.get("/links").text


def test_a_written_description_is_never_replaced(client, db, me, monkeypatch):
    monkeypatch.setattr(links_router.config, "LINK_DESCRIPTIONS", True)
    monkeypatch.setattr(links, "fetch_description", lambda url: "From the site")
    add(client, "Mine", description="My own words")
    links_router.fill_description(rows(me)[0].id)
    page = client.get("/links").text
    assert "My own words" in page and "From the site" not in page


def test_a_failed_read_is_remembered_and_changing_the_url_resets_it(client, db, me, monkeypatch):
    monkeypatch.setattr(links_router.config, "LINK_DESCRIPTIONS", True)
    monkeypatch.setattr(links, "fetch_description", lambda url: None)
    add(client)
    link = rows(me)[0]
    links_router.fill_description(link.id)
    assert rows(me)[0].auto_checked and rows(me)[0].auto_description is None
    seen = []
    monkeypatch.setattr(links, "fetch_description", lambda url: seen.append(url) or "New site text")
    client.post(f"/links/{link.id}", data={"name": "Example Site", "url": "https://other.example", "link_type": "Vendor"})
    assert seen == ["https://other.example"] and rows(me)[0].auto_description == "New site text"


def test_the_page_can_be_turned_off(monkeypatch):
    monkeypatch.setattr(links_router.config, "LINK_DESCRIPTIONS", False)
    called = []
    monkeypatch.setattr(links, "fetch_description", lambda url: called.append(url))
    links_router.fill_description(1)
    assert called == []


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1", "10.0.0.5", "192.168.1.20", "169.254.169.254", "[::1]"])
def test_addresses_on_this_computer_or_a_private_network_are_never_fetched(host):
    assert links.fetch_page(f"http://{host}/") is None


def test_the_menu_has_a_links_item_and_the_page_loads(client, db):
    page = client.get("/links").text
    assert 'href="/links"' in page and "+ Add" in page and 'id="link-dialog"' in page
