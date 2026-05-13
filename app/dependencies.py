from fastapi import Cookie

from app.config import settings
from app.models import UserProfile
from app.services.cookie import load_profile


async def get_user_profile(
    session: str | None = Cookie(default=None, alias=settings.cookie_name),
) -> UserProfile:
    return load_profile(session)
