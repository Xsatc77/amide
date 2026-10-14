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
