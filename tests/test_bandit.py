"""Tests for the linear contextual bandit: state, scoring, updates, and API."""
from unittest.mock import MagicMock

import numpy as np
import pytest

import app.services.embeddings as emb
from app.models import ReadingHistoryEntry, UserProfile
from app.services import bandits
from app.services.bandits import (
    FEATURE_DIM,
    BanditState,
    compute_phi,
    default_state,
    reward_from_feedback,
    scroll_reward,
    score,
    update,
)
from app.services.cookie import dump_profile


# ── Helpers ───────────────────────────────────────────────────────────────────

def _mock_model(seed: int = 0) -> MagicMock:
    model = MagicMock()
    rng = np.random.default_rng(seed)

    def encode(texts, convert_to_numpy=True, normalize_embeddings=True):
        arr = rng.random((len(texts), 384)).astype(np.float32)
        norms = np.linalg.norm(arr, axis=1, keepdims=True)
        return arr / norms

    model.encode.side_effect = encode
    return model


# ── Default state ─────────────────────────────────────────────────────────────

def test_default_state_has_correct_dimension():
    bs = default_state()
    assert len(bs.theta)  == FEATURE_DIM
    assert len(bs.A_flat) == FEATURE_DIM ** 2
    assert len(bs.b)      == FEATURE_DIM
    assert bs.n_updates   == 0


def test_default_state_theta_recoverable():
    # A⁻¹b should reproduce θ (up to floating-point noise)
    bs = default_state()
    A  = np.array(bs.A_flat).reshape(FEATURE_DIM, FEATURE_DIM)
    b  = np.array(bs.b)
    theta_recovered = np.linalg.solve(A, b)
    assert np.allclose(theta_recovered, bs.theta, atol=1e-9)


# ── Feature vector ────────────────────────────────────────────────────────────

def test_compute_phi_length():
    phi = compute_phi([], "A", pop_score=0.5, cat_score=1.0, from_history=0.0)
    assert len(phi) == FEATURE_DIM


def test_compute_phi_cluster_sims_sorted_descending():
    scores = [{"A": 0.3}, {"A": 0.9}, {"A": 0.6}]
    phi = compute_phi(scores, "A", pop_score=0.0, cat_score=0.0, from_history=0.0)
    cluster_sims = phi[:5]
    assert cluster_sims[0] == pytest.approx(0.9)
    assert cluster_sims[1] == pytest.approx(0.6)
    assert cluster_sims[2] == pytest.approx(0.3)
    assert cluster_sims[3] == pytest.approx(0.0)   # padded
    assert cluster_sims[4] == pytest.approx(0.0)   # padded


def test_compute_phi_bias_is_last():
    phi = compute_phi([], "A", pop_score=0.2, cat_score=0.8, from_history=1.0)
    assert phi[-1] == pytest.approx(1.0)
    assert phi[-2] == pytest.approx(1.0)   # from_history
    assert phi[-3] == pytest.approx(0.8)   # cat_score
    assert phi[-4] == pytest.approx(0.2)   # pop_score


# ── Scoring ───────────────────────────────────────────────────────────────────

def test_score_is_dot_product():
    theta = [1.0] * FEATURE_DIM
    phi   = [0.5] * FEATURE_DIM
    assert score(phi, theta) == pytest.approx(FEATURE_DIM * 0.5)


def test_higher_embed_score_ranks_higher_with_warm_theta():
    bs = default_state()
    phi_good = compute_phi([{"A": 0.9}], "A", pop_score=0.5, cat_score=1.0, from_history=0.0)
    phi_poor = compute_phi([{"B": 0.2}], "B", pop_score=0.5, cat_score=1.0, from_history=0.0)
    assert score(phi_good, bs.theta) > score(phi_poor, bs.theta)


# ── Online update ─────────────────────────────────────────────────────────────

def test_update_increments_n_updates():
    bs  = default_state()
    phi = [0.5] * FEATURE_DIM
    bs2 = update(bs, phi, reward=1.0)
    assert bs2.n_updates == 1


def test_update_theta_shifts_toward_positive_reward():
    bs  = default_state()
    phi = [1.0] + [0.0] * (FEATURE_DIM - 1)   # only first feature active

    bs2 = update(bs, phi, reward=1.0)
    # After a positive observation on feature 0, θ₀ should increase
    assert bs2.theta[0] > bs.theta[0]


