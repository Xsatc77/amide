"""Match the exercise name on a workout plan to an exercise in the database, by function rather than spelling.

Tiers, first hit wins: exact name; alias; normalised (case, punctuation, DB/BB/KB abbreviations, plurals and word
order ignored; the exercise's own equipment counts, so "Barbell Bench Press" is "Bench Press"); then fuzzy, which
scores shared words and spelling together and compares equipment words. A fuzzy match is accepted only when it is
clearly the best, or when every near-best candidate would give the same calorie estimate. Anything weaker comes back
as suggestions for a person to confirm, never applied."""

import difflib
import re
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache

from app.workouts import exercise_db
from app.workouts.exercise_db import Exercise

_SYNONYMS = {"db": "dumbbell", "dumbell": "dumbbell", "bb": "barbell", "kb": "kettlebell", "bw": "bodyweight",
             "pushup": "push up", "pullup": "pull up", "chinup": "chin up", "situp": "sit up", "stepup": "step up",
             "ohp": "overhead press", "rdl": "romanian deadlift", "sldl": "stiff leg deadlift",
             "dumbbells": "dumbbell", "barbells": "barbell"}
# whole-query shortcuts for names people use that no database name spells
_QUERY_ALIASES = {"running": "Treadmill Run", "run": "Treadmill Run", "jogging": "Treadmill Jog",
                  "jog": "Treadmill Jog", "walking": "Treadmill Walk", "walk": "Treadmill Walk",
                  "military press": "Overhead Press", "incline walk": "Treadmill Incline Walk",
                  "incline walking": "Treadmill Incline Walk"}
_EQUIPMENT = {"barbell", "dumbbell", "cable", "machine", "kettlebell", "smith", "bodyweight", "band", "ez"}
_NOISE = {"the", "a", "an", "with", "on", "of", "and"}
# A fuzzy match may add only these words to what was typed ("Lat Pulldown" -> "Cable Lat Pulldown"); any other extra
# word ("Box", "Smith", "Sumo") makes it a different exercise, so it stays a suggestion.
_IGNORABLE_EXTRA = {"cable", "machine", "dumbbell", "barbell", "kettlebell", "bodyweight", "band", "ez"}
# Words that say little about which movement it is; ignored when looking for the nearest exercise to estimate from.
_FILLER = {"exercise", "ball", "stability", "swiss", "bosu", "weighted", "assisted", "single", "one", "alternating",
           "lying", "seated", "standing", "incline", "decline", "flat", "wide", "close", "narrow", "grip", "reverse"} | _EQUIPMENT
_APPROX_MIN = 0.5
_CONFIDENT = 0.70
_MARGIN = 0.05
_SUGGEST = 0.50
_MAX_SUGGESTIONS = 3


@dataclass(frozen=True)
class ExerciseMatch:
    exercise: Exercise | None
    how: str                      # "exact" | "alias" | "normalized" | "fuzzy" | "none"
    score: float = 1.0
    suggestions: tuple[Exercise, ...] = field(default_factory=tuple)

    @property
    def confident(self) -> bool:
        return self.exercise is not None


def _singular(word: str) -> str:
    if len(word) > 4 and word.endswith(("ches", "shes", "xes")):      # crunches, lunges stay; crunch, rush, box
        return word[:-2]
    return word[:-1] if len(word) > 2 and word.endswith("s") and not word.endswith("ss") else word


@lru_cache(maxsize=4096)
def tokens(text: str) -> tuple[str, ...]:
    """Lowercase words with abbreviations expanded and plurals folded, in order."""
    text = unicodedata.normalize("NFKC", text or "").casefold().replace("&", " and ")
    text = re.sub(r"\b(pull|push)[\s-]+down", r"\1down", text)       # "lat pull down" is "lat pulldown"
    out = []
    for word in re.findall(r"[a-z0-9]+", text):
        expanded = _SYNONYMS.get(word) or _SYNONYMS.get(_singular(word)) or word
        for part in expanded.split():
            part = _singular(part)
            if part and part not in _NOISE:
                out.append(part)
    return tuple(out)


def _key(words) -> str:
    return " ".join(sorted(words))


def _equipment_words(ex: Exercise) -> tuple[str, ...]:
    return tuple(w for w in tokens(ex.equipment or "") if w in _EQUIPMENT)


def _profile(ex: Exercise) -> tuple:
    """What decides a calorie estimate: two exercises with the same profile burn the same."""
    return ex.style, ex.sec_per_rep, ex.rest_min, ex.met, ex.model, ex.speed_table


