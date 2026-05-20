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
    assert vec is not None
    assert vec.shape == (384,)
    assert abs(np.linalg.norm(vec) - 1.0) < 1e-5


# ── Multi-vector, negative signal, recency ────────────────────────────────────

def test_negative_signal_shifts_taste_vector():
    # A 1★ article should push the taste vector away from its embedding.
    # Profile A: one liked article.
    # Profile B: same liked article + one 1★ article.
    # Both point in roughly the same liked direction, but B is deflected away
    # from the disliked article → the two vectors should differ.
    emb._model = _mock_model(seed=11)

    liked    = ArticleSummary(title="Liked",    extract="Liked article extract.",    page_url="")
    disliked = ArticleSummary(title="Disliked", extract="Disliked article extract.", page_url="")

    profile_a = UserProfile(reading_history=[
        ReadingHistoryEntry(title="Liked", rating=4, scroll_depth=80),
    ])
    profile_b = UserProfile(reading_history=[
        ReadingHistoryEntry(title="Liked",    rating=4, scroll_depth=80),
        ReadingHistoryEntry(title="Disliked", rating=1, scroll_depth=50),
    ])

    emb._model = _mock_model(seed=11)
    vec_a = emb.compute_taste_vector(profile_a, [liked, disliked])

    emb._model = _mock_model(seed=11)
    vec_b = emb.compute_taste_vector(profile_b, [liked, disliked])

    assert vec_a is not None
    assert vec_b is not None
    assert not np.allclose(vec_a, vec_b), "Negative signal had no effect on taste vector"


def test_recency_decay_weights_recent_articles_more():
    # Profile A: 5★ article is OLD, 3★ article is RECENT.
    #   effective weights: [2.0 * 0.95, 1.0 * 1.0] = [1.9, 1.0]
    # Profile B: 3★ article is OLD, 5★ article is RECENT.
    #   effective weights: [1.0 * 0.95, 2.0 * 1.0] = [0.95, 2.0]
    # With the same mock embeddings (e1, e2) both produce different weighted sums.
    emb._model = _mock_model(seed=22)

    art_a = ArticleSummary(title="A", extract="Article A text.", page_url="")
    art_b = ArticleSummary(title="B", extract="Article B text.", page_url="")

    profile_a = UserProfile(reading_history=[
        ReadingHistoryEntry(title="A", rating=5, scroll_depth=50),  # older, high rating
        ReadingHistoryEntry(title="B", rating=3, scroll_depth=50),  # newer, neutral rating
    ])
    profile_b = UserProfile(reading_history=[
        ReadingHistoryEntry(title="A", rating=3, scroll_depth=50),  # older, neutral rating
        ReadingHistoryEntry(title="B", rating=5, scroll_depth=50),  # newer, high rating
    ])

    emb._model = _mock_model(seed=22)
    vec_a = emb.compute_taste_vector(profile_a, [art_a, art_b])

    emb._model = _mock_model(seed=22)
    vec_b = emb.compute_taste_vector(profile_b, [art_a, art_b])

    assert vec_a is not None
    assert vec_b is not None
    assert not np.allclose(vec_a, vec_b), \
        "Recency × rating interaction had no effect — different weight distributions should diverge"


def test_multi_vector_returned_with_sufficient_history():
    # With MIN_CLUSTER_SIZE=5 and MAX_CLUSTERS=3, we need ≥10 articles for k=2.
    emb._model = _mock_model(seed=33)

    summaries = [
        ArticleSummary(title=f"Art{i}", extract=f"Extract for article {i}.", page_url="")
        for i in range(12)
    ]
    profile = UserProfile(reading_history=[
        ReadingHistoryEntry(title=f"Art{i}", rating=4, scroll_depth=70)
        for i in range(12)
    ])

    vectors = emb.compute_taste_vectors(profile, summaries)

    assert vectors is not None
    assert len(vectors) >= 2, f"Expected ≥2 taste vectors for 12 articles, got {len(vectors)}"
    for v in vectors:
        assert v.shape == (384,)
        assert abs(np.linalg.norm(v) - 1.0) < 1e-5


def test_only_one_star_articles_have_no_positive_signal():
    # All 1★ articles → no positive pool → None (fall back to cold start in pipeline)
    emb._model = _mock_model(seed=44)

    summaries = [
        ArticleSummary(title="X", extract="Article X.", page_url=""),
        ArticleSummary(title="Y", extract="Article Y.", page_url=""),
    ]
    profile = UserProfile(reading_history=[
        ReadingHistoryEntry(title="X", rating=1, scroll_depth=50),
        ReadingHistoryEntry(title="Y", rating=1, scroll_depth=50),
    ])

    vectors = emb.compute_taste_vectors(profile, summaries)
    assert vectors is None
