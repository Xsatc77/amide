"""Library extras: reordering a goal's suggested stack, and each person's private notes and saved articles per peptide."""

import html

import pytest
from sqlalchemy import select

from app.db import SessionLocal
from app.models import GoalPeptide, Peptide, PeptideNote, PeptideSource
from photo_helpers import other_client


@pytest.fixture
def stack():
    """Three library cards in the 'wellness' stack, in the order Alpha, Beta, Gamma (after whatever else was there)."""
    with SessionLocal() as s:
        cards = [Peptide(name=f"Stack {n} Test", source=PeptideSource.CUSTOM) for n in ("Alpha", "Beta", "Gamma")]
        s.add_all(cards)
        s.flush()
        s.query(GoalPeptide).filter(GoalPeptide.goal == "wellness").delete()
        for n, c in enumerate(cards):
            s.add(GoalPeptide(goal="wellness", peptide_id=c.id, position=n))
        s.commit()
        ids = [c.id for c in cards]
    yield ids
    with SessionLocal() as s:
        s.query(GoalPeptide).filter(GoalPeptide.goal == "wellness").delete()
        s.query(Peptide).filter(Peptide.id.in_(ids)).delete()
        s.commit()


def order(goal="wellness"):
    with SessionLocal() as s:
        return [s.get(Peptide, gp.peptide_id).name for gp in s.scalars(select(GoalPeptide).where(GoalPeptide.goal == goal).order_by(GoalPeptide.position))]


def test_the_stacks_page_lists_each_goals_peptides_in_order(client, db, stack):
    page = html.unescape(client.get("/library/stacks").text)
    assert "Wellness / General Health" in page
    section = page.split(chr(105) + chr(100) + "=\"stack-wellness\"")[1].split("</section>")[0]
    assert section.index("Stack Alpha Test") < section.index("Stack Beta Test") < section.index("Stack Gamma Test")


def test_moving_a_peptide_down_and_up_swaps_it_with_its_neighbour(client, db, stack):
    r = client.post("/library/stacks/wellness/move", data={"peptide_id": str(stack[0]), "direction": "down"}, follow_redirects=False)
    assert r.status_code == 303 and order() == ["Stack Beta Test", "Stack Alpha Test", "Stack Gamma Test"]
    client.post("/library/stacks/wellness/move", data={"peptide_id": str(stack[2]), "direction": "up"})
    assert order() == ["Stack Beta Test", "Stack Gamma Test", "Stack Alpha Test"]


def test_moving_past_either_end_changes_nothing(client, db, stack):
    client.post("/library/stacks/wellness/move", data={"peptide_id": str(stack[0]), "direction": "up"})
    client.post("/library/stacks/wellness/move", data={"peptide_id": str(stack[2]), "direction": "down"})
    assert order() == ["Stack Alpha Test", "Stack Beta Test", "Stack Gamma Test"]


def test_a_bad_goal_direction_or_peptide_is_refused(client, db, stack):
    assert client.post("/library/stacks/nonsense/move", data={"peptide_id": str(stack[0]), "direction": "up"}).status_code == 404
    assert client.post("/library/stacks/wellness/move", data={"peptide_id": str(stack[0]), "direction": "sideways"}).status_code == 422
    assert client.post("/library/stacks/wellness/move", data={"peptide_id": "99999999", "direction": "up"}).status_code == 404


# ---------------------------------------------------------------- learning notes

@pytest.fixture
def card():
    with SessionLocal() as s:
        c = Peptide(name="Learning Test Card", source=PeptideSource.CUSTOM)
        s.add(c)
        s.commit()
        cid = c.id
    yield cid
    with SessionLocal() as s:
        s.query(PeptideNote).filter_by(peptide_id=cid).delete()
        s.query(Peptide).filter_by(id=cid).delete()
        s.commit()


def test_a_note_and_a_saved_article_appear_on_the_card(client, db, card):
    r = client.post(f"/library/{card}/notes", data={"title": "My trial week", "body": "Felt calmer by day 4"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == f"/library/{card}#learning"
    client.post(f"/library/{card}/notes", data={"title": "A study", "url": "https://example.org/paper", "body": ""})
    page = html.unescape(client.get(f"/library/{card}").text)
    assert "My trial week" in page and "Felt calmer by day 4" in page and 'href="https://example.org/paper"' in page and 'rel="noopener' in page


def test_a_note_needs_a_title_and_a_web_link_must_be_http(client, db, card):
    assert client.post(f"/library/{card}/notes", data={"title": "  ", "body": "x"}).status_code == 422
    assert client.post(f"/library/{card}/notes", data={"title": "Bad", "url": "javascript:alert(1)"}).status_code == 422
    assert client.post(f"/library/{card}/notes", data={"title": "x" * 300}).status_code == 422
    with SessionLocal() as s:
        assert s.query(PeptideNote).filter_by(peptide_id=card).count() == 0


def test_notes_are_private_to_their_owner(client, db, card):
    client.post(f"/library/{card}/notes", data={"title": "Mine only", "body": "secret"})
    with other_client("learnother") as member:
        assert "Mine only" not in member.get(f"/library/{card}").text
        with SessionLocal() as s:
            note_id = s.scalar(select(PeptideNote.id).where(PeptideNote.peptide_id == card))
        assert member.post(f"/library/notes/{note_id}/delete").status_code == 404
    with SessionLocal() as s:
        assert s.get(PeptideNote, note_id) is not None


def test_a_note_can_be_deleted_by_its_owner(client, db, card):
    client.post(f"/library/{card}/notes", data={"title": "Temp note"})
    with SessionLocal() as s:
        note_id = s.scalar(select(PeptideNote.id).where(PeptideNote.peptide_id == card))
    assert client.post(f"/library/notes/{note_id}/delete", follow_redirects=False).status_code == 303
    assert "Temp note" not in client.get(f"/library/{card}").text


def test_the_learning_page_lists_all_my_notes_with_a_search(client, db, card):
    client.post(f"/library/{card}/notes", data={"title": "Findable title", "body": "needle in text"})
    page = html.unescape(client.get("/library/learning").text)
    assert "Findable title" in page and "Learning Test Card" in page
    assert "needle" in html.unescape(client.get("/library/learning", params={"q": "needle"}).text)
    assert "Findable title" not in client.get("/library/learning", params={"q": "zzzzz"}).text
