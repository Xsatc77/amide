"""Who may see whose data. A share is a grant from an owner to a grantee for one category, and it counts only once the
grantee has accepted it (accepted_at is set). A pending share, one offered but not yet accepted, grants nothing anywhere.
Every read that widens visibility because of a share goes through the helpers here, so consent is checked in one place."""
from sqlalchemy import select

from app.models import Share, ShareCategory


def accepted():
    """The WHERE condition that keeps only accepted shares."""
    return Share.accepted_at.is_not(None)


def accepted_owner_ids(uid: int, category: ShareCategory):
    """A subquery of the owner ids whose data `uid` may see for `category`: accepted shares only."""
    return select(Share.owner_id).where(Share.grantee_id == uid, Share.category == category, accepted())


def accepted_categories(session, owner_id: int, grantee_id: int) -> set[ShareCategory]:
    """The categories `owner_id` has shared with `grantee_id` and the grantee has accepted."""
    return set(session.scalars(
        select(Share.category).where(Share.owner_id == owner_id, Share.grantee_id == grantee_id, accepted())))


def has_accepted_share(session, owner_id: int, grantee_id: int, category: ShareCategory) -> bool:
    return session.scalar(select(Share.id).where(
        Share.owner_id == owner_id, Share.grantee_id == grantee_id, Share.category == category, accepted())) is not None
