from fastapi import HTTPException, Request


def current_user_id(request: Request) -> int:
    """The signed-in user's id. The gate guarantees a user on every app page; this is a safety net."""
    user = getattr(request.state, "user", None)
    if user is None:
        raise HTTPException(401, "Sign in required")
    return user.id
