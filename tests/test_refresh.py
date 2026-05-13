import pytest

from app.models import ArticleSummary, ReadingHistoryEntry, UserProfile
from app.services.cookie import dump_profile, load_profile


NEW_TITLES = ["New_F", "New_G", "New_H", "New_I", "New_J", "New_K", "New_L", "New_M"]
NEW_ARTICLES = [
    ArticleSummary(title=t, extract=f"Extract for {t}", page_url="")
    for t in NEW_TITLES
]


def test_article_weight_unrated():
    profile = UserProfile()
    assert profile.article_weight("anything") == 1.0


def test_article_weight_rated_high_scroll():
    profile = UserProfile(
        reading_history=[ReadingHistoryEntry(title="A", rating=4, scroll_depth=82)]
    )
    assert abs(profile.article_weight("A") - 1.8) < 1e-9


def test_article_weight_rated_low_scroll():
    profile = UserProfile(
        reading_history=[ReadingHistoryEntry(title="A", rating=2, scroll_depth=10)]
    )
    assert abs(profile.article_weight("A") - 0.2) < 1e-9


def test_article_weight_floor_at_zero():
    # rating=1 (0.2) with <25% scroll (-0.3) = -0.1 → clamped to 0.0
    profile = UserProfile(
        reading_history=[ReadingHistoryEntry(title="A", rating=1, scroll_depth=5)]
    )
    assert profile.article_weight("A") == 0.0


def test_refresh_5_new_articles(client, returning_profile, mocker):
    mocker.patch("app.routes.api.generate_feed", return_value=NEW_TITLES)
    mocker.patch("app.routes.api.fetch_summaries_bulk", return_value=NEW_ARTICLES)

    client.cookies.set("session", dump_profile(returning_profile))
    response = client.post("/api/refresh")

    assert response.status_code == 200
    data = response.json()
    returned_titles = [a["title"] for a in data["articles"]]
    assert len(returned_titles) == 8
    for title in returned_titles:
        assert title not in returning_profile.current_articles


def test_refresh_preserves_history(client, returning_profile, mocker):
    mocker.patch("app.routes.api.generate_feed", return_value=NEW_TITLES)
    mocker.patch("app.routes.api.fetch_summaries_bulk", return_value=NEW_ARTICLES)

    client.cookies.set("session", dump_profile(returning_profile))
    response = client.post("/api/refresh")

    set_cookie = response.headers.get("set-cookie", "")
    cookie_part = [p for p in set_cookie.split(";") if "session=" in p]
    assert cookie_part, "No session cookie set after refresh"
    raw = cookie_part[0].split("session=", 1)[1]
    updated = load_profile(raw)

    # Reading history untouched
    assert len(updated.reading_history) == len(returning_profile.reading_history)
    assert updated.reading_history[0].title == returning_profile.reading_history[0].title

    # Unclicked articles are NOT moved to seen on refresh
    for title in returning_profile.current_articles:
        assert title not in updated.seen_articles

    # New articles are current
    assert set(updated.current_articles) == set(NEW_TITLES)


def test_refresh_no_overlap_with_current(client, returning_profile, mocker):
    mocker.patch("app.routes.api.generate_feed", return_value=NEW_TITLES)
    mocker.patch("app.routes.api.fetch_summaries_bulk", return_value=NEW_ARTICLES)

    client.cookies.set("session", dump_profile(returning_profile))
    response = client.post("/api/refresh")

    set_cookie = response.headers.get("set-cookie", "")
    cookie_part = [p for p in set_cookie.split(";") if "session=" in p]
    raw = cookie_part[0].split("session=", 1)[1]
    updated = load_profile(raw)

    current_set = set(updated.current_articles)
    seen_set = set(updated.seen_articles)
    assert current_set.isdisjoint(seen_set)
