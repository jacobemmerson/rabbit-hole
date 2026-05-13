from datetime import date, datetime, timezone
from unittest.mock import patch

import pytest

from app.models import UserProfile
from app.services.cookie import dump_profile
from app.services.reset import get_local_date, needs_reset

MOCK_ARTICLES = None  # populated by fixture below


def _profile_with_date(last_generated: str | None, tz: str = "UTC") -> UserProfile:
    return UserProfile(
        timezone=tz,
        last_generated=last_generated,
        selected_categories=["History"],
        current_articles=["Old_A", "Old_B", "Old_C", "Old_D", "Old_E"],
    )


def test_reset_no_last_generated():
    profile = _profile_with_date(None)
    assert needs_reset(profile) is True


def test_reset_different_date():
    profile = _profile_with_date("2026-05-10")
    with patch("app.services.reset.datetime") as mock_dt:
        mock_dt.now.return_value = datetime(2026, 5, 11, 12, 0, 0, tzinfo=timezone.utc)
        assert needs_reset(profile) is True


def test_no_reset_same_day():
    profile = _profile_with_date("2026-05-11", tz="UTC")
    with patch("app.services.reset.datetime") as mock_dt:
        mock_dt.now.return_value = datetime(2026, 5, 11, 15, 0, 0, tzinfo=timezone.utc)
        assert needs_reset(profile) is False


def test_no_reset_timezone_behind_server():
    # Server UTC time is 2026-05-12 02:00 UTC, but user is in America/Los_Angeles (UTC-7 in May)
    # which makes it still 2026-05-11 19:00 local — so no reset should fire.
    profile = _profile_with_date("2026-05-11", tz="America/Los_Angeles")
    with patch("app.services.reset.datetime") as mock_dt:
        import pytz
        la_tz = pytz.timezone("America/Los_Angeles")
        mock_dt.now.return_value = datetime(2026, 5, 12, 2, 0, 0, tzinfo=timezone.utc).astimezone(la_tz)
        assert needs_reset(profile) is False


def test_reset_produces_distinct_articles(client, mocker):
    old_titles = ["Old_A", "Old_B", "Old_C", "Old_D", "Old_E"]
    new_titles = ["New_F", "New_G", "New_H", "New_I", "New_J"]

    from app.models import ArticleSummary
    new_articles = [
        ArticleSummary(title=t, extract="x", page_url="") for t in new_titles
    ]
    mocker.patch("app.routes.pages.generate_feed", return_value=new_titles)
    mocker.patch("app.routes.pages.fetch_summaries_bulk", return_value=new_articles)

    profile = UserProfile(
        timezone="UTC",
        last_generated="2026-05-10",
        selected_categories=["History"],
        current_articles=old_titles,
        seen_articles=[],
    )
    cookie_val = dump_profile(profile)

    client.cookies.set("session", cookie_val)
    with patch("app.services.reset.datetime") as mock_dt:
        from datetime import datetime, timezone
        mock_dt.now.return_value = datetime(2026, 5, 11, 12, 0, 0, tzinfo=timezone.utc)
        response = client.get("/")

    assert response.status_code == 200
    # Verify the new articles appear in the rendered page
    for title in new_titles:
        assert title in response.text
    # Verify old articles are NOT in the rendered feed cards
    for title in old_titles:
        assert title not in response.text

    # Verify the updated cookie has old titles in seen_articles
    from app.services.cookie import load_profile
    set_cookie = response.headers.get("set-cookie", "")
    # Extract cookie value from Set-Cookie header
    cookie_part = [p for p in set_cookie.split(";") if "session=" in p]
    assert cookie_part, "No session cookie written"
    raw = cookie_part[0].split("session=", 1)[1]
    updated = load_profile(raw)
    # Unclicked articles are NOT moved to seen_articles on reset
    for title in old_titles:
        assert title not in updated.seen_articles
    for title in new_titles:
        assert title in updated.current_articles
