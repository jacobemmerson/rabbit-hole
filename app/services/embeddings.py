import logging

import numpy as np
from sentence_transformers import SentenceTransformer

from app.models import ArticleSummary, UserProfile

logger = logging.getLogger(__name__)

_model: SentenceTransformer | None = None


def load_model() -> None:
    global _model
    logger.info("Loading sentence-transformers model all-MiniLM-L6-v2...")
    _model = SentenceTransformer("all-MiniLM-L6-v2")
    logger.info("Model loaded.")


def embed(texts: list[str]) -> np.ndarray:
    if not texts:
        raise ValueError("texts must be non-empty")
    return _model.encode(texts, convert_to_numpy=True, normalize_embeddings=True)


def compute_taste_vector(
    profile: UserProfile, summaries: list[ArticleSummary]
) -> np.ndarray | None:
    extract_map = {s.title: s.extract for s in summaries if s.extract}

    pairs: list[tuple[str, float]] = []
    for entry in profile.reading_history:
        extract = extract_map.get(entry.title)
        if extract:
            pairs.append((extract, profile.article_weight(entry.title)))

    # Custom interests act as permanent preference anchors in the taste vector.
    # Weight of 1.5 matches a positively-rated article with moderate scroll depth.
    for text in profile.custom_interests:
        if text.strip():
            pairs.append((text.strip(), 1.5))

    if not pairs:
        return None

    texts = [text for text, _ in pairs]
    weights = np.array([w for _, w in pairs], dtype=np.float32)

    embeddings = embed(texts)
    weighted = embeddings * weights[:, np.newaxis]
    total = weighted.sum(axis=0)

    norm = np.linalg.norm(total)
    if norm < 1e-9:
        return None

    return total / norm


def score_candidates(
    taste_vector: np.ndarray, candidates: list[ArticleSummary]
) -> dict[str, float]:
    if not candidates:
        return {}
    texts = [c.extract for c in candidates]
    embeddings = embed(texts)
    scores = embeddings @ taste_vector
    return {c.title: float(score) for c, score in zip(candidates, scores)}
