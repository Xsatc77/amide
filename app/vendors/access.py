"""Who may change an existing vendor. Vendors are shared (everyone signed in can see them, favorite them, add new ones
and read their price lists), but editing one -- payment wallets and QR images, the price-list file or link, the
website, the details, deleting it -- belongs to the person who added it, or an administrator. A vendor with no recorded
creator can only be changed by an administrator."""

from sqlalchemy.orm import Session

from app.models import User, Vendor


def may_edit_vendor(session: Session, vendor: Vendor, uid: int) -> bool:
    user = session.get(User, uid)
    if user is not None and user.is_admin:
        return True
    return vendor.created_by_id is not None and vendor.created_by_id == uid