@lru_cache(maxsize=1)
def _candidates() -> tuple[tuple[Exercise, tuple[str, ...], tuple[str, ...]], ...]:
    """(exercise, words of the name or alias, the same plus the exercise's own equipment) for every name and alias."""
    by_name = {e.name: e for e in exercise_db.all_exercises()}
    rows = []
    for e in by_name.values():
        words = tokens(e.name)
        rows.append((e, words, words + tuple(w for w in _equipment_words(e) if w not in words)))
    for alias, canonical in exercise_db.aliases().items():
        e = by_name.get(canonical)
        if e is not None:
            words = tokens(alias)
            rows.append((e, words, words + tuple(w for w in _equipment_words(e) if w not in words)))
    return tuple(rows)


def _score(query: tuple[str, ...], cand: tuple[str, ...]) -> float:
    a, b = set(query), set(cand)
    if not a or not b:
        return 0.0
    jaccard = len(a & b) / len(a | b)
    spelling = difflib.SequenceMatcher(None, _key(a), _key(b)).ratio()
    score = 0.55 * jaccard + 0.45 * spelling
    if a < b or b < a:
        score += 0.15          # every word of one is in the other: one name is the other with extra detail
    eq_a, eq_b = a & _EQUIPMENT, b & _EQUIPMENT
    if eq_a and eq_b and not (eq_a & eq_b):
        score -= 0.30          # a barbell curl is not a cable curl
    return min(max(score, 0.0), 0.99)


def approximate_exercise(name: str | None) -> Exercise | None:
    """The nearest exercise to estimate calories from when `match_exercise` is not sure: the one that shares the most
    of the typed name's meaningful words (the movement, not the equipment or posture), preferring the plainest name.
    For a calorie estimate only; it is never stored as a confirmed match."""
    match = match_exercise(name)
    if match.exercise:
        return match.exercise
    query = {w for w in tokens(name or "") if w not in _FILLER}
    if not query:
        query = set(tokens(name or ""))
    if not query:
        return None
    ranked = []
    for e, _, words in _candidates():
        core = {w for w in words if w not in _FILLER} or set(words)
        shared = len(query & core) / len(query)
        if shared >= _APPROX_MIN:
            ranked.append((-shared, len(core - query), len(e.name), e.name, e))
    if not ranked:
        return match.suggestions[0] if match.suggestions else None
    return min(ranked)[-1]


def match_exercise(name: str | None) -> ExerciseMatch:
    raw = (name or "").strip()
    query = tokens(raw)
    if not query:
        return ExerciseMatch(None, "none", 0.0)
    exact = exercise_db.get(raw)
    if exact:
        return ExerciseMatch(exact, "exact")
    canonical = exercise_db.canonical_for_alias(raw) or _QUERY_ALIASES.get(" ".join(re.findall(r"[a-z]+", raw.casefold())))
    if canonical and exercise_db.get(canonical):
        return ExerciseMatch(exercise_db.get(canonical), "alias")
    key = _key(query)
    for position in (1, 2):   # the name as written, then the name with its equipment ("barbell bench press")
        same = {row[0].name: row[0] for row in _candidates() if _key(row[position]) == key}
        if len(same) == 1:
            return ExerciseMatch(next(iter(same.values())), "normalized")

    best: dict[str, tuple[float, Exercise]] = {}
    for e, _, words in _candidates():
        score = _score(query, words)
        if score > best.get(e.name, (0.0, e))[0]:
            best[e.name] = (score, e)
    ranked = sorted(best.values(), key=lambda item: (-item[0], len(item[1].name), item[1].name))
    if not ranked or ranked[0][0] < _SUGGEST:
        return ExerciseMatch(None, "none", ranked[0][0] if ranked else 0.0)
    top_score = ranked[0][0]
    suggestions = tuple(e for s, e in ranked[:_MAX_SUGGESTIONS] if s >= _SUGGEST)
    if top_score >= _CONFIDENT:
        group = [e for s, e in ranked if s >= top_score - _MARGIN]
        only_equipment_extra = all(set(tokens(e.name)) - set(query) <= _IGNORABLE_EXTRA for e in group)
        if only_equipment_extra and (len(group) == 1 or len({_profile(e) for e in group}) == 1):
            return ExerciseMatch(group[0], "fuzzy", top_score, suggestions)
    return ExerciseMatch(None, "none", top_score, suggestions)
