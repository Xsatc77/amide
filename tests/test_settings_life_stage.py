import pytest

from app.models import User


@pytest.fixture
def reset_profile(client):
    yield
    client.post("/settings/body-profile", data={})


def saved(db, me):
    db.expire_all()
    return db.get(User, me).life_stage


def test_life_stage_saves_and_clears(client, db, me, reset_profile):
    assert client.post("/settings/body-profile", data={"sex": "Female", "life_stage": "luteal"},
                       follow_redirects=False).status_code == 303
    assert saved(db, me) == "luteal"
    client.post("/settings/body-profile", data={"sex": "Female"})
    assert saved(db, me) is None


def test_an_unknown_life_stage_is_refused(client, db, me, reset_profile):
    r = client.post("/settings/body-profile", data={"life_stage": "nonsense"}, follow_redirects=False)
    assert r.status_code == 422 and saved(db, me) is None


def test_the_settings_page_lists_every_stage_with_its_label(client, reset_profile):
    page = client.get("/settings").text
    for key in ("luteal", "pregnancy_1", "pregnancy_2", "pregnancy_3", "breastfeeding", "perimenopause", "pcos"):
        assert f'value="{key}"' in page
    assert "Perimenopause (-175)" in page and "name=\"life_stage\"" in page
