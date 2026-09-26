"""Injection-site rotation. Pure functions, no database access."""

from app.models import InjectionSite

# Each site's mirrored opposite side of the same body part -- recommend() never crosses body parts.
_MIRROR = {
    InjectionSite.ABDOMEN_L: InjectionSite.ABDOMEN_R,
    InjectionSite.ABDOMEN_R: InjectionSite.ABDOMEN_L,
    InjectionSite.THIGH_L: InjectionSite.THIGH_R,
    InjectionSite.THIGH_R: InjectionSite.THIGH_L,
    InjectionSite.ARM_L: InjectionSite.ARM_R,
    InjectionSite.ARM_R: InjectionSite.ARM_L,
    InjectionSite.GLUTE_L: InjectionSite.GLUTE_R,
    InjectionSite.GLUTE_R: InjectionSite.GLUTE_L,
}

_SUBQ_SITES = [InjectionSite.ABDOMEN_L, InjectionSite.ABDOMEN_R, InjectionSite.THIGH_L,
              InjectionSite.THIGH_R, InjectionSite.ARM_L, InjectionSite.ARM_R]
_IM_SITES = _SUBQ_SITES + [InjectionSite.GLUTE_L, InjectionSite.GLUTE_R]

_ROUTE_SITES = {"subq": _SUBQ_SITES, "im": _IM_SITES}


def eligible_sites(route: str) -> list[InjectionSite]:
    """Sites this route can use. Empty for oral/nasal/topical/other -- those never show a picker."""
    return _ROUTE_SITES.get(route, [])


def recommend(last_site: InjectionSite | None, route: str) -> InjectionSite | None:
    """The site to highlight as recommended: the mirrored opposite side of the last-used site's
    body part. None if there's no prior site for this peptide, the route doesn't use sites at all,
    or the mirrored site isn't eligible for this route (never falls back to a different body part)."""
    sites = eligible_sites(route)
    if not sites or last_site is None:
        return None
    partner = _MIRROR.get(last_site)
    return partner if partner in sites else None
