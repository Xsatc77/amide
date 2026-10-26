"""Passkeys (a software authenticator stands in for the phone) and "Remember this device"."""

import hashlib
import itertools
import os
import json
import struct
from datetime import timedelta

import cbor2
import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec

from app import config
from app.auth import devices, passkeys
from app.db import SessionLocal
from app.models import Passkey, TrustedDevice

from tests.test_auth_flow import PW, accept, clock, fresh, next_code, register, setup_2fa, text, user  # noqa: F401

ORIGIN, RP_ID = "http://testserver", "testserver"


class Phone:
    """A software authenticator: makes a key pair, signs the way a phone's secure hardware would after Face ID."""

    def __init__(self):
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.credential_id = os.urandom(32)
        self.count = 0

    def _client_data(self, kind: str, challenge: str) -> bytes:
        return json.dumps({"type": kind, "challenge": challenge, "origin": ORIGIN, "crossOrigin": False}).encode()

    def register(self, options: dict) -> dict:
        numbers = self.key.public_key().public_numbers()
        cose = cbor2.dumps({1: 2, 3: -7, -1: 1, -2: numbers.x.to_bytes(32, "big"), -3: numbers.y.to_bytes(32, "big")})
        auth_data = (hashlib.sha256(RP_ID.encode()).digest() + bytes([0x45]) + struct.pack(">I", 0) + bytes(16)
                     + struct.pack(">H", len(self.credential_id)) + self.credential_id + cose)
        attestation = cbor2.dumps({"fmt": "none", "attStmt": {}, "authData": auth_data})
        client = self._client_data("webauthn.create", options["challenge"])
        return {"id": passkeys.b64(self.credential_id), "rawId": passkeys.b64(self.credential_id), "type": "public-key",
                "response": {"attestationObject": passkeys.b64(attestation), "clientDataJSON": passkeys.b64(client)}}

    def sign_in(self, options: dict, *, tamper: bool = False) -> dict:
        self.count += 1
        auth_data = hashlib.sha256(RP_ID.encode()).digest() + bytes([0x05]) + struct.pack(">I", self.count)
        client = self._client_data("webauthn.get", options["challenge"])
        signature = self.key.sign(auth_data + hashlib.sha256(client).digest(), ec.ECDSA(hashes.SHA256()))
        if tamper:
            signature = signature[:-1] + bytes([signature[-1] ^ 1])
        return {"id": passkeys.b64(self.credential_id), "rawId": passkeys.b64(self.credential_id), "type": "public-key",
                "response": {"authenticatorData": passkeys.b64(auth_data), "clientDataJSON": passkeys.b64(client),
                             "signature": passkeys.b64(signature), "userHandle": None}}


def add_passkey(c, phone: Phone, name="My iPhone"):
    options = c.post("/account/passkeys/options").json()
    return c.post("/account/passkeys", json={"name": name, "credential": phone.register(options)})


def passkey_login(c, phone: Phone, **kw):
    options = c.post("/login/passkey/options").json()
    return c.post("/login/passkey", json={"credential": phone.sign_in(options, **kw)})


def signed_in(c) -> bool:
    return c.get("/protocols").status_code == 200


# ---------------------------------------------------------------- passkeys

def test_adding_a_passkey_stores_only_the_public_key_and_lists_it(fresh):
    accept(fresh)
    register(fresh, "Keyed1")
    r = add_passkey(fresh, Phone())
    assert r.status_code == 200 and r.json()["next"] == "/account/passkeys"
    with SessionLocal() as s:
        key = s.query(Passkey).one()
        assert key.name == "My iPhone" and key.user_id == user("keyed1").id and key.public_key
    assert "My iPhone" in text(fresh.get("/account/passkeys"))


def test_a_passkey_signs_you_in_from_a_fresh_browser_without_a_password_or_code(fresh):
    accept(fresh)
    register(fresh, "Keyed2")
    secret = setup_2fa(fresh)
    phone = Phone()
    add_passkey(fresh, phone)
    fresh.post("/logout")
    assert not signed_in(fresh)

    r = passkey_login(fresh, phone)
    assert r.status_code == 200 and r.json()["next"] == "/dashboard"
    assert signed_in(fresh)                                   # 2FA is on for this account, and no code was asked
    with SessionLocal() as s:
        assert s.query(Passkey).filter(Passkey.user_id == user("keyed2").id).one().last_used_at is not None


def test_a_bad_signature_is_refused(fresh):
    accept(fresh)
    register(fresh, "Keyed3")
    phone = Phone()
    add_passkey(fresh, phone)
    fresh.post("/logout")
    r = passkey_login(fresh, phone, tamper=True)
    assert r.status_code == 401 and not signed_in(fresh)


def test_a_challenge_can_only_be_answered_once(fresh):
    accept(fresh)
    register(fresh, "Keyed4")
    phone = Phone()
    add_passkey(fresh, phone)
    fresh.post("/logout")
    options = fresh.post("/login/passkey/options").json()
    answer = phone.sign_in(options)
    assert fresh.post("/login/passkey", json={"credential": answer}).status_code == 200
    fresh.post("/logout")
    assert fresh.post("/login/passkey", json={"credential": answer}).status_code == 401


def test_a_passkey_the_server_does_not_know_is_refused(fresh):
    accept(fresh)
    register(fresh, "Keyed5")
    fresh.post("/logout")
    assert passkey_login(fresh, Phone()).status_code == 401 and not signed_in(fresh)