def test_update_theta_shifts_away_from_negative_reward():
    bs  = default_state()
    phi = [1.0] + [0.0] * (FEATURE_DIM - 1)

    bs2 = update(bs, phi, reward=0.0)
    assert bs2.theta[0] < bs.theta[0]


def test_multiple_updates_converge():
    # After many consistent high-reward observations on a specific feature,
    # the bandit should weight it strongly.
    bs  = default_state()
    phi = [0.0] * FEATURE_DIM
    phi[7] = 1.0   # only from_history active

    for _ in range(50):
        bs = update(bs, phi, reward=1.0)

    assert bs.theta[7] > 0.5


# ── Reward helper ─────────────────────────────────────────────────────────────

def test_reward_thumbs_up():
    assert reward_from_feedback(5, 80) == pytest.approx(1.0)

def test_reward_thumbs_down():
    assert reward_from_feedback(1, 20) == pytest.approx(0.0)

def test_reward_scroll_mid_returns_none():
    assert reward_from_feedback(None, 50) is None

def test_reward_scroll_low_no_rating_returns_none():
    assert reward_from_feedback(None, 15) is None

def test_scroll_reward_at_threshold():
    # At scroll=75 the decay term is exp(0)=1, so reward = 0.65 + 0.25 = 0.90
    assert scroll_reward(75) == pytest.approx(0.90)

def test_scroll_reward_decays_toward_65():
    r75  = scroll_reward(75)
    r90  = scroll_reward(90)
    r100 = scroll_reward(100)
    # Monotonically decreasing toward the 0.65 asymptote
    assert r75 > r90 > r100 > 0.65

def test_scroll_reward_approaches_asymptote():
    import math
    # reward = 0.65 + 0.25·exp(−0.1·(100−75)) = 0.65 + 0.25·exp(−2.5)
    assert scroll_reward(100) == pytest.approx(0.65 + 0.25 * math.exp(-2.5))

def test_reward_from_feedback_uses_scroll_decay():
    # reward_from_feedback should delegate to scroll_reward for scroll >= 75
    assert reward_from_feedback(None, 75)  == pytest.approx(scroll_reward(75))
    assert reward_from_feedback(None, 100) == pytest.approx(scroll_reward(100))


# ── /api/score returns phi_map and bandit_state ───────────────────────────────

def test_score_endpoint_returns_phi_map(client, signed_cookie_for):
    emb._model = _mock_model()
    profile = UserProfile(selected_categories=["Science"])
    client.cookies.set("session", signed_cookie_for(profile))

    candidates = [{"title": "Wave function", "extract": "A wave function...", "group": "Science"}]
    res = client.post("/api/score", json={"candidates": candidates, "history_extracts": {}, "n": 1})

    assert res.status_code == 200
    data = res.json()
    assert "phi_map" in data
    assert "bandit_state" in data
    assert "Wave function" in data["phi_map"]
    assert len(data["phi_map"]["Wave function"]) == FEATURE_DIM


def test_score_endpoint_accepts_bandit_state(client, signed_cookie_for):
    emb._model = _mock_model()
    profile = UserProfile(selected_categories=["Science"])
    client.cookies.set("session", signed_cookie_for(profile))

    bs = default_state()
    candidates = [{"title": "Quantum entanglement", "extract": "Entanglement...", "group": "Science"}]
    res = client.post("/api/score", json={
        "candidates": candidates,
        "history_extracts": {},
        "bandit_state": bs.to_dict(),
        "n": 1,
    })
    assert res.status_code == 200
    assert res.json()["bandit_state"]["n_updates"] == 0   # unchanged (no update at score time)


# ── /api/bandit/update ────────────────────────────────────────────────────────

def test_bandit_update_endpoint(client):
    bs  = default_state()
    phi = [0.8, 0.0, 0.0, 0.0, 0.0, 0.5, 1.0, 0.0, 1.0]   # 9 features

    res = client.post("/api/bandit/update", json={
        "phi":          phi,
        "reward":       1.0,
        "bandit_state": bs.to_dict(),
    })
    assert res.status_code == 200
    updated = res.json()["bandit_state"]
    assert updated["n_updates"] == 1
    assert updated["theta"] != bs.theta   # θ should have changed
