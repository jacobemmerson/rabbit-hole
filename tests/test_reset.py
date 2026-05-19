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


def test_reset_serves_stale_and_flags_auto_refresh(client, mocker):
    # On a daily reset the home route now serves the previous day's articles immediately
    # and signals JS to auto-call POST /api/refresh in the background.
    old_titles = ["Old_A", "Old_B", "Old_C", "Old_D", "Old_E"]

    from app.models import ArticleSummary
    old_articles = [ArticleSummary(title=t, extract="x", page_url="") for t in old_titles]
    mocker.patch("app.routes.pages.fetch_summaries_bulk", return_value=old_articles)

    profile = UserProfile(
        timezone="UTC",
        last_generated="2026-05-10",
        selected_categories=["History"],
        current_articles=old_titles,
        seen_articles=[],
    )

    client.cookies.set("session", dump_profile(profile))
    with patch("app.services.reset.datetime") as mock_dt:
        from datetime import datetime, timezone
        mock_dt.now.return_value = datetime(2026, 5, 11, 12, 0, 0, tzinfo=timezone.utc)
        response = client.get("/")

    assert response.status_code == 200
    # Stale articles are served immediately; JS will swap them via auto-refresh
    for title in old_titles:
        assert title in response.text
    # Page signals JS to trigger auto-refresh
    assert 'data-auto-refresh' in response.text

    # Cookie has last_generated bumped to today so reset doesn't re-fire
    from app.services.cookie import load_profile
    set_cookie = response.headers.get("set-cookie", "")
    cookie_part = [p for p in set_cookie.split(";") if "session=" in p]
    assert cookie_part, "No session cookie written"
    updated = load_profile(cookie_part[0].split("session=", 1)[1])
    assert updated.last_generated == "2026-05-11"
    # current_articles unchanged — POST /api/refresh handles generation and archiving
    assert set(updated.current_articles) == set(old_titles)
