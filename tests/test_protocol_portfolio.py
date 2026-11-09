"""Protocol Portfolio: one PDF with the protocol, each peptide's Library page (no dosing chart) and a cost breakdown with the best 3 buys."""

from datetime import date, timedelta
from io import BytesIO

import pytest
from pypdf import PdfReader

from app.db import SessionLocal
from app.models import DoseUnit, DosingTierLevel, Frequency, Peptide, PeptideDosingTier, PeptideSource, Protocol, ProtocolItem, Route
from photo_helpers import other_client
from price_helpers import item, make_list, make_vendor
from test_shop_protocol import bac_world

TODAY = date.today()


def peptide(name, **kw):
    with SessionLocal() as s:
        p = Peptide(name=name, source=PeptideSource.CUSTOM, **kw)
        s.add(p)
        s.commit()
        return p.id


def protocol(uid, peptide_ids, *, days=40, name="Portfolio Course", end=True):
    with SessionLocal() as s:
        p = Protocol(name=name, start_date=TODAY, end_date=TODAY + timedelta(days=days - 1) if end else None, owner_id=uid)
        s.add(p)
        s.flush()
        for pid in peptide_ids:
            s.add(ProtocolItem(protocol_id=p.id, peptide_id=pid, dose=250, dose_unit=DoseUnit.MCG, frequency=Frequency.DAILY, route=Route.SUBQ))
        s.commit()
        return p.id


def pdf_text(response) -> str:
    assert response.status_code == 200, response.text[:200]
    return "\n".join(page.extract_text() for page in PdfReader(BytesIO(response.content)).pages)


def test_it_downloads_as_a_pdf_named_after_the_protocol(client, db, me):
    pid = peptide("Portozine")
    response = client.get(f"/protocols/{protocol(me, [pid], name='Portfolio Name Check')}/portfolio.pdf")
    assert response.status_code == 200 and response.content.startswith(b"%PDF")
    assert response.headers["content-type"] == "application/pdf"
    assert 'filename="protocol-portfolio-portfolio-name-check.pdf"' in response.headers["content-disposition"]


def test_the_protocol_comes_first_then_each_peptides_library_page_then_the_cost_page(client, db, me):
    a = peptide("Portozine", card_details={"quick_read": ["Portozine library line"]})
    b = peptide("Portavex", card_details={"quick_read": ["Portavex library line"]})
    text = pdf_text(client.get(f"/protocols/{protocol(me, [a, b], name='Portfolio Order Check')}/portfolio.pdf"))
    order = [text.find(s) for s in ("Portfolio Order Check", "Portozine library line", "Portavex library line", "Cost Breakdown")]
    assert -1 not in order and order == sorted(order)


def test_the_dosing_chart_is_left_out(client, db, me):
    pid = peptide("Portodose", card_details={"quick_read": ["Portodose library line"]})
    with SessionLocal() as s:
        s.add(PeptideDosingTier(peptide_id=pid, level=DosingTierLevel.BEGINNER, dose_text="QQ-DOSE-91 mcg", frequency_text="QQ-FREQ-91"))
        s.commit()
    text = pdf_text(client.get(f"/protocols/{protocol(me, [pid])}/portfolio.pdf"))
    assert "Portodose library line" in text
    assert "QQ-DOSE-91" not in text and "Dosing (Community)" not in text


def test_the_best_three_buys_per_peptide_cheapest_first_and_the_dearest_left_off(client, db, me):
    with SessionLocal() as s:
        c = Peptide(name="Portbuy", source=PeptideSource.CUSTOM)
        s.add(c)
        s.commit()
        pid = c.id
    for vendor_name, price in {"Alfa Supply": 300, "Bravo Supply": 120, "Charlie Supply": 200, "Delta Supply": 410}.items():
        make_list(db, make_vendor(db, vendor_name), TODAY, item("Portbuy", 10, price, card=c))
    text = pdf_text(client.get(f"/protocols/{protocol(me, [pid])}/portfolio.pdf"))     # 250 mcg a day for 40 days: 10 mg
    assert "Bravo Supply" in text and "Charlie Supply" in text and "Alfa Supply" in text
    assert "Delta Supply" not in text                                                    # the fourth and dearest
    assert text.find("Bravo Supply") < text.find("Charlie Supply") < text.find("Alfa Supply")
    assert "$120.00" in text


def test_the_bac_water_estimate_for_the_whole_protocol_is_on_the_last_page(client, db, me):
    pid = peptide("Portwater", normally_supplied_amount=10, normally_supplied_unit=DoseUnit.MG)
    last = PdfReader(BytesIO(client.get(f"/protocols/{protocol(me, [pid], days=200)}/portfolio.pdf").content)).pages[-1].extract_text()
    assert "BAC Water For The Full Protocol" in last and "mL over 200 days" in last and "bottles of 30 mL" in last


