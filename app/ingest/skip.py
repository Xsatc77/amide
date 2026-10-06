"""Skip words and follow words: plain words (never patterns) matched as whole words, in any case."""

import re

MAX_WORDS, MAX_LENGTH = 20, 40


def parse_skip_words(raw: str | None) -> list[str]:
    """Split on commas and new lines, trim, lower-case, drop empties and repeats. Raises ValueError for a word over 40
    characters or more than 20 words."""
    words: list[str] = []
    for piece in re.split(r"[,\n]", raw or ""):
        word = piece.strip().lower()
        if not word or word in words:
            continue
        if len(word) > MAX_LENGTH:
            raise ValueError(f"A word can be at most {MAX_LENGTH} characters.")
        words.append(word)
    if len(words) > MAX_WORDS:
        raise ValueError(f"At most {MAX_WORDS} words.")
    return words


def find_skip_word(words: list[str], texts) -> str | None:
    """The first of `words` found as a whole word (letters or digits on either side mean it is part of another word)."""
    for text in texts:
        lowered = (text or "").lower()
        for word in words:
            if lowered and re.search(r"(?<![a-z0-9])" + re.escape(word) + r"(?![a-z0-9])", lowered):
                return word
    return None


def safe_words(raw: str | None) -> list[str]:
    """Like parse_skip_words but never raises: a stored value that is somehow invalid yields no words."""
    try:
        return parse_skip_words(raw)
    except ValueError:
        return []
