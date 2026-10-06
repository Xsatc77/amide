import asyncio

import httpx

from app.main import app
from app.models import IngestItem, IngestSource, Vendor
from ingest_helpers import make_source, make_token, pdf_bytes, png_bytes
from watcher.amide_client import AmideAuthError, AmideClient, AmideUnavailable
from watcher.ports import Payload


def http():
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://amide.test")


def run(coro):
    return asyncio.run(coro)


def payload(**kw):
    return Payload(**{**dict(chat_id="-100123", message_id="10", album_id=None, date="2026-10-06T14:30:00+00:00", text="New prices",
                             files=[("list.pdf", pdf_bytes())]), **kw})


def test_register_list_send_and_state_against_the_real_api(client, db, me):
    secret = make_token(db, me)

    async def go():
        async with http() as h:
            amide = AmideClient(h, secret)
            await amide.register("-100123", "Acme group")
            assert await amide.list_sources() == []                         # registered but not mapped or enabled yet
            source = db.query(IngestSource).one()
            vendor = Vendor(name="Acme Labs")
            db.add(vendor)
            db.commit()
            source.vendor_id, source.enabled = vendor.id, True
            db.commit()
            assert await amide.list_sources() == ["-100123"]
            first = await amide.send(payload())
            again = await amide.send(payload())
            assert (first.kind, again.kind) == ("delivered", "delivered")   # a resend is harmless (Amide answers duplicate)
            assert await amide.report_state("-100123", "gone", "removed from the group") is True
    run(go())
    db.expire_all()
    assert db.query(IngestItem).count() == 1 and db.query(IngestSource).one().state == "gone"


def test_an_album_goes_as_one_message_with_its_album_id(client, db, me):
    secret = make_token(db, me)
    source = make_source(db)

    async def go():
        async with http() as h:
            return await AmideClient(h, secret).send(payload(album_id="777", text="", files=[("1.png", png_bytes(1)), ("2.png", png_bytes(2))]))
    assert run(go()).kind == "delivered"
    assert {i.group_key for i in db.query(IngestItem)} == {f"{source.id}:a:777"}


def test_a_bad_token_is_an_auth_error_and_a_disabled_group_is_dropped_not_retried(client, db, me):
    secret = make_token(db, me)
    make_source(db, enabled=False)

    async def go():
        async with http() as h:
            bad = AmideClient(h, "amide_ing_wrong")
            try:
                await bad.list_sources()
                raise AssertionError("should have raised")
            except AmideAuthError:
                pass
            assert (await bad.send(payload())).kind == "auth"
            assert (await AmideClient(h, secret).send(payload())).kind == "drop"            # 409: the group is disabled
    run(go())


def test_network_errors_and_server_errors_mean_retry_later():
    def handler(request):
        if request.url.path.endswith("/messages"):
            return httpx.Response(503)
        if request.url.path.endswith("/sources"):
            return httpx.Response(429)
        raise httpx.ConnectError("down")

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://amide.test") as h:
            amide = AmideClient(h, "t")
            assert (await amide.send(payload())).kind == "retry"
            for call in (amide.list_sources(), amide.register("-1", "x")):
                try:
                    await call
                    raise AssertionError("should have raised")
                except AmideUnavailable:
                    pass
            assert await amide.report_state("-1", "gone") is False
    run(go())
    assert run(_unreachable()).kind == "retry"


async def _unreachable():
    def refuse(request):
        raise httpx.ConnectError("refused")
    async with httpx.AsyncClient(transport=httpx.MockTransport(refuse), base_url="http://amide.test") as h:
        return await AmideClient(h, "t").send(payload())