def test_a_ranked_brand_of_bac_water_is_priced_on_the_last_page(client, db, me):
    pid = bac_world(db, me)                                                  # a ranked brand on a price list; a 40-day course
    text = pdf_text(client.get(f"/protocols/{pid}/portfolio.pdf"))
    assert "Acme Hospira Bacteriostatic Water" in text and "Sterile Supply Co" in text and "$48.00" in text


def test_a_protocol_without_an_end_date_says_why_there_are_no_prices(client, db, me):
    pid = peptide("Portopen")
    text = pdf_text(client.get(f"/protocols/{protocol(me, [pid], end=False)}/portfolio.pdf"))
    assert "no end date" in text and "Cost Breakdown" in text


def test_greek_letters_and_symbols_in_a_name_do_not_break_the_pdf(client, db, me):
    pid = peptide("Thymosin β4 ≥ test", card_details={"quick_read": ["α-helix → fine"]})
    text = pdf_text(client.get(f"/protocols/{protocol(me, [pid])}/portfolio.pdf"))
    assert "beta4" in text and "alpha-helix -> fine" in text


def test_someone_elses_protocol_is_a_404(client, db, me):
    pid = protocol(me, [peptide("Portprivate")])
    with other_client("portother") as stranger:
        assert stranger.get(f"/protocols/{pid}/portfolio.pdf").status_code == 404
    assert client.get("/protocols/999999/portfolio.pdf").status_code == 404


def test_the_button_is_on_the_saved_protocols_table_as_well_as_the_cards(client, db, me):
    pid = protocol(me, [peptide("Portbutton")], name="Portfolio Button Check")
    with SessionLocal() as s:
        s.get(Protocol, pid).end_date = TODAY - timedelta(days=1)         # ended: listed in Saved Protocols
        s.commit()
    page = client.get("/protocols").text
    saved = page[page.index('id="saved-heading"'):]
    assert f'data-portfolio-url="/protocols/{pid}/portfolio.pdf"' in saved


def test_a_powder_with_no_vial_size_on_file_still_gets_bac_water_from_the_vials_it_buys(client, db, me):
    with SessionLocal() as s:
        c = Peptide(name="Portpowder", source=PeptideSource.CUSTOM)
        s.add(c)
        s.commit()
        pid = c.id
    make_list(db, make_vendor(db, "Echo Supply"), TODAY, item("Portpowder", 10, 100, card=db.get(Peptide, pid)))
    text = pdf_text(client.get(f"/protocols/{protocol(me, [pid], days=40)}/portfolio.pdf"))     # 10 mg for the course: 2 vials of 10 mg
    assert "This course needs no BAC water" not in text
    assert "3.0 mL over 40 days" in text


def test_a_peptide_with_no_price_at_all_assumes_five_mg_vials_and_says_so(client, db, me):
    pid = peptide("Portunpriced")
    text = pdf_text(client.get(f"/protocols/{protocol(me, [pid], days=40)}/portfolio.pdf"))     # 10 mg for the course: 3 assumed 5 mg vials
    assert "This course needs no BAC water" not in text
    assert "Portunpriced: the water assumes 5 mg vials" in text.replace("\n", " ")


def test_the_button_says_why_the_pdf_failed(client, db, me):
    js = client.get("/static/js/protocol-portfolio.js").text
    assert "Server error" in js and "no answer from the server" in js


def test_a_protocol_that_has_not_started_says_when_it_starts_not_day_none(client, db, me):
    pid = peptide("Futurezine")
    with SessionLocal() as s:
        p = Protocol(name="Future Course", start_date=TODAY + timedelta(days=30), owner_id=me)
        s.add(p); s.flush()
        s.add(ProtocolItem(protocol_id=p.id, peptide_id=pid, dose=1, dose_unit=DoseUnit.MG, frequency=Frequency.DAILY, route=Route.SUBQ))
        s.commit()
    t = client.get("/protocols").text
    assert "Day None" not in t and "Starts " in t


def test_a_pricing_failure_still_gives_a_pdf_with_a_note_on_the_cost_page(client, db, me, monkeypatch):
    import app.routers.protocol_portfolio as mod

    def boom(*a, **k):
        raise RuntimeError("price maths broke")
    monkeypatch.setattr(mod, "portfolio_prices", boom)
    pid = peptide("Brokezine")
    response = client.get(f"/protocols/{protocol(me, [pid])}/portfolio.pdf")
    assert "could not be worked out" in pdf_text(response)
