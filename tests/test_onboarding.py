import numpy as np
import pytest
from unittest.mock import MagicMock

import app.services.embeddings as emb
from app.models import ArticleSummary, ReadingHistoryEntry, UserProfile
from app.services.cookie import dump_profile, load_profile


# ── Onboard endpoint ─────────────────────────────────────────────────────────

def test_onboard_saves_custom_interests(client):
    response = client.post(
        "/api/onboard",
        data={
            "selected_categories": ["Science"],
            "timezone": "UTC",
            "custom_interests": ["Byzantine architecture", "How vaccines work"],
        },
        follow_redirects=False,
    )
    assert response.status_code == 303

    set_cookie = response.headers.get("set-cookie", "")
    cookie_part = next((p for p in set_cookie.split(";") if "session=" in p), None)
    assert cookie_part, "No session cookie set"
    profile = load_profile(cookie_part.split("session=", 1)[1])

    assert profile.custom_interests == ["Byzantine architecture", "How vaccines work"]
    assert profile.selected_categories == ["Science"]


def test_onboard_caps_custom_interests_at_5(client):
    response = client.post(
        "/api/onboard",
        data={
            "selected_categories": ["Science"],
            "custom_interests": ["A", "B", "C", "D", "E", "F", "G"],
        },
        follow_redirects=False,
    )
    set_cookie = response.headers.get("set-cookie", "")
    cookie_part = next((p for p in set_cookie.split(";") if "session=" in p), None)
    profile = load_profile(cookie_part.split("session=", 1)[1])
    assert len(profile.custom_interests) == 5


def test_onboard_strips_blank_custom_interests(client):
    response = client.post(
        "/api/onboard",
        data={
            "selected_categories": ["Science"],
            "custom_interests": ["   ", "valid interest", ""],
        },
        follow_redirects=False,
    )
    set_cookie = response.headers.get("set-cookie", "")
    cookie_part = next((p for p in set_cookie.split(";") if "session=" in p), None)
    profile = load_profile(cookie_part.split("session=", 1)[1])
    assert profile.custom_interests == ["valid interest"]


def test_onboard_custom_only_no_categories(client):
    # Custom interests alone should be enough to submit (no category required)
    response = client.post(
        "/api/onboard",
        data={"custom_interests": ["Deep sea biology"]},
        follow_redirects=False,
    )
    assert response.status_code == 303
    set_cookie = response.headers.get("set-cookie", "")
    cookie_part = next((p for p in set_cookie.split(";") if "session=" in p), None)
    profile = load_profile(cookie_part.split("session=", 1)[1])
    assert profile.custom_interests == ["Deep sea biology"]
    assert profile.selected_categories == []


# ── Taste vector includes custom interests ───────────────────────────────────

def _mock_model(seed: int = 42) -> MagicMock:
    model = MagicMock()
    rng = np.random.default_rng(seed)

    def encode(texts, convert_to_numpy=True, normalize_embeddings=True):
        arr = rng.random((len(texts), 384)).astype(np.float32)
        norms = np.linalg.norm(arr, axis=1, keepdims=True)
        return arr / norms

    model.encode.side_effect = encode
    return model


def test_taste_vector_includes_custom_interests():
    emb._model = _mock_model()

    profile_without = UserProfile(
        reading_history=[ReadingHistoryEntry(title="A", rating=4, scroll_depth=80)],
        custom_interests=[],
    )
    profile_with = UserProfile(
        reading_history=[ReadingHistoryEntry(title="A", rating=4, scroll_depth=80)],
        custom_interests=["Byzantine architecture"],
    )

    summaries = [ArticleSummary(title="A", extract="Extract for A.", page_url="")]

    # Reset RNG state so both calls start from the same seed
    emb._model = _mock_model(seed=99)
    vec_without = emb.compute_taste_vector(profile_without, summaries)

    emb._model = _mock_model(seed=99)
    vec_with = emb.compute_taste_vector(profile_with, summaries)

    assert vec_without is not None
    assert vec_with is not None
    # Vectors should differ because the custom interest text is embedded alongside history
    assert not np.allclose(vec_without, vec_with)


def test_taste_vector_custom_only_no_history():
    emb._model = _mock_model(seed=7)

    profile = UserProfile(
        reading_history=[],
        custom_interests=["Fermentation and brewing"],
    )
    vec = emb.compute_taste_vector(profile, [])
    # Should produce a valid taste vector even with no reading history
    assert vec is not None
    assert vec.shape == (384,)
    assert abs(np.linalg.norm(vec) - 1.0) < 1e-5
