"""Linear contextual bandit (LinTS-style) for personalised article scoring.

Feature vector φ(context, article), dimension 9:
  [sim_0, sim_1, sim_2, sim_3, sim_4,   ← cosine sims to taste vector clusters,
                                            sorted descending, zero-padded to 5
   pop_score,                            ← exp(-(rank-1)/150)
   cat_score,                            ← 1 if from a selected category, else 0
   from_history,                         ← 1 if sourced via reading-history search
   1.0]                                  ← bias

θ is learned per-user: A += φφᵀ, b += r·φ, θ = solve(A, b).
Warm-start θ approximates the current fixed-weight formula at n≈10 history articles
so ranking quality degrades gracefully before any feedback is collected.
"""

import numpy as np

# ── Constants ─────────────────────────────────────────────────────────────────

CLUSTER_FEATURES = 5          # must match MAX_CLUSTERS in embeddings.py
FEATURE_DIM      = CLUSTER_FEATURES + 4   # + pop + cat + from_history + bias
LAMBDA           = 1.0        # L2 regularisation (diagonal of initial A)

# Warm-start θ: approximates mid-range heuristic (n≈10 read articles)
#   embed_weight ≈ 0.38, pop_weight ≈ 0.24, cat_weight ≈ 0.38
# Embed weight is split across cluster slots with decreasing values to mimic
# the max-of-similarities behaviour before the bandit has learned the shape.
_THETA_WARM = [
    0.15,   # cluster sim 0  (best match)
    0.10,   # cluster sim 1
    0.07,   # cluster sim 2
    0.04,   # cluster sim 3
    0.02,   # cluster sim 4
    0.24,   # pop_score
    0.38,   # cat_score
    0.00,   # from_history  (no prior, learned from data)
    0.00,   # bias
]

assert len(_THETA_WARM) == FEATURE_DIM


# ── State ─────────────────────────────────────────────────────────────────────

class BanditState:
    """Minimal value object for serialisation; stored in IndexedDB / JSON."""
    __slots__ = ("theta", "A_flat", "b", "n_updates")

    def __init__(
        self,
        theta:    list[float],
        A_flat:   list[float],
        b:        list[float],
        n_updates: int = 0,
    ) -> None:
        self.theta     = theta
        self.A_flat    = A_flat
        self.b         = b
        self.n_updates = n_updates

    def to_dict(self) -> dict:
        return {
            "theta":     self.theta,
            "A_flat":    self.A_flat,
            "b":         self.b,
            "n_updates": self.n_updates,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "BanditState":
        return cls(
            theta=d["theta"],
            A_flat=d["A_flat"],
            b=d["b"],
            n_updates=d.get("n_updates", 0),
        )


def default_state() -> BanditState:
    """Return a warm-started bandit state."""
    theta = np.array(_THETA_WARM, dtype=np.float64)
    A     = np.eye(FEATURE_DIM, dtype=np.float64) * LAMBDA
    b     = A @ theta        # b = Aθ₀, so A⁻¹b = θ₀ (warm start)
    return BanditState(
        theta=theta.tolist(),
        A_flat=A.flatten().tolist(),
        b=b.tolist(),
    )


# ── Feature vector ────────────────────────────────────────────────────────────

def compute_phi(
    embed_scores_per_vector: list[dict[str, float]],
    title: str,
    pop_score: float,
    cat_score: float,
    from_history: float,
) -> list[float]:
    """Build the 9-dimensional feature vector for one candidate article.

    Cluster similarities are sorted descending and zero-padded to CLUSTER_FEATURES
    so the feature dimension is fixed regardless of how many taste vectors exist.
    """
    sims = sorted(
        (s.get(title, 0.0) for s in embed_scores_per_vector),
        reverse=True,
    )
    sims = (sims + [0.0] * CLUSTER_FEATURES)[:CLUSTER_FEATURES]
    return sims + [pop_score, cat_score, from_history, 1.0]


# ── Scoring ───────────────────────────────────────────────────────────────────

def score(phi: list[float], theta: list[float]) -> float:
    return float(np.dot(phi, theta))


# ── Online update (LinTS) ─────────────────────────────────────────────────────

def update(state: BanditState, phi: list[float], reward: float) -> BanditState:
    """Incorporate one (φ, r) observation into the posterior.

    A  ← A + φφᵀ
    b  ← b + r·φ
    θ  ← solve(A, b)          (numerically stable vs. A⁻¹b)
    """
    phi_arr = np.array(phi,          dtype=np.float64)
    A       = np.array(state.A_flat, dtype=np.float64).reshape(FEATURE_DIM, FEATURE_DIM)
    b       = np.array(state.b,      dtype=np.float64)

    A_new = A + np.outer(phi_arr, phi_arr)
    b_new = b + reward * phi_arr
    theta_new = np.linalg.solve(A_new, b_new)

    return BanditState(
        theta=theta_new.tolist(),
        A_flat=A_new.flatten().tolist(),
        b=b_new.tolist(),
        n_updates=state.n_updates + 1,
    )


# ── Reward helper ─────────────────────────────────────────────────────────────

def scroll_reward(scroll_depth: int) -> float:
    """Implicit reward from scroll depth: exponential decay toward 0.65.

    The engagement signal is strongest at the 75% threshold (reward ≈ 0.90)
    and decays toward 0.65 as scroll_depth increases, reflecting diminishing
    marginal informativeness of each additional percent scrolled.

      reward(s) = 0.65 + 0.25 · exp(−0.1 · (s − 75))
    """
    return 0.65 + 0.25 * float(np.exp(-0.1 * (scroll_depth - 75)))


def reward_from_feedback(rating: int | None, scroll_depth: int) -> float | None:
    """Map the three feedback signals to a reward in [0, 1].

    Explicit signals (highest priority):
      👍  (rating=5) → 1.0
      👎  (rating=1) → 0.0

    Implicit scroll signal (only used when no explicit rating):
      ≥75% scroll    → exponential decay toward 0.65 (see scroll_reward)

    Everything else → None  (no update; signal too ambiguous)
    """
    if rating == 5: return 1.0
    if rating == 1: return 0.0
    if scroll_depth >= 75: return scroll_reward(scroll_depth)
    return None
