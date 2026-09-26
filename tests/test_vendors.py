from app.db import SessionLocal
from app.vendors.resolve import resolve_contact_method_type, resolve_payment_method_type
from app.vendors.links import contact_link


def test_resolve_contact_method_type_reuses_existing_case_insensitively(db):
    with SessionLocal() as s:
        a = resolve_contact_method_type(s, "Email")
        b = resolve_contact_method_type(s, "email")
        assert a.id == b.id


def test_resolve_contact_method_type_creates_new(db):
    with SessionLocal() as s:
        created = resolve_contact_method_type(s, "Signal")
        assert created.name == "Signal"
        again = resolve_contact_method_type(s, "signal")
        assert again.id == created.id


def test_resolve_contact_method_type_blank_is_none(db):
    with SessionLocal() as s:
        assert resolve_contact_method_type(s, "  ") is None


def test_resolve_payment_method_type_reuses_existing(db):
    with SessionLocal() as s:
        a = resolve_payment_method_type(s, "Crypto")
        b = resolve_payment_method_type(s, "CRYPTO")
        assert a.id == b.id


def test_resolve_payment_method_type_creates_new(db):
    with SessionLocal() as s:
        created = resolve_payment_method_type(s, "Zelle")
        assert created.name == "Zelle"
        again = resolve_payment_method_type(s, "zelle")
        assert again.id == created.id


def test_resolve_payment_method_type_blank_is_none(db):
    with SessionLocal() as s:
        assert resolve_payment_method_type(s, "   ") is None


def test_email_link():
    assert contact_link("Email", "vendor@example.com") == "mailto:vendor@example.com"


def test_phone_link():
    assert contact_link("Phone", "+1 (555) 123-4567") == "tel:+1 (555) 123-4567"


def test_whatsapp_link_strips_non_digits():
    assert contact_link("WhatsApp", "+1 (555) 123-4567") == "https://wa.me/15551234567"


def test_telegram_link_strips_leading_at():
    assert contact_link("Telegram", "@somehandle") == "https://t.me/somehandle"


def test_telegram_link_without_at():
    assert contact_link("Telegram", "somehandle") == "https://t.me/somehandle"


def test_custom_method_type_has_no_link():
    assert contact_link("Signal", "+15551234567") is None


def test_matching_is_case_insensitive_on_method_name():
    assert contact_link("email", "vendor@example.com") == "mailto:vendor@example.com"
    assert contact_link("EMAIL", "vendor@example.com") == "mailto:vendor@example.com"
