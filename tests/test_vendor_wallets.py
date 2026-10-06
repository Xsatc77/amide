import html

from app import config
from app.db import SessionLocal
from app.models import Vendor, VendorWallet
from price_helpers import make_vendor

PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 32
ADDRESS = "bc1qexampleexampleexampleexample0000"


def save(client, vendor, rows, status=303):
    """rows: list of dicts of wallet fields (coin, address, network, id, remove_qr) plus optional qr=(name, bytes)"""
    data, files = {"name": vendor.name}, {}
    for i, row in enumerate(rows):
        for key, value in row.items():
            if key == "qr":
                files[f"wallets-{i}-qr"] = value
            else:
                data[f"wallets-{i}-{key}"] = value
    r = client.post(f"/vendors/{vendor.id}", data=data, files=files or {"x": ("", b"")}, follow_redirects=False)
    assert r.status_code == status, r.text[:300]
    return r


def wallets(vendor_id):
    with SessionLocal() as s:
        return [(w.id, w.coin, w.address, w.network, w.qr_filename)
                for w in s.query(VendorWallet).filter_by(vendor_id=vendor_id).order_by(VendorWallet.id)]


def page(client, vendor):
    return html.unescape(client.get(f"/vendors/{vendor.id}").text)


def test_a_wallet_is_saved_and_shown_with_a_copy_button(client, db):
    v = make_vendor(db, "Acme")
    save(client, v, [{"coin": "USDT", "address": ADDRESS, "network": "TRC20"}])
    assert [w[1:4] for w in wallets(v.id)] == [("USDT", ADDRESS, "TRC20")]
    text = page(client, v)
    assert ADDRESS in text and "TRC20" in text and f'data-copy="{ADDRESS}"' in text


def test_the_coin_list_is_btc_eth_usdc_usdt(client, db):
    v = make_vendor(db, "Acme")
    text = page(client, v)
    template = text[text.index('id="wallet-row-template"'):]
    assert [c for c in ("BTC", "ETH", "USDC", "USDT") if f'<option value="{c}">' in template] == ["BTC", "ETH", "USDC", "USDT"]


def test_an_unknown_coin_or_a_spaced_address_is_refused(client, db):
    v = make_vendor(db, "Acme")
    assert "Choose a coin" in save(client, v, [{"coin": "DOGE", "address": ADDRESS}], status=422).text
    assert "one string" in save(client, v, [{"coin": "BTC", "address": "bc1q abc"}], status=422).text
    assert wallets(v.id) == []


def test_a_qr_photo_is_stored_served_and_checked(client, db):
    v = make_vendor(db, "Acme")
    save(client, v, [{"coin": "BTC", "address": ADDRESS, "qr": ("q.png", PNG, "image/png")}])
    (wid, *_, qr), = wallets(v.id)
    assert qr and (config.WALLET_QR_DIR / qr).exists()
    r = client.get(f"/vendors/{v.id}/wallets/{wid}/qr")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png" and r.content == PNG
    assert f"/vendors/{v.id}/wallets/{wid}/qr" in page(client, v)
    bad = save(client, v, [{"id": str(wid), "coin": "BTC", "address": ADDRESS, "qr": ("q.png", b"not a png")}], status=422)
    assert "don't match" in html.unescape(bad.text)
    assert wallets(v.id)[0][4] == qr


def test_editing_keeps_the_qr_unless_replaced_or_removed(client, db):
    v = make_vendor(db, "Acme")
    save(client, v, [{"coin": "BTC", "address": ADDRESS, "qr": ("q.png", PNG, "image/png")}])
    (wid, *_, qr), = wallets(v.id)
    save(client, v, [{"id": str(wid), "coin": "BTC", "address": ADDRESS + "x", "network": "Bitcoin"}])
    assert wallets(v.id) == [(wid, "BTC", ADDRESS + "x", "Bitcoin", qr)]
    save(client, v, [{"id": str(wid), "coin": "BTC", "address": ADDRESS, "remove_qr": "1"}])
    assert wallets(v.id)[0][4] is None and not (config.WALLET_QR_DIR / qr).exists()


def test_a_wallet_left_out_is_deleted_with_its_qr_and_so_is_the_vendor(client, db):
    v = make_vendor(db, "Acme")
    save(client, v, [{"coin": "BTC", "address": ADDRESS, "qr": ("q.png", PNG, "image/png")}])
    (_, *_, qr), = wallets(v.id)
    save(client, v, [])
    assert wallets(v.id) == [] and not (config.WALLET_QR_DIR / qr).exists()
    save(client, v, [{"coin": "ETH", "address": "0x" + "a" * 40, "qr": ("q.png", PNG, "image/png")}])
    (_, *_, qr2), = wallets(v.id)
    assert client.post(f"/vendors/{v.id}/delete", follow_redirects=False).status_code == 303
    assert not (config.WALLET_QR_DIR / qr2).exists()
    with SessionLocal() as s:
        assert s.query(VendorWallet).count() == 0 and s.get(Vendor, v.id) is None


def test_a_failed_save_leaves_no_stray_qr_file(client, db):
    v = make_vendor(db, "Acme")
    before = set(config.WALLET_QR_DIR.glob("*")) if config.WALLET_QR_DIR.exists() else set()
    save(client, v, [{"coin": "BTC", "address": ADDRESS, "qr": ("a.png", PNG, "image/png")},
                     {"coin": "ETH", "address": "0x" + "a" * 40, "qr": ("b.png", b"junk")}], status=422)
    after = set(config.WALLET_QR_DIR.glob("*")) if config.WALLET_QR_DIR.exists() else set()
    assert after == before and wallets(v.id) == []
