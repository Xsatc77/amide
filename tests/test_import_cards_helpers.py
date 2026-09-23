"""Pure text helpers of tools/import_cards.py (no PDF library needed)."""

import importlib.util
from pathlib import Path

_spec = importlib.util.spec_from_file_location(
    "import_cards", Path(__file__).resolve().parent.parent / "tools" / "import_cards.py")
ic = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(ic)


def W(x, y, text, bold=False):
    """A text run: (x0, y0, x1, y1, text, bold)."""
    return (x, y - 4, x + 5 * len(text), y + 4, text, bold)


def test_group_lines_orders_by_y_then_x():
    runs = [W(100, 50, "world"), W(10, 51, "hello"), W(10, 70, "next")]
    lines = ic.group_lines(runs)
    assert [[r[4] for r in line] for line in lines] == [["hello", "world"], ["next"]]


def test_join_rejoins_bold_fragments():
    runs = [W(336, 382, "Modulates"), W(375, 382, "angiogenesis and fibroblast migration", True), W(514, 382, ".")]
    assert ic.join_lines(ic.group_lines(runs)) == "Modulates angiogenesis and fibroblast migration."
    runs = [W(336, 410, "Human data are limited", True), W(420, 410, "; the mechanism is not fully clarified.")]
    assert ic.join_lines(ic.group_lines(runs)) == "Human data are limited; the mechanism is not fully clarified."


def test_is_spaced_heading():
    assert ic.is_spaced_heading("K E Y A P P L I C A T I O N S")
    assert ic.is_spaced_heading("H A L F - L I F E")
    assert not ic.is_spaced_heading("Not established in humans")
    assert not ic.is_spaced_heading("GI symptoms")


def test_split_bullets():
    lines = ["Cytoprotective action described in experimental models.",
             "Modulates angiogenesis and fibroblast",
             "migration.",
             "Human data are limited."]
    assert ic.split_bullets(lines) == ["Cytoprotective action described in experimental models.",
                                       "Modulates angiogenesis and fibroblast migration.",
                                       "Human data are limited."]


def test_split_references_joins_wrapped_lines():
    lines = ["Teichman et al., 2006 — J Clin Endocrinol Metab", "91(3):799–805", "Ionescu & Frohman, 2006 — J Clin Endocrinol Metab"]
    assert ic.split_references(lines) == ["Teichman et al., 2006 — J Clin Endocrinol Metab 91(3):799–805",
                                          "Ionescu & Frohman, 2006 — J Clin Endocrinol Metab"]


def test_short_values_are_not_headings():
    assert not ic.is_spaced_heading("< 1 min")
    assert not ic.is_spaced_heading("~ 6 h")
    assert ic.is_spaced_heading("K E Y R E F E R E N C E S".replace("Y R", "YR"))


def test_strip_spaced_headings_removes_heading_letters_only():
    heading = [W(321 + 9 * i, 455, ch) for i, ch in enumerate("QUICKP")]
    body = [W(190, 456, "Rise in"), W(230, 456, "transaminases", True)]
    kept = ic.strip_spaced_headings(heading + body)
    assert sorted(r[4] for r in kept) == ["Rise in", "transaminases"]


def test_dash_broken_words_rejoin():
    lines = ic.group_lines([W(40, 330, "Renin–"), W(40, 340, "angiotensin axis")])
    assert ic.join_lines(lines) == "Renin–angiotensin axis"


def test_split_bullets_allows_stray_character_after_sentence():
    lines = ["Fusion protein that traps ligands of the TGF- superfamily. β", "Promotes late maturation."]
    assert ic.split_bullets(lines) == ["Fusion protein that traps ligands of the TGF- superfamily. β",
                                       "Promotes late maturation."]
