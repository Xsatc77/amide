"""The picture for an active medicine that is not a powder vial: what it looks like depends on its form."""

from app.models import Medium

_IMAGES = {
    Medium.PILL: "pill-bottle-blank-label.png",
    Medium.DROPS: "dropper-bottle-blank-label.png",
    Medium.SALVE: "salve-tube-blank-label.png",
    Medium.AUTOINJECTOR: "auto-injector.png",
    Medium.INHALER: "nasal-spray-blank-label.png",
    Medium.LIQUID: "vial-blank-label.png",
}


def image_for(medium: Medium | None) -> str:
    """Path under /static for the form's picture; a plain vial for anything without one of its own."""
    return "img/icons/" + _IMAGES.get(medium, "vial-blank-label.png")


# Where the periodic-table tile sits on each picture (centre as a fraction across and down the picture) and how big it is in pixels: on the label where the
# picture has one, on the body of the tube, spray bottle and autoinjector, which are narrower.
_TILES = {
    Medium.PILL: (0.50, 0.57, 44),
    Medium.DROPS: (0.50, 0.72, 42),
    Medium.SALVE: (0.50, 0.46, 36),
    Medium.AUTOINJECTOR: (0.50, 0.42, 30),
    Medium.INHALER: (0.50, 0.72, 34),
    Medium.LIQUID: (0.47, 0.51, 44),
}

# Words that name the form, not the medicine: "B-12 drops" is B-12.
_FORM_WORDS = {"drops", "drop", "pills", "pill", "capsules", "capsule", "tablets", "tablet", "softgels", "softgel", "spray", "cream", "salve", "ointment", "gel",
               "liquid", "oral", "tincture", "injector", "autoinjector", "pen", "inhaler"}


def tile_spec(medium: Medium | None) -> tuple[float, float, int]:
    return _TILES.get(medium, _TILES[Medium.LIQUID])


def symbol_for(name: str | None) -> str:
    """The tile's symbol for a medicine's name, without the word for its form (so "B-12 drops" is B, and "Vitamin D pills" is Vt)."""
    from app.inventory.symbol import symbol
    words = [w for w in (name or "").split() if w.lower().strip(".,()-") not in _FORM_WORDS]
    return symbol(" ".join(words) if words else name)
