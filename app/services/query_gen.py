import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer

from app.models import UserProfile
from app.services.embeddings import RECENCY_DECAY, embed

MAX_HISTORY_DOCS = 15  # most recent history articles considered
MAX_INIT_DOCS    = 20  # candidate articles used as init corpus when history is sparse
N_PER_DOC        = 5   # top bigrams extracted per history article
TOP_QUERIES      = 10  # final output after embedding rerank

_RATING_WEIGHT = {5: 2.0, 1: 0.2}


def _doc_weight(entry) -> float:
    base = _RATING_WEIGHT.get(entry.rating, 1.0) if entry.rating else 1.0
    if entry.scroll_depth > 75:
        base += 0.3
    elif entry.scroll_depth < 25:
        base -= 0.3
    return max(base, 0.0)


def extract_seed_bigrams(
    profile: UserProfile,
    history_extracts: dict[str, str],
    candidate_extracts: list[str],
    taste_vectors: list[np.ndarray] | None,
    n_per_doc: int = N_PER_DOC,
    top_n: int = TOP_QUERIES,
) -> list[str]:
    """Return up to top_n bigrams from the user's reading history, reranked by
    cosine similarity to their taste vectors.

    Candidate extracts (from the user's selected categories) serve as an
    initialisation corpus when history is sparse, giving IDF a meaningful
    background even on first use.
    """
    # Align history entries with available extracts, oldest first
    history_entries = [
        e for e in profile.reading_history
        if history_extracts.get(e.title)
    ][-MAX_HISTORY_DOCS:]

    history_texts = [history_extracts[e.title] for e in history_entries]

    # Per-document weights: recency decay × rating weight
    n = len(history_entries)
    doc_weights = [
        _doc_weight(e) * RECENCY_DECAY ** (n - 1 - i)
        for i, e in enumerate(history_entries)
    ]

    # Use candidate extracts as background corpus when history is thin
    init_texts = (
        [t for t in candidate_extracts if t][:MAX_INIT_DOCS]
        if len(history_texts) < 5 else []
    )

    corpus = history_texts + init_texts
    if len(corpus) < 3:
        return []

    vec = TfidfVectorizer(
        ngram_range=(2, 2),
        stop_words="english",
        max_features=300,
        min_df=1,
        sublinear_tf=True,
    )
    try:
        X = vec.fit_transform(corpus)
    except ValueError:
        return []
    terms = vec.get_feature_names_out()

    # Collect top n_per_doc bigrams per history document, weighted by doc_weight
    bigram_pool: dict[str, float] = {}
    for doc_idx, weight in enumerate(doc_weights):
        if weight == 0.0:
            continue
        row = np.asarray(X[doc_idx].todense()).flatten()
        weighted = row * weight
        top_idx = np.argsort(weighted)[::-1][:n_per_doc]
        for idx in top_idx:
            if weighted[idx] > 0:
                term = terms[idx]
                # Keep the highest weighted score seen for each bigram
                bigram_pool[term] = max(bigram_pool.get(term, 0.0), weighted[idx])

    if not bigram_pool:
        return []

    if not taste_vectors:
        ranked = sorted(bigram_pool.items(), key=lambda x: x[1], reverse=True)
        return [b for b, _ in ranked[:top_n]]

    # Embed candidate bigrams and rerank by max cosine similarity to taste clusters
    bigrams = list(bigram_pool.keys())
    embs = embed(bigrams)  # already L2-normalised
    sim_scores = {
        bigram: max(float(emb @ tv) for tv in taste_vectors)
        for bigram, emb in zip(bigrams, embs)
    }

    # Filter anti-correlated bigrams (noise that embeds away from all taste clusters)
    ranked = [(b, s) for b, s in sim_scores.items() if s > 0]
    ranked.sort(key=lambda x: x[1], reverse=True)
    return [b for b, _ in ranked[:top_n]]
