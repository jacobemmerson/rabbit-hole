from itsdangerous import URLSafeSerializer, BadSignature

from app.config import settings
from app.models import UserProfile

_serializer = URLSafeSerializer(settings.secret_key, salt="rabbit-hole-profile")


def load_profile(raw_cookie: str | None) -> UserProfile:
    if not raw_cookie:
        return UserProfile()
    try:
        data = _serializer.loads(raw_cookie)
        return UserProfile.model_validate(data)
    except (BadSignature, Exception):
        return UserProfile()


def dump_profile(profile: UserProfile) -> str:
    return _serializer.dumps(profile.model_dump())
