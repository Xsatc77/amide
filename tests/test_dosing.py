from datetime import date

from app.calendar.schedule import DueItem, Occurrence, missed_items
from app.dosing.site import eligible_sites, recommend
from app.models import InjectionSite, Route


def _item(protocol_item_id=1, route="subq"):
    return DueItem(peptide="Retatrutide", peptide_id=1, protocol_item_id=protocol_item_id, dose=2.0,
                  unit="mg", step=None, time_of_day=None, route=route, inventory=None)


def test_due_item_carries_protocol_item_id():
    item = _item(protocol_item_id=42)
    assert item.protocol_item_id == 42


def test_missed_items_excludes_today_and_logged():
    occ_past_logged = Occurrence(date(2026, 9, 20), 1, "Fat Loss", [_item(protocol_item_id=1)])
    occ_past_unlogged = Occurrence(date(2026, 9, 21), 1, "Fat Loss", [_item(protocol_item_id=2)])
    occ_today = Occurrence(date(2026, 9, 25), 1, "Fat Loss", [_item(protocol_item_id=3)])
    logged = {(1, date(2026, 9, 20))}
    result = missed_items([occ_past_logged, occ_past_unlogged, occ_today], logged, today=date(2026, 9, 25))
    assert len(result) == 1
    occ, item = result[0]
    assert occ.date == date(2026, 9, 21) and item.protocol_item_id == 2


def test_eligible_sites_subq_excludes_glute():
    sites = eligible_sites("subq")
    assert InjectionSite.GLUTE_L not in sites and InjectionSite.GLUTE_R not in sites
    assert InjectionSite.ABDOMEN_L in sites


def test_eligible_sites_im_includes_glute():
    sites = eligible_sites("im")
    assert InjectionSite.GLUTE_L in sites


def test_eligible_sites_non_injection_route_is_empty():
    assert eligible_sites("oral") == []


def test_recommend_mirrors_opposite_side_same_body_part():
    assert recommend(InjectionSite.ABDOMEN_L, "subq") == InjectionSite.ABDOMEN_R
    assert recommend(InjectionSite.THIGH_R, "subq") == InjectionSite.THIGH_L


def test_recommend_none_when_no_prior_site():
    assert recommend(None, "subq") is None


def test_recommend_never_crosses_body_parts():
    # Even though Glute isn't eligible for subq, recommend() must never suggest a DIFFERENT body
    # part just because the mirrored one isn't available -- it returns None in that case, not a guess.
    assert recommend(InjectionSite.GLUTE_L, "subq") is None
