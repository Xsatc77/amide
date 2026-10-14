"""The one- or two-letter symbol on a vial's periodic-table style tile: the name's first letter and the next consonant, so Retatrutide is Rt,
BPC-157 is Bp and Tirzepatide is Tr. A name with a single letter gets that letter; a name with none gets a question mark."""

_VOWELS = set("aeiou")


def symbol(name: str | None) -> str:
    letters = [c for c in (name or "") if c.isalpha()]
    if not letters:
        return "?"
    first, rest = letters[0].upper(), letters[1:]
    second = next((c for c in rest if c.lower() not in _VOWELS), rest[0] if rest else "")
    return first + second.lower()
