from app.db import SessionLocal
from app.vendors.resolve import resolve_contact_method_type, resolve_payment_method_type


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
