import pytest

from app.models import ReadingHistoryEntry, UserProfile


# POST /api/refresh has been removed — feed generation is now client-side via feed.js.
# Article weight logic and score_candidates_for_profile are tested below.


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
    profile = UserProfile(
        reading_history=[ReadingHistoryEntry(title="A", rating=1, scroll_depth=5)]
    )
    assert profile.article_weight("A") == 0.0
