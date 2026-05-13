from datetime import date, datetime

import pytz

from app.models import UserProfile


def get_local_date(timezone_str: str) -> date:
    try:
        tz = pytz.timezone(timezone_str)
    except pytz.exceptions.UnknownTimeZoneError:
        tz = pytz.UTC
    return datetime.now(tz).date()


def needs_reset(profile: UserProfile) -> bool:
    if profile.last_generated is None:
        return True
    local_today = get_local_date(profile.timezone)
    stored_date = date.fromisoformat(profile.last_generated)
    return local_today != stored_date
