"""Match a vendor's product name to library cards despite spacing, punctuation, case and typos.

Vendors spell the same compound many ways ("BPC157", "BPC 157", "Hexarelin Acetate"). Matching runs in
tiers and stops at the first tier with a hit, so a looser tier can never override a stricter one:

  exact       the same name, ignoring case
  normalized  the same name once spacing, punctuation, case and salt words ("Acetate") are ignored
  related     a card's alias, or a name with its parenthetical dropped ("Aicar" = "AICAR (Acadesine)")
  blend       the same set of ingredients, whatever the doses or order ("BPC157 5mg+TB500 5mg")
  fuzzy       one misspelled word, nothing else different (digits and word count must agree)

Words like with / without / no / DAC separate genuinely different products ("with B12" vs "without
B12", "CJC-1295 DAC" vs "CJC-1295 (No DAC)"), so no tier is allowed to treat them as noise.
"""

import difflib
import re
import unicodedata
from dataclasses import dataclass

_IGNORED_WORDS = {"acetate", "with"}
_QUALIFIERS = {"with", "without", "no", "non", "dac"}
_MIN_KEY = 3
_FUZZY_WORD_RATIO = 0.8
_FUZZY_TIE_MARGIN = 0.05
_SUGGEST_RATIO = 0.6
_ALIAS_SPLIT = re.compile(r"[,;]")  # a slash belongs inside an alias ("CJC-1295/Ipamorelin"), not between them
_PARENTHETICAL = re.compile(r"\(([^()]*)\)")
_DOSE = re.compile(r"\d+(?:\.\d+)?\s*(?:mg|mcg|iu)\b", re.IGNORECASE)


@dataclass(frozen=True)
class Match:
    cards: list
    how: str  # "exact" | "normalized" | "related" | "blend" | "fuzzy"


def _norm(text) -> str:
    return unicodedata.normalize("NFKC", text or "")


def _words(text) -> list[str]:
    return re.findall(r"[a-z0-9]+", _norm(text).casefold())


_NO_ABBREVIATIONS = {"without", "wo", "wout"}  # vendors write "CJC-1295 WO/Dac"


def _qualifier(word: str) -> str | None:
    if word in _NO_ABBREVIATIONS or difflib.SequenceMatcher(None, word, "without").ratio() >= 0.8:  # "whitout" too
        return "no"
    return word if word in _QUALIFIERS else None


def qualifiers(name) -> frozenset[str]:
    """The with / no / DAC words of a name ("without" counts as "no"): the words that make a different product."""
    return frozenset(q for q in map(_qualifier, _words(name)) if q)


def _tokens(text) -> list[str]:
    return ["no" if w == "without" else w for w in _words(text) if w not in _IGNORED_WORDS]


def name_key(name) -> str:
    """The comparison form of a name: lowercase letters and digits only, salt words dropped."""
    return "".join(_tokens(name))


def _name_keys(name) -> tuple[str, set[str]]:
    """(full key, related keys). Related keys come from the name without its parentheticals and from
    each parenthetical's own text -- unless a parenthetical carries a product qualifier."""
    text = _norm(name)
    full = name_key(text)
    related: set[str] = set()
    inners = _PARENTHETICAL.findall(text)
    if inners and not any(_QUALIFIERS & set(_words(inner)) for inner in inners):
        related.add(name_key(_PARENTHETICAL.sub(" ", text)))
        related.update(name_key(inner) for inner in inners)
    related.discard(full)
    return full, {k for k in related if len(k) >= _MIN_KEY}


def _card_keys(card) -> tuple[str, set[str]]:
    full, related = _name_keys(card.name)
    aliases = {name_key(a) for a in _ALIAS_SPLIT.split(card.aliases or "")}
    related |= {k for k in aliases if len(k) >= _MIN_KEY and k != full}
    return full, related


def match_name(vendor_name, cards) -> Match | None:
    raw = (vendor_name or "").strip()
    vendor_full, vendor_related = _name_keys(raw)
    if not vendor_full:
        return None

    exact = [c for c in cards if c.name.strip().casefold() == raw.casefold()]
    if exact:
        return Match(exact, "exact")

    keyed = [(c, *_card_keys(c)) for c in cards]
    normalized = [c for c, full, _ in keyed if full == vendor_full]
    if normalized:
        return Match(normalized, "normalized")

    wanted = vendor_related | ({vendor_full} if len(vendor_full) >= _MIN_KEY else set())
    related = [c for c, full, rel in keyed if wanted & (rel | {full})]
    if related:
        return Match(related, "related")

    ingredients = _ingredients(raw)
    if ingredients:
        blends = [c for c in cards if _ingredients(c.name) == ingredients]
        if blends:
            return Match(blends, "blend")

    return _fuzzy(raw, cards)


def _ingredients(name) -> frozenset[str] | None:
    """The set of ingredients of a "A + B + C" blend, doses removed; None when it isn't a blend."""
    text = _norm(name)
    plus_inners = [inner for inner in _PARENTHETICAL.findall(text) if "+" in inner]
    parts = (plus_inners[0] if plus_inners else text).split("+")
    keys = [name_key(_DOSE.sub(" ", part)) for part in parts]
    keys = frozenset(k for k in keys if k)
    return keys if len(keys) >= 2 else None


def _digits(tokens: list[str]) -> list[str]:
    return re.findall(r"\d+", " ".join(tokens))


def _fuzzy(raw: str, cards) -> Match | None:
    vendor = _tokens(raw)
    if not vendor:
        return None
    scored = []
    for card in cards:
        theirs = _tokens(card.name)
        if len(theirs) != len(vendor) or _digits(theirs) != _digits(vendor):
            continue
        differing = [(a, b) for a, b in zip(vendor, theirs) if a != b]
        if len(differing) != 1:
            continue
        a, b = differing[0]
        if a in _QUALIFIERS or b in _QUALIFIERS:
            continue
        ratio = difflib.SequenceMatcher(None, a, b).ratio()
        if ratio >= _FUZZY_WORD_RATIO:
            scored.append((ratio, card))
    if not scored:
        return None
    scored.sort(key=lambda item: item[0], reverse=True)
    if len(scored) > 1 and scored[1][0] >= scored[0][0] - _FUZZY_TIE_MARGIN:
        return None
    return Match([scored[0][1]], "fuzzy")


def suggest(vendor_name, cards, limit: int = 3) -> list[str]:
    """Closest card names for a vendor name nothing matched, for a human to judge. Never auto-applied."""
    vendor_full, _ = _name_keys(vendor_name)
    if not vendor_full:
        return []
    scored = []
    for card in cards:
        full, related = _card_keys(card)
        best = max(difflib.SequenceMatcher(None, vendor_full, key).ratio() for key in {full, *related})
        if best >= _SUGGEST_RATIO:
            scored.append((best, card.name))
    scored.sort(key=lambda item: item[0], reverse=True)
    return [name for _, name in scored[:limit]]
