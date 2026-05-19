import pytest

from app.models import ReadingHistoryEntry, UserProfile
from app.services.cookie import dump_profile, load_profile


def _make_full_profile() -> UserProfile:
    return UserProfile(
        timezone="America/New_York",
        selected_categories=["History", "Science & Mathematics"],
        reading_history=[
            ReadingHistoryEntry(title="Photosynthesis", rating=3, scroll_depth=60)
        ],
    )


def test_roundtrip():
    profile = _make_full_profile()
    cookie  = dump_profile(profile)
    restored = load_profile(cookie)
    assert restored.timezone           == profile.timezone
    assert restored.selected_categories == profile.selected_categories
    assert len(restored.reading_history) == 1
    assert restored.reading_history[0].rating == 3


def test_load_none():
    profile = load_profile(None)
    assert isinstance(profile, UserProfile)
    assert profile.selected_categories == []
    assert profile.timezone == "UTC"


def test_load_empty_string():
    profile = load_profile("")
    assert isinstance(profile, UserProfile)


def test_load_tampered():
    cookie   = dump_profile(_make_full_profile())
    tampered = cookie[:-5] + "XXXXX"
    result   = load_profile(tampered)
    assert isinstance(result, UserProfile)
    assert result.selected_categories == []


def test_load_garbage():
    result = load_profile("not.a.valid.cookie.at.all!!!")
    assert isinstance(result, UserProfile)


def test_cookie_written_on_home_request(client, signed_cookie_for, empty_profile):
    client.cookies.set("session", signed_cookie_for(empty_profile))
    response = client.get("/", follow_redirects=False)
    assert "set-cookie" in response.headers
