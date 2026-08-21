from unittest.mock import MagicMock

import numpy as np
import pytest

import app.services.embeddings as emb
from app.models import ArticleSummary, ReadingHistoryEntry, UserProfile
from app.services.recommendations import generate_cold_start, generate_feed  # noqa: F401


def _make_mock_model(seed: int = 42) -> MagicMock:
    model = MagicMock()
    rng = np.random.default_rng(seed)

    def encode(texts, convert_to_numpy=True, normalize_embeddings=True):
        n = len(texts)
        arr = rng.random((n, 384)).astype(np.float32)
        norms = np.linalg.norm(arr, axis=1, keepdims=True)
        return arr / norms

    model.encode.side_effect = encode
    return model


MOCK_RELATED = [f"Cand{i}" for i in range(1, 14)]  # 13 candidates, enough for 10-article feeds
MOCK_SUMMARIES = [
    ArticleSummary(title=t, extract=f"Extract for {t}.", page_url="")
    for t in MOCK_RELATED + ["Quantum_mechanics", "Roman_Empire", "Photosynthesis"]
]


@pytest.mark.asyncio
async def test_cold_start_returns_5(cold_profile, mocker):
    mocker.patch("app.services.recommendations.fetch_search", return_value=MOCK_RELATED)
    mocker.patch("app.services.recommendations.get_popularity", return_value={})
    result = await generate_feed(cold_profile)
    assert len(result) == 8


@pytest.mark.asyncio
async def test_cold_start_used_for_fewer_than_3_history(cold_profile, mocker):
    mocker.patch("app.services.recommendations.fetch_search", return_value=MOCK_RELATED)
    mocker.patch("app.services.recommendations.get_popularity", return_value={})
    await generate_feed(cold_profile)
    assert len(cold_profile.reading_history) < 3


@pytest.mark.asyncio
async def test_warm_user_returns_5(warm_profile, mocker):
    mocker.patch("app.services.recommendations.fetch_search", return_value=MOCK_RELATED)
    mocker.patch("app.services.recommendations.fetch_summaries_bulk", return_value=MOCK_SUMMARIES)
    emb._model = _make_mock_model()
    result = await generate_feed(warm_profile)
    assert len(result) == 8


def test_taste_vector_changes_with_new_article():
    emb._model = _make_mock_model()
    base_history = [
        ReadingHistoryEntry(title="A", rating=5, scroll_depth=90),
        ReadingHistoryEntry(title="B", rating=3, scroll_depth=50),
        ReadingHistoryEntry(title="C", rating=2, scroll_depth=30),
    ]
    profile_a = UserProfile(reading_history=base_history)
    profile_b = UserProfile(
        reading_history=base_history + [ReadingHistoryEntry(title="D", rating=4, scroll_depth=80)]
    )
    summaries_a = [ArticleSummary(title=t, extract=f"Text {t}", page_url="") for t in ["A", "B", "C"]]
    summaries_b = summaries_a + [ArticleSummary(title="D", extract="Text D", page_url="")]

    vec_a = emb.compute_taste_vector(profile_a, summaries_a)
    vec_b = emb.compute_taste_vector(profile_b, summaries_b)

    assert vec_a is not None
    assert vec_b is not None
    assert not np.allclose(vec_a, vec_b)


def test_low_rating_reduces_weight():
    low = UserProfile(reading_history=[ReadingHistoryEntry(title="A", rating=1, scroll_depth=50)])
    high = UserProfile(reading_history=[ReadingHistoryEntry(title="A", rating=5, scroll_depth=50)])
    assert low.article_weight("A") < high.article_weight("A")


def test_high_rating_increases_weight():
    profile = UserProfile(
        reading_history=[ReadingHistoryEntry(title="A", rating=5, scroll_depth=50)]
    )
    assert profile.article_weight("A") == 2.0


@pytest.mark.asyncio
async def test_no_duplicates_in_feed(cold_profile, mocker):
    mocker.patch("app.services.recommendations.fetch_search", return_value=MOCK_RELATED)
    mocker.patch("app.services.recommendations.get_popularity", return_value={})
    result = await generate_feed(cold_profile)
    assert len(result) == len(set(result))


