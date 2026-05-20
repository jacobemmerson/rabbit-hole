import logging

import numpy as np
from sentence_transformers import SentenceTransformer
from sklearn.cluster import KMeans

from app.models import ArticleSummary, UserProfile

logger = logging.getLogger(__name__)

_model: SentenceTransformer | None = None

# ── Tuning constants ──────────────────────────────────────────────────────────

RECENCY_DECAY    = 0.95   # weight multiplier per article back in history
NEGATIVE_LAMBDA  = 0.3    # how far to push away from 1★ articles
MIN_CLUSTER_SIZE = 5      # minimum articles per cluster before splitting
MAX_CLUSTERS     = 5      # never exceed this many taste vectors

# Thumbs up (5) → strong positive pull; thumbs down (1) → weak weight that
# goes to the negative pool in compute_taste_vectors.  Unrated → neutral 1.0.
_RATING_MAP = {5: 2.0, 1: 0.2}


def load_model() -> None:
    global _model
    logger.info("Loading sentence-transformers model all-MiniLM-L6-v2...")
    _model = SentenceTransformer("all-MiniLM-L6-v2")
    logger.info("Model loaded.")


def embed(texts: list[str]) -> np.ndarray:
    if not texts:
        raise ValueError("texts must be non-empty")
    return _model.encode(texts, convert_to_numpy=True, normalize_embeddings=True)


# ── Internal helpers ──────────────────────────────────────────────────────────

def _entry_base_weight(entry) -> float:
    base = _RATING_MAP.get(entry.rating, 1.0) if entry.rating else 1.0
    if entry.scroll_depth > 75:
        base += 0.3
    elif entry.scroll_depth < 25:
        base -= 0.3
    return max(base, 0.0)


def _weighted_mean(embs: np.ndarray, weights: np.ndarray) -> np.ndarray:
    return (embs * weights[:, np.newaxis]).sum(axis=0)


# ── Public API ────────────────────────────────────────────────────────────────

def compute_taste_vectors(
    profile: UserProfile,
    summaries: list[ArticleSummary],
) -> list[np.ndarray] | None:
    """Return one taste vector per interest cluster, or None if there is no
    positive signal.

    Three improvements over a plain weighted average:

    1. Recency decay — articles read earlier in history get exponentially less
       weight so the taste vector tracks current interests.
    2. Negative signal — explicitly 1★ articles are embedded separately and
       their centroid is subtracted from every cluster, pushing recommendations
       away from disliked content.
    3. Multi-vector — k-means clusters the positive embeddings so a user with
       broad interests (e.g. physics AND cooking) gets one vector per topic
       rather than a muddled midpoint.  Scoring takes the max cosine similarity
       across all vectors, so a candidate only needs to match ONE cluster.
    """
    extract_map = {s.title: s.extract for s in summaries if s.extract}

    pos_texts:   list[str]   = []
    pos_weights: list[float] = []
    neg_texts:   list[str]   = []
    neg_weights: list[float] = []

    n = len(profile.reading_history)
    for i, entry in enumerate(profile.reading_history):
        extract = extract_map.get(entry.title)
        if not extract:
            continue
        base = _entry_base_weight(entry)
        if base == 0.0:
            continue

        # age=0 for the most recent article; decay^0 = 1.0 (no penalty)
        recency = RECENCY_DECAY ** (n - 1 - i)

        if entry.rating == 1:
            # Actively disliked — push the taste vector away from these
            neg_texts.append(extract)
            neg_weights.append(base)
        else:
            pos_texts.append(extract)
            pos_weights.append(base * recency)

    # Custom interests are permanent anchors — no recency decay
    for text in profile.custom_interests:
        if text.strip():
            pos_texts.append(text.strip())
            pos_weights.append(1.5)

    if not pos_texts:
        return None

    # Batch positives and negatives into one encoder call to avoid two round-trips.
    all_texts = pos_texts + neg_texts
    all_embs  = embed(all_texts)
    pos_embs  = all_embs[:len(pos_texts)]
    neg_embs  = all_embs[len(pos_texts):]

    pos_w = np.array(pos_weights, dtype=np.float32)

    # Use total seen articles (pos + neg) for the cluster threshold so that
    # disliked articles still count toward activating additional sim dimensions,
    # even though they're excluded from the positive pool used for clustering.
    # Cap k at len(pos_texts) so k-means always has at least k samples.
    total_seen = len(pos_texts) + len(neg_texts)
    k = min(MAX_CLUSTERS, max(1, total_seen // MIN_CLUSTER_SIZE))
    k = min(k, max(1, len(pos_texts)))

    if k == 1:
        raw_vectors = [_weighted_mean(pos_embs, pos_w)]
    else:
        try:
            km = KMeans(n_clusters=k, n_init=3, max_iter=20, random_state=42)
            labels = km.fit_predict(pos_embs)
            raw_vectors = []
            for cid in range(k):
                mask = labels == cid
                if mask.any():
                    raw_vectors.append(_weighted_mean(pos_embs[mask], pos_w[mask]))
        except Exception:
            raw_vectors = [_weighted_mean(pos_embs, pos_w)]

    # Build unit-normalised negative centroid (if any 1★ articles)
    neg_unit: np.ndarray | None = None
    if neg_texts:
        neg_w    = np.array(neg_weights, dtype=np.float32)
        neg_raw  = _weighted_mean(neg_embs, neg_w)
        neg_norm = np.linalg.norm(neg_raw)
        if neg_norm > 1e-9:
            neg_unit = neg_raw / neg_norm

    # Normalise each cluster vector, subtract negative centroid, re-normalise
    result: list[np.ndarray] = []
    for raw in raw_vectors:
        norm = np.linalg.norm(raw)
        if norm < 1e-9:
            continue
        v = raw / norm
        if neg_unit is not None:
            v = v - NEGATIVE_LAMBDA * neg_unit
            norm2 = np.linalg.norm(v)
            if norm2 < 1e-9:
                continue
            v = v / norm2
        result.append(v)

    return result if result else None


def compute_taste_vector(
    profile: UserProfile, summaries: list[ArticleSummary]
) -> np.ndarray | None:
    """Single-vector wrapper kept for backward compatibility and tests."""
    vectors = compute_taste_vectors(profile, summaries)
    return vectors[0] if vectors else None


def score_candidates(
    taste_vector: np.ndarray, candidates: list[ArticleSummary]
) -> dict[str, float]:
    if not candidates:
        return {}
    texts      = [c.extract for c in candidates]
    embs       = embed(texts)
    scores     = embs @ taste_vector
    return {c.title: float(s) for c, s in zip(candidates, scores)}


def score_candidates_multi(
    taste_vectors: list[np.ndarray], candidates: list[ArticleSummary]
) -> list[dict[str, float]]:
    """Score candidates against every taste vector in one encoder call.

    Embeds candidate texts exactly once then dot-products against each vector,
    avoiding the k-times redundant embedding that calling score_candidates in a
    loop would cause.
    """
    if not candidates or not taste_vectors:
        return [{} for _ in taste_vectors]
    texts = [c.extract for c in candidates]
    embs  = embed(texts)                          # one call regardless of k
    return [
        {c.title: float(s) for c, s in zip(candidates, embs @ tv)}
        for tv in taste_vectors
    ]
