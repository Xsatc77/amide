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


# The periodic-table tile on each picture: (centre across, centre down as fractions of the picture, side in px, the picture's height in px). Measured against each
# picture's silhouette so the whole tile, border included, sits inside the item with a few pixels to spare, and centred on it. The narrow items (tube, spray bottle,
# autoinjector) are drawn taller so their tile can be a readable size. On the bottles with a white label (pill, dropper) the tile stays on the label, never on the
# orange or brown glass around it. tests/test_active_items.py re-measures the pictures, so a new picture cannot overhang.
_TILES = {
    Medium.PILL: (0.486, 0.555, 40, 160),
    Medium.DROPS: (0.50, 0.724, 42, 170),
    Medium.SALVE: (0.4865, 0.37, 40, 180),
    Medium.AUTOINJECTOR: (0.4474, 0.375, 33, 200),
    Medium.INHALER: (0.4603, 0.72, 40, 180),
    Medium.LIQUID: (0.462, 0.58, 48, 140),
}

# Words that name the form, not the medicine: "B-12 drops" is B-12.
_FORM_WORDS = {"drops", "drop", "pills", "pill", "capsules", "capsule", "tablets", "tablet", "softgels", "softgel", "spray", "cream", "salve", "ointment", "gel",
               "liquid", "oral", "tincture", "injector", "autoinjector", "pen", "inhaler"}


def tile_spec(medium: Medium | None) -> tuple[float, float, int, int]:
    return _TILES.get(medium, _TILES[Medium.LIQUID])


def symbol_for(name: str | None) -> str:
    """The tile's symbol for a medicine's name, without the word for its form (so "B-12 drops" is B, and "Vitamin D pills" is Vt)."""
    from app.inventory.symbol import symbol
    words = [w for w in (name or "").split() if w.lower().strip(".,()-") not in _FORM_WORDS]
    return symbol(" ".join(words) if words else name)
