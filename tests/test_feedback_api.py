import pytest

from app.models import ReadingHistoryEntry, UserProfile
from app.services.cookie import dump_profile
from tests.conftest import extract_profile


def test_feedback_updates_existing_rating(client, cold_profile, signed_cookie_for):
    cold_profile.reading_history[0].rating = None
    client.cookies.set("session", signed_cookie_for(cold_profile))
    response = client.post(
        "/api/feedback",
        json={"title": "Quantum_mechanics", "rating": 4},
    )
    assert response.status_code == 200
    updated = extract_profile(response)
    entry = next(e for e in updated.reading_history if e.title == "Quantum_mechanics")
    assert entry.rating == 4


def test_feedback_creates_new_history_entry(client, cold_profile, signed_cookie_for):
    client.cookies.set("session", signed_cookie_for(cold_profile))
    response = client.post(
        "/api/feedback",
        json={"title": "Brand_New_Article", "rating": 3},
    )
    assert response.status_code == 200
    updated = extract_profile(response)
    titles = [e.title for e in updated.reading_history]
    assert "Brand_New_Article" in titles
    entry = next(e for e in updated.reading_history if e.title == "Brand_New_Article")
    assert entry.rating == 3


def test_feedback_rejects_out_of_range_rating(client, cold_profile, signed_cookie_for):
    client.cookies.set("session", signed_cookie_for(cold_profile))
    response = client.post("/api/feedback", json={"title": "Article", "rating": 6})
    assert response.status_code == 422


def test_feedback_rejects_zero_rating(client, cold_profile, signed_cookie_for):
    client.cookies.set("session", signed_cookie_for(cold_profile))
    response = client.post("/api/feedback", json={"title": "Article", "rating": 0})
    assert response.status_code == 422


def test_scroll_increases_depth(client, cold_profile, signed_cookie_for):
    cold_profile.reading_history[0].scroll_depth = 25
    client.cookies.set("session", signed_cookie_for(cold_profile))
    response = client.post(
        "/api/scroll",
        json={"title": "Quantum_mechanics", "depth": 75},
    )
    assert response.status_code == 200
    updated = extract_profile(response)
    entry = next(e for e in updated.reading_history if e.title == "Quantum_mechanics")
    assert entry.scroll_depth == 75


def test_scroll_does_not_decrease_depth(client, cold_profile, signed_cookie_for):
    cold_profile.reading_history[0].scroll_depth = 75
    client.cookies.set("session", signed_cookie_for(cold_profile))
    response = client.post(
        "/api/scroll",
        json={"title": "Quantum_mechanics", "depth": 25},
    )
    assert response.status_code == 200
    updated = extract_profile(response)
    entry = next(e for e in updated.reading_history if e.title == "Quantum_mechanics")
    assert entry.scroll_depth == 75


def test_scroll_creates_new_entry(client, cold_profile, signed_cookie_for):
    client.cookies.set("session", signed_cookie_for(cold_profile))
    response = client.post(
        "/api/scroll",
        json={"title": "New_Unread_Article", "depth": 50},
    )
    assert response.status_code == 200
    updated = extract_profile(response)
    titles = [e.title for e in updated.reading_history]
    assert "New_Unread_Article" in titles
    entry = next(e for e in updated.reading_history if e.title == "New_Unread_Article")
    assert entry.scroll_depth == 50
    assert entry.rating is None
