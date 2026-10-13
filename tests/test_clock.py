"""'Today' follows the signed-in person's time zone, not the server's (a server in UTC is already Tuesday at 8 PM Monday in Central time)."""

from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select

from app import clock
from app.db import SessionLocal
from app.models import User, WaterLog


def test_without_a_zone_today_is_the_servers_date_and_with_one_it_is_that_zones_date():
    clock.set_zone(None)
    assert clock.today() == date.today()
    clock.set_zone("Pacific/Kiritimati")
    try:
        assert clock.today() == datetime.now(ZoneInfo("Pacific/Kiritimati")).date()
    finally:
        clock.set_zone(None)


def test_a_bad_or_blank_zone_falls_back_to_the_servers_date():
    for bad in ("", "Not/AZone", None):
        clock.set_zone(bad)
        assert clock.today() == date.today()


def tester_id():
    with SessionLocal() as s:
        return s.scalar(select(User.id).where(User.username_key == "tester"))


def set_user_zone(zone):
    with SessionLocal() as s:
        s.get(User, tester_id()).timezone = zone
        s.commit()


@pytest.fixture(autouse=True)
def restore_zone():
    yield
    set_user_zone(None)
    with SessionLocal() as s:
        s.query(WaterLog).delete()
        s.commit()


@pytest.mark.parametrize("zone", ["Pacific/Kiritimati", "Pacific/Pago_Pago"])
def test_a_page_that_logs_today_uses_the_signed_in_persons_zone(client, zone):
    set_user_zone(zone)
    client.post("/dashboard/water/log", data={"ounces": "8"})
    with SessionLocal() as s:
        logged = s.scalar(select(WaterLog.logged_at))
    assert logged == datetime.now(ZoneInfo(zone)).date()


def test_the_two_zones_really_are_different_days():
    assert datetime.now(ZoneInfo("Pacific/Kiritimati")).date() != datetime.now(ZoneInfo("Pacific/Pago_Pago")).date()


def log_water_day(client):
    client.post("/dashboard/water/log", data={"ounces": "8"})
    with SessionLocal() as s:
        return s.scalar(select(WaterLog.logged_at))


def test_with_no_zone_chosen_the_devices_zone_cookie_is_used_and_a_chosen_zone_wins(client):
    set_user_zone(None)
    client.cookies.set("amide_tz", "Pacific/Kiritimati")
    try:
        assert log_water_day(client) == datetime.now(ZoneInfo("Pacific/Kiritimati")).date()
        with SessionLocal() as s:
            s.query(WaterLog).delete()
            s.commit()
        set_user_zone("Pacific/Pago_Pago")                      # a zone chosen in Settings beats whatever the device says
        assert log_water_day(client) == datetime.now(ZoneInfo("Pacific/Pago_Pago")).date()
    finally:
        client.cookies.delete("amide_tz")


def test_a_junk_zone_cookie_is_ignored(client):
    set_user_zone(None)
    client.cookies.set("amide_tz", "Not/AZone")
    try:
        assert log_water_day(client) == date.today()
    finally:
        client.cookies.delete("amide_tz")


def test_every_page_tells_the_browser_to_report_its_zone_and_settings_says_what_system_time_means(client):
    assert "amide_tz" in client.get("/dashboard").text
    assert "this device" in client.get("/settings").text.lower()
