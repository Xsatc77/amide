from datetime import date
from types import SimpleNamespace as NS

from app.models import DoseUnit, Frequency, Medium, PurchasingUnit
from app.protocols.course_totals import compute_course_totals


def peptide_item(name="BPC-157", dose=250.0, unit=DoseUnit.MCG, freq=Frequency.DAILY, every_n=None,
                 weekdays=None, steps=(), cycle_offs=(), inventory_item_id=None):
    return NS(id=1, peptide=NS(name=name, library_specifications=None), dose=dose, dose_unit=unit, frequency=freq,
              every_n_days=every_n, weekdays=weekdays, steps=list(steps), cycle_offs=list(cycle_offs),
              inventory_item_id=inventory_item_id)


def proto(start=date(2026, 1, 1), end=date(2026, 1, 14), titration=False, items=None):
    return NS(start_date=start, end_date=end, titration_enabled=titration, items=items or [peptide_item()])


def inv(id=1, vial_size_mg=100.0, vial_size_unit=DoseUnit.MG, purchasing_unit=PurchasingUnit.INDIVIDUAL,
        medium=Medium.LYOPHILIZED):
    return NS(id=id, vial_size_mg=vial_size_mg, vial_size_unit=vial_size_unit, purchasing_unit=purchasing_unit,
              medium=medium)


def test_no_end_date_means_no_totals():
    p = proto(end=None)
    assert compute_course_totals(p, {}) is None


def test_simple_daily_dose_sums_correctly_in_its_own_unit():
    # 14 days, 250 mcg/day = 3500 mcg total, no inventory item linked.
    p = proto(items=[peptide_item(dose=250.0, unit=DoseUnit.MCG, inventory_item_id=None)])
    [total] = compute_course_totals(p, {})
    assert total.unit == "mcg" and total.total_amount == 3500.0
    assert total.vials_estimate is None and "No inventory item linked" in total.note


def test_vial_estimate_rounds_up_any_positive_remainder():
    # 3500 mcg = 3.5 mg total dose; a 1 mg vial needs 3.5 vials -> rounds up to 4.
    p = proto(items=[peptide_item(dose=250.0, unit=DoseUnit.MCG, inventory_item_id=1)])
    inventory = {1: inv(vial_size_mg=1.0, vial_size_unit=DoseUnit.MG)}
    [total] = compute_course_totals(p, inventory)
    assert total.vials_estimate == 4
    assert total.bac_water_ml == 6.0  # 4 vials * 1.5 mL


def test_vial_estimate_does_not_round_up_an_exact_whole_number():
    # 14 days * 250 mcg = 3.5 mg; a 0.875 mg vial divides evenly into exactly 4 vials.
    p = proto(items=[peptide_item(dose=250.0, unit=DoseUnit.MCG, inventory_item_id=1)])
    inventory = {1: inv(vial_size_mg=0.875, vial_size_unit=DoseUnit.MG)}
    [total] = compute_course_totals(p, inventory)
    assert total.vials_estimate == 4


def test_iu_item_shows_total_but_skips_vial_math():
    p = proto(items=[peptide_item(dose=2.0, unit=DoseUnit.IU, inventory_item_id=1)])
    inventory = {1: inv()}
    [total] = compute_course_totals(p, inventory)
    assert total.unit == "IU" and total.total_amount == 28.0  # 14 days * 2 IU
    assert total.vials_estimate is None and "IU" in total.note


def test_as_needed_item_with_no_inventory_link_defaults_to_one_vial():
    p = proto(items=[peptide_item(freq=Frequency.AS_NEEDED, inventory_item_id=None)])
    [total] = compute_course_totals(p, {})
    assert total.as_needed and total.total_amount is None
    assert total.vials_estimate == 1 and "1 vial" in total.note


def test_as_needed_item_linked_to_a_kit_item_estimates_ten_vials():
    p = proto(items=[peptide_item(freq=Frequency.AS_NEEDED, inventory_item_id=1)])
    inventory = {1: inv(purchasing_unit=PurchasingUnit.KIT_OF_10)}
    [total] = compute_course_totals(p, inventory)
    assert total.vials_estimate == 10 and "kit" in total.note.lower()


def test_titration_and_cycle_off_both_apply_across_the_course():
    # Weeks 1-2 on (step 1: 100 mcg/day), week 3 off entirely, week 4 on at step 2 (200 mcg/day).
    # 14-day course starting Jan 1 2026 covers exactly weeks 1-2 (7 days each); extend to 28 days
    # to reach week 4.
    steps = [NS(start_week=1, end_week=2, dose=100.0), NS(start_week=4, end_week=None, dose=200.0)]
    offs = [NS(start_week=3, end_week=3)]
    p = proto(end=date(2026, 1, 28), titration=True,
              items=[peptide_item(dose=None, unit=DoseUnit.MCG, steps=steps, cycle_offs=offs)])
    [total] = compute_course_totals(p, {})
    # Weeks 1-2: 14 days * 100 = 1400. Week 3: 0 (cycled off). Week 4: 7 days * 200 = 1400.
    assert total.total_amount == 2800.0


def test_far_future_end_date_does_not_crash():
    # A protocol can technically save an end_date right at the limit of what `date` can represent;
    # the day-by-day walk must not raise OverflowError stepping past date.max.
    p = proto(start=date(2026, 1, 1), end=date.max,
              items=[peptide_item(dose=250.0, unit=DoseUnit.MCG, inventory_item_id=1)])
    inventory = {1: inv()}
    [total] = compute_course_totals(p, inventory)
    assert total.vials_estimate is None
    assert "too long" in total.note.lower()


def test_non_lyophilized_medium_skips_vial_and_bac_math():
    # A Pill's vial_size_mg means "amount per pill" (see app/inventory/rules.py) -- running the
    # same mg/vial-size division on it produces a meaningless "vial" count and makes up BAC water
    # for an item that's never reconstituted.
    p = proto(items=[peptide_item(dose=250.0, unit=DoseUnit.MCG, inventory_item_id=1)])
    inventory = {1: inv(vial_size_mg=5.0, vial_size_unit=DoseUnit.MG, medium=Medium.PILL)}
    [total] = compute_course_totals(p, inventory)
    assert total.vials_estimate is None and total.bac_water_ml is None
    assert "lyophilized" in total.note.lower()


def test_falls_back_to_normally_supplied_vial_size_when_no_inventory():
    # 14 days * 250 mcg = 3500 mcg total = 3.5 mg; normally supplied at 5mg per vial = 1 vial.
    # Peptide has no inventory link, but library card says "normally supplied at 5mg".
    item = peptide_item(dose=250.0, unit=DoseUnit.MCG, inventory_item_id=None)
    item.peptide.id = 1  # Assign an id to the peptide
    p = proto(items=[item])
    peptide_with_spec = NS(normally_supplied_amount=5.0, normally_supplied_unit=DoseUnit.MG)
    normally_supplied = {item.peptide.id: peptide_with_spec}
    [total] = compute_course_totals(p, {}, normally_supplied)
    assert total.total_amount == 3500.0  # in mcg (the item's dose_unit)
    assert total.vials_estimate == 1
    assert total.bac_water_ml == 1.5
