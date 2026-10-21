from app.ingest import tokens
from app.models import IngestToken
from app.routers import apple_health
from photo_helpers import other_client


def _session_id(c):
    return c.cookies.get("amide_session")


def test_health_token_works_for_a_non_admin_and_is_shown_once(client, db):
    with other_client("healthmember") as member:
        r = member.post("/settings/apple-health/token", data={"label": "iPhone"})
        assert r.status_code == 303 and r.headers["location"] == "/settings#integrations"
        token = db.query(IngestToken).one()
        assert token.scope == "health" and token.label == "iPhone"
        shown = apple_health.take_new_token(_session_id(member))
        assert shown is not None and shown[0].startswith(tokens.HEALTH_PREFIX) and shown[1] == "iPhone"
        assert apple_health.take_new_token(_session_id(member)) is None       # gone after the first look
        assert tokens.find_token(db, f"Bearer {shown[0]}", scope="health").id == token.id
        assert shown[0] not in (token.token_hash, token.prefix) and token.token_hash == tokens.hash_secret(shown[0])


def test_watcher_token_is_refused_on_health_scope(db, me):
    _, secret = tokens.create_token(db, me, "w")
    assert tokens.find_token(db, f"Bearer {secret}") is not None
    assert tokens.find_token(db, f"Bearer {secret}", scope="health") is None


def test_health_token_is_refused_on_watcher_scope(db, me):
    _, secret = tokens.create_token(db, me, "h", scope="health")
    assert secret.startswith(tokens.HEALTH_PREFIX)
    assert tokens.find_token(db, f"Bearer {secret}", scope="health") is not None
    assert tokens.find_token(db, f"Bearer {secret}") is None


def test_revoked_token_is_refused(db, me):
    token, secret = tokens.create_token(db, me, "h", scope="health")
    assert tokens.revoke_token(db, token.id) is True
    assert tokens.find_token(db, f"Bearer {secret}", scope="health") is None


def test_one_person_cannot_revoke_anothers_token(client, db, me):
    token, secret = tokens.create_token(db, me, "h", scope="health")
    with other_client("healthintruder") as member:
        assert member.post(f"/settings/apple-health/token/{token.id}/revoke", data={}).status_code in (303, 404)
    db.expire_all()
    assert db.get(IngestToken, token.id).revoked_at is None
    assert tokens.find_token(db, f"Bearer {secret}", scope="health") is not None
    assert client.post(f"/settings/apple-health/token/{token.id}/revoke", data={}).status_code == 200      # the owner's own redirect lands on /settings
    db.expire_all()
    assert db.get(IngestToken, token.id).revoked_at is not None
