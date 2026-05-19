from unittest.mock import MagicMock, patch

import numpy as np
import pytest

import app.services.embeddings as emb
from app.models import CandidateItem, ReadingHistoryEntry, UserProfile
from app.services.cookie import dump_profile


def _mock_model(seed: int = 42) -> MagicMock:
    model = MagicMock()
    rng = np.random.default_rng(seed)

    def encode(texts, convert_to_numpy=True, normalize_embeddings=True):
        arr = rng.random((len(texts), 384)).astype(np.float32)
        norms = np.linalg.norm(arr, axis=1, keepdims=True)
        return arr / norms

    model.encode.side_effect = encode
    return model


@pytest.fixture(autouse=True)
def mock_embed_model():
    emb._model = _mock_model()


def test_score_returns_ranked_titles(client, signed_cookie_for):
    profile = UserProfile(
        selected_categories=["Science"],
        reading_history=[ReadingHistoryEntry(title="Quantum mechanics", rating=5, scroll_depth=90)],
    )
    client.cookies.set("session", signed_cookie_for(profile))

    candidates = [
        {"title": "Wave function",        "extract": "A wave function is...", "group": "Science"},
        {"title": "Schrödinger equation", "extract": "The Schrödinger equation...", "group": "Science"},
        {"title": "Roman Empire",         "extract": "The Roman Empire was...", "group": "History"},
    ]
    history_extracts = {"Quantum mechanics": "Quantum mechanics is a fundamental theory."}

    response = client.post(
        "/api/score",
        json={"candidates": candidates, "history_extracts": history_extracts, "n": 3},
    )
    assert response.status_code == 200
    data = response.json()
    assert "titles" in data
    assert len(data["titles"]) <= 3
    for t in data["titles"]:
        assert t in {c["title"] for c in candidates}


def test_score_no_history_returns_candidates(client, signed_cookie_for):
    profile = UserProfile(selected_categories=["Science"])
    client.cookies.set("session", signed_cookie_for(profile))

    candidates = [
        {"title": "Wave function", "extract": "A wave function is...", "group": "Science"},
    ]
    response = client.post("/api/score", json={"candidates": candidates, "history_extracts": {}, "n": 1})
    assert response.status_code == 200
    assert response.json()["titles"] == ["Wave function"]


def test_score_respects_n(client, signed_cookie_for):
    profile = UserProfile(selected_categories=["Science"])
    client.cookies.set("session", signed_cookie_for(profile))

    candidates = [
        {"title": f"Article{i}", "extract": f"Extract {i}.", "group": "Science"}
        for i in range(10)
    ]
    response = client.post("/api/score", json={"candidates": candidates, "history_extracts": {}, "n": 4})
    assert response.status_code == 200
    assert len(response.json()["titles"]) <= 4


def test_score_requires_cookie(client):
    # Without a valid session cookie the server returns an empty profile,
    # which still produces a valid (popularity/category-scored) ranking.
    candidates = [{"title": "Wave function", "extract": "...", "group": "Science"}]
    response = client.post("/api/score", json={"candidates": candidates, "history_extracts": {}, "n": 1})
    assert response.status_code == 200