def test_the_passkey_endpoints_need_the_legal_notice_first(fresh):
    assert fresh.post("/login/passkey/options").status_code == 303


def test_a_passkey_is_removed_only_by_its_owner(fresh):
    accept(fresh)
    register(fresh, "Owner7")
    add_passkey(fresh, Phone())
    with SessionLocal() as s:
        key_id = s.query(Passkey).filter(Passkey.user_id == user("owner7").id).one().id
    other = type(fresh)(fresh.app, follow_redirects=False)
    accept(other)
    register(other, "Other7")
    other.post(f"/account/passkeys/{key_id}/delete")
    with SessionLocal() as s:
        assert s.get(Passkey, key_id) is not None
    fresh.post(f"/account/passkeys/{key_id}/delete")
    with SessionLocal() as s:
        assert s.get(Passkey, key_id) is None


# ---------------------------------------------------------------- remember this device

_made = itertools.count(1)


def two_factor_user(fresh):
    """A new account with 2FA on (a fresh name each time: the accounts outlive a test); returns (lower-case name, secret)."""
    name = f"Trusty{next(_made)}"
    accept(fresh)
    register(fresh, name)
    return name.lower(), setup_2fa(fresh)


def log_in_with_code(c, name, secret, *, remember):
    c.post("/logout")
    assert c.post("/login", data={"username": name, "password": PW}).headers["location"] == "/login/2fa"
    data = {"code": next_code(secret)}
    if remember:
        data["remember"] = "1"
    return c.post("/login/2fa", data=data)


def test_ticking_remember_skips_the_code_next_time(fresh):
    name, secret = two_factor_user(fresh)
    r = log_in_with_code(fresh, name, secret, remember=True)
    assert devices.COOKIE in r.headers["set-cookie"] and "Max-Age=1209600" in r.headers["set-cookie"]
    fresh.post("/logout")
    r = fresh.post("/login", data={"username": name, "password": PW})
    assert r.headers["location"] == "/dashboard" and signed_in(fresh)


def test_without_the_tick_the_code_is_asked_every_time(fresh):
    name, secret = two_factor_user(fresh)
    r = log_in_with_code(fresh, name, secret, remember=False)
    assert devices.COOKIE not in r.headers.get("set-cookie", "")
    fresh.post("/logout")
    assert fresh.post("/login", data={"username": name, "password": PW}).headers["location"] == "/login/2fa"


def test_a_remembered_device_expires_after_the_trusted_days(fresh, clock):
    name, secret = two_factor_user(fresh)
    log_in_with_code(fresh, name, secret, remember=True)
    clock["now"] += timedelta(days=config.TRUSTED_DEVICE_DAYS - 1)
    accept(fresh)                                                 # a day later the idle session is long gone: the notice comes first
    assert fresh.post("/login", data={"username": name, "password": PW}).headers["location"] == "/dashboard"
    clock["now"] += timedelta(days=2)
    fresh.post("/logout")
    accept(fresh)
    assert fresh.post("/login", data={"username": name, "password": PW}).headers["location"] == "/login/2fa"


def test_a_remembered_device_still_needs_the_password(fresh):
    name, secret = two_factor_user(fresh)
    log_in_with_code(fresh, name, secret, remember=True)
    fresh.post("/logout")
    r = fresh.post("/login", data={"username": name, "password": "wrong-Pass1!"})
    assert r.status_code == 422 and not signed_in(fresh)


def test_one_persons_remembered_device_does_not_skip_anothers_code(fresh):
    name, secret = two_factor_user(fresh)
    log_in_with_code(fresh, name, secret, remember=True)
    cookie = fresh.cookies.get(devices.COOKIE)
    fresh.post("/logout")
    other = type(fresh)(fresh.app, follow_redirects=False)
    accept(other)
    register(other, "SecondTrusty")
    setup_2fa(other)
    other.post("/logout")
    other.cookies.set(devices.COOKIE, cookie)
    assert other.post("/login", data={"username": "secondtrusty", "password": PW}).headers["location"] == "/login/2fa"


def test_turning_off_2fa_or_the_forget_button_forgets_remembered_devices(fresh):
    name, secret = two_factor_user(fresh)
    log_in_with_code(fresh, name, secret, remember=True)
    uid = user(name).id
    with SessionLocal() as s:
        assert s.query(TrustedDevice).filter(TrustedDevice.user_id == uid).count() == 1
    fresh.post("/account/2fa/forget-devices")
    with SessionLocal() as s:
        assert s.query(TrustedDevice).filter(TrustedDevice.user_id == uid).count() == 0
    fresh.post("/logout")
    assert fresh.post("/login", data={"username": name, "password": PW}).headers["location"] == "/login/2fa"


def test_the_idle_limit_is_thirty_minutes_and_a_device_is_remembered_for_fourteen_days():
    assert config.SESSION_IDLE_MINUTES == 30 and config.TRUSTED_DEVICE_DAYS == 14


def test_the_same_passkey_cannot_be_added_twice(fresh):
    accept(fresh)
    register(fresh, "Dupe1")
    phone = Phone()
    assert add_passkey(fresh, phone).status_code == 200
    assert add_passkey(fresh, phone).status_code == 400
    with SessionLocal() as s:
        assert s.query(Passkey).filter(Passkey.credential_id == passkeys.b64(phone.credential_id)).count() == 1