@pytest.mark.asyncio
async def test_different_histories_produce_different_recs(mocker):
    mocker.patch(
        "app.services.recommendations.fetch_search",
        return_value=MOCK_RELATED,
    )
    mocker.patch(
        "app.services.recommendations.fetch_summaries_bulk",
        return_value=MOCK_SUMMARIES,
    )

    # Each call to encode returns different values because the seeded rng advances
    emb._model = _make_mock_model(seed=7)

    history_a = [
        ReadingHistoryEntry(title="Quantum_mechanics", rating=5, scroll_depth=90),
        ReadingHistoryEntry(title="Roman_Empire", rating=5, scroll_depth=90),
        ReadingHistoryEntry(title="Photosynthesis", rating=5, scroll_depth=90),
    ]
    history_b = [
        ReadingHistoryEntry(title="Quantum_mechanics", rating=1, scroll_depth=5),
        ReadingHistoryEntry(title="Roman_Empire", rating=1, scroll_depth=5),
        ReadingHistoryEntry(title="Photosynthesis", rating=1, scroll_depth=5),
    ]

    profile_a = UserProfile(
        selected_categories=["Science & Mathematics"],
        reading_history=history_a,
    )
    profile_b = UserProfile(
        selected_categories=["Science & Mathematics"],
        reading_history=history_b,
    )

    result_a = await generate_feed(profile_a)
    result_b = await generate_feed(profile_b)

    # Different weights → different taste vectors → different ordering
    assert result_a != result_b


@pytest.mark.asyncio
async def test_cold_start_ranks_by_popularity(cold_profile, mocker):
    candidates = ["Obscure1", "Obscure2", "Popular1", "Obscure3", "Popular2"]
    mocker.patch("app.services.recommendations.fetch_search", return_value=candidates)
    mocker.patch("app.services.recommendations.get_popularity", return_value={
        "Popular1": 5,
        "Popular2": 12,
    })
    result = await generate_feed(cold_profile)
    # Both popular articles should appear before obscure ones
    popular_indices = [result.index(t) for t in ("Popular1", "Popular2") if t in result]
    obscure_indices = [result.index(t) for t in result if t not in ("Popular1", "Popular2")]
    if popular_indices and obscure_indices:
        assert max(popular_indices) < min(obscure_indices)


def test_alpha_is_1_at_10_articles():
    history = [
        ReadingHistoryEntry(title=f"Art{i}", rating=3, scroll_depth=50)
        for i in range(10)
    ]
    profile = UserProfile(reading_history=history)
    alpha = min(len(profile.reading_history) / 10, 1.0)
    assert alpha == 1.0


def test_alpha_below_1_before_10_articles():
    history = [
        ReadingHistoryEntry(title=f"Art{i}", rating=3, scroll_depth=50)
        for i in range(9)
    ]
    profile = UserProfile(reading_history=history)
    alpha = min(len(profile.reading_history) / 10, 1.0)
    assert alpha == 0.9
    assert alpha < 1.0


@pytest.mark.asyncio
async def test_cold_start_custom_interests_not_crowded_out(mocker):
    # With 2 categories and 1 custom interest there are 3 groups in the round-robin.
    # The custom interest must claim at least one slot even when category buckets
    # are full; previously custom filled only after categories were exhausted.
    cat_titles = [f"CatArticle{i}" for i in range(20)]
    custom_titles = [f"CustomArticle{i}" for i in range(10)]

    async def fake_search(query):
        if query.startswith("custom:"):
            return custom_titles
        return cat_titles

    mocker.patch("app.services.recommendations.fetch_search", side_effect=fake_search)
    mocker.patch("app.services.recommendations.get_popularity", return_value={})

    result = await generate_cold_start(
        selected_categories=["Science", "History"],
        seen_articles=[],
        count=6,
        custom_interests=["custom: deep sea biology"],
    )

    assert len(result) == 6
    custom_in_result = [t for t in result if t.startswith("CustomArticle")]
    assert len(custom_in_result) >= 1, "Custom interest got zero slots despite equal round-robin weight"
