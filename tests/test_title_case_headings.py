"""Every card heading in the templates is Title Case ("Items In Shipment"): no word of a title starts with a lowercase letter.

The tutorial chapters (templates/learn/s*.html) and the legal documents keep the sentence style of their prose, and a few units stay lowercase."""

import re
from pathlib import Path

TEMPLATES = Path(__file__).resolve().parents[1] / "app" / "templates"
PROSE = ("learn/s", "legal/")
HEADING = re.compile(r"<(h[1-4]|legend)\b[^>]*>(.*?)</\1>", re.S | re.I)
MARKUP = re.compile(r"\{\{.*?\}\}|\{%.*?%\}|\{#.*?#\}|<[^>]+>|&[#A-Za-z0-9]+;|\([^)]*\)", re.S)          # expressions, tags, entities and parentheses (units) are not title words
NOTE = re.compile(r'<(?:span|small)\b[^>]*class="[^"]*\b(?:muted|small)\b')                           # a muted note after the title is a sentence


def lowercase_words(inner: str) -> list[str]:
    cut = NOTE.search(inner)
    text = MARKUP.sub(" ", inner[:cut.start()] if cut else inner)
    return [w for w in text.split() if re.match(r"[a-z]", re.sub(r"^[^A-Za-z]+", "", w) or "A") and not re.match(r"^[a-z]+://", w)]


def test_every_card_heading_is_title_case():
    bad = []
    for path in sorted(TEMPLATES.rglob("*.html")):
        rel = path.relative_to(TEMPLATES).as_posix()
        if rel.startswith(PROSE):
            continue
        for m in HEADING.finditer(path.read_text(encoding="utf-8")):
            words = lowercase_words(m.group(2))
            if words:
                shown = re.sub(r"\s+", " ", m.group(2).strip())[:80]
                bad.append(f"{rel}: {shown}  ({', '.join(words)})")
    assert not bad, "headings with a lowercase first letter:\n" + "\n".join(bad)


def test_the_checker_sees_a_lowercase_word():
    assert lowercase_words("Your info") == ["info"] and lowercase_words("Your Info") == []
    assert lowercase_words("Weight (lbs)") == [] and lowercase_words("Water goal {{ x | lower }}") == ["goal"]
