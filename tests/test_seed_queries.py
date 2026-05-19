from datetime import date
from unittest.mock import patch

import pytest

from app.services.recommendations import CATEGORY_SEED_POOL


def test_seed_queries_returns_known_categories(client):
    response = client.get("/api/seed-queries?categories[]=Science&categories[]=History")
    assert response.status_code == 200
    data = response.json()
    keys = {g["key"] for g in data["groups"]}
    assert "Science" in keys
    assert "History" in keys


def test_seed_queries_custom_interests(client):
    response = client.get("/api/seed-queries?custom[]=Byzantine+architecture&custom[]=quantum+physics")
    assert response.status_code == 200
    data = response.json()
    keys = {g["key"] for g in data["groups"]}
    assert "__custom__0" in keys
    assert "__custom__1" in keys
    # The query for a custom interest is the text itself
    custom0 = next(g for g in data["groups"] if g["key"] == "__custom__0")
    assert custom0["queries"] == ["Byzantine architecture"]


def test_seed_queries_unknown_category_ignored(client):
    response = client.get("/api/seed-queries?categories[]=NonExistentCategory")
    assert response.status_code == 200
    data = response.json()
    assert data["groups"] == []


def test_seed_queries_returns_two_queries_per_category(client):
    response = client.get("/api/seed-queries?categories[]=Music")
    assert response.status_code == 200
    data = response.json()
    group = next(g for g in data["groups"] if g["key"] == "Music")
    assert len(group["queries"]) == 2


def test_seed_queries_rotates_daily(client):
    # Run the same request on two different "days" and confirm queries differ.
    # We simulate two ordinals that produce different starting positions.
    pool = CATEGORY_SEED_POOL["Science"]

    with patch("app.services.recommendations.date") as mock_date:
        mock_date.today.return_value = date(2026, 5, 1)
        r1 = client.get("/api/seed-queries?categories[]=Science").json()

    with patch("app.services.recommendations.date") as mock_date:
        mock_date.today.return_value = date(2026, 5, 3)
        r2 = client.get("/api/seed-queries?categories[]=Science").json()

    queries1 = r1["groups"][0]["queries"]
    queries2 = r2["groups"][0]["queries"]
    # Confirm both are subsets of the pool and differ across the two days
    assert all(q in pool for q in queries1)
    assert all(q in pool for q in queries2)
    assert queries1 != queries2
