import asyncio
import math
from datetime import date

from app.config import settings
from app.models import UserProfile
from app.services import embeddings
from app.services.wikipedia import (
    fetch_search,
    fetch_summaries_bulk,
    get_popularity,
)

MAX_CANDIDATES = 30
# Pool of search queries per category (8 per category).
# Two are selected per day via daily rotation so consecutive feeds explore
# different corners of each topic.
CATEGORY_SEED_POOL: dict[str, list[str]] = {
    "Science":      ["quantum mechanics", "molecular biology",
                     "particle physics experiment", "neuroscience brain",
                     "evolutionary biology", "chemical reaction",
                     "thermodynamics physics", "genetics DNA"],
    "Mathematics":  ["number theory", "mathematical theorem",
                     "topology geometry", "graph theory",
                     "mathematical proof logic", "cryptography mathematics",
                     "game theory", "combinatorics"],
    "History":      ["ancient civilizations", "world war history",
                     "Roman empire history", "medieval period",
                     "ancient Egypt archaeology", "Renaissance history",
                     "colonial history", "industrial revolution history"],
    "Technology":   ["semiconductor technology", "industrial invention",
                     "engineering innovation", "renewable energy technology",
                     "telecommunications history", "manufacturing process",
                     "materials science", "transportation technology"],
    "Computing":    ["computer algorithm", "programming language history",
                     "computer network protocol", "operating system",
                     "database system", "software engineering",
                     "computer architecture", "cybersecurity"],
    "Arts":         ["Renaissance painting", "art movement",
                     "sculpture history", "modern art",
                     "abstract expressionism", "photography history",
                     "printmaking technique", "art nouveau"],
    "Culture":      ["world mythology", "cultural anthropology",
                     "folklore tradition", "cultural ritual",
                     "indigenous culture", "festival celebration",
                     "cultural exchange", "oral tradition"],
    "Geography":    ["geological formation", "physical geography",
                     "tectonic plates", "river ecosystem",
                     "mountain range geology", "ocean current",
                     "desert geography", "island formation"],
    "Philosophy":   ["philosophy of mind", "ethics moral",
                     "existentialism philosophy", "epistemology knowledge",
                     "ancient philosophy", "political philosophy",
                     "metaphysics reality", "logic reasoning"],
    "Religion":     ["religious history", "world religion",
                     "mythology sacred", "religious practice ritual",
                     "mysticism spirituality", "religious scripture",
                     "religious philosophy", "ancient religion"],
    "Sports":       ["Olympic sports history", "sports science",
                     "athletic achievement", "team sport strategy",
                     "sports medicine", "martial arts history",
                     "extreme sports", "sports psychology"],
    "Nature":       ["animal behavior", "plant evolution",
                     "wildlife ecology", "animal adaptation",
                     "marine biology", "bird migration",
                     "insect biology", "mammal evolution"],
    "Environment":  ["climate science", "conservation ecology",
                     "biodiversity ecosystem", "environmental policy",
                     "deforestation habitat", "ocean pollution",
                     "renewable energy environment", "species extinction"],
    "Politics":     ["political philosophy", "democracy history",
                     "political movement revolution", "international relations",
                     "civil rights movement", "election systems",
                     "diplomacy foreign policy", "political ideology"],
    "Society":      ["social movements", "cultural sociology",
                     "urban sociology", "social psychology",
                     "demographic change", "gender studies history",
                     "immigration culture", "social inequality"],
    "Literature":   ["classic novel", "literary fiction",
                     "narrative technique", "world literature",
                     "poetry form", "Shakespeare plays",
                     "dystopian fiction", "literary modernism"],
    "Music":        ["music theory", "classical composer",
                     "jazz history", "music genre evolution",
                     "musical instrument history", "opera history",
                     "folk music tradition", "music recording history"],
    "Film":         ["film history", "cinematic technique",
                     "film noir genre", "documentary filmmaking",
                     "animation history", "foreign cinema",
                     "film director", "silent film era"],
    "Television":   ["television history", "documentary series",
                     "television drama", "broadcast journalism",
                     "reality television", "television genre",
                     "streaming media history", "television production"],
    "Food":         ["culinary history", "food fermentation",
                     "gastronomy cuisine", "food science chemistry",
                     "agricultural history", "spice trade",
                     "bread baking history", "food preservation"],
    "Economics":    ["economic theory", "monetary history",
                     "market economy", "economic development",
                     "trade economics history", "banking financial history",
                     "economic inequality", "macroeconomics policy"],
    "Business":     ["entrepreneurship history", "business innovation",
                     "corporate history", "marketing advertising",
                     "supply chain management", "business strategy",
                     "stock market history", "economic monopoly"],
    "Health":       ["epidemiology", "nutrition science",
                     "public health policy", "mental health psychology",
                     "exercise physiology", "sleep science",
                     "immune system biology", "health disparities"],
    "Medicine":     ["medical history", "pharmacology drugs",
                     "surgical procedure history", "pathology disease",
                     "medical imaging technology", "vaccine development",
                     "neurology brain disorder", "cancer research"],
    "Space":        ["space exploration", "black hole",
                     "Mars mission history", "space station",
                     "rocket propulsion", "space telescope",
                     "asteroid comet", "space colonization"],
    "Astronomy":    ["stellar evolution", "exoplanet discovery",
                     "galaxy formation", "dark matter universe",
                     "cosmology big bang", "nebula star formation",
                     "planetary science", "astronomical observation"],
}

_QUERIES_PER_DAY = 2

# Prefixes that indicate Wikipedia meta-articles with no standalone reading value
_META_PREFIXES = (
    "Outline of ",
    "List of ",
    "Index of ",
    "Lists of ",
    "Glossary of ",
)


def _is_valid_article(title: str) -> bool:
    return not any(title.startswith(p) for p in _META_PREFIXES)


def get_daily_seeds(category: str) -> list[str]:
    """Return the 2 seed queries active today for a category.
    Rotates through the pool daily so consecutive feeds explore different topics.
    """
    pool = CATEGORY_SEED_POOL.get(category, [])
    if not pool:
        return []
    # Multiply day ordinal to advance more than 2 positions per day
    start = (date.today().toordinal() * _QUERIES_PER_DAY) % len(pool)
    return [pool[(start + i) % len(pool)] for i in range(min(_QUERIES_PER_DAY, len(pool)))]


def _round_robin(
    buckets: dict[str, list[str]],
    categories: list[str],
    picked: set[str],
    count: int,
) -> list[str]:
    """Pick `count` articles from per-category buckets using round-robin."""
    results: list[str] = []
    positions = {cat: 0 for cat in categories}

    while len(results) < count:
        made_progress = False
        for cat in categories:
            if len(results) >= count:
                break
            bucket = buckets[cat]
            pos = positions[cat]
            while pos < len(bucket):
                title = bucket[pos]
                pos += 1
                if title not in picked:
                    results.append(title)
                    picked.add(title)
                    made_progress = True
                    break
            positions[cat] = pos
        if not made_progress:
            break

    return results


async def generate_cold_start(
    selected_categories: list[str],
    seen_articles: list[str],
    count: int = 6,
) -> list[str]:
    cat_query_pairs: list[tuple[str, str]] = [
        (cat, q)
        for cat in selected_categories
        for q in get_daily_seeds(cat)
    ]

    if not cat_query_pairs:
        return []

    raw = await asyncio.gather(*[fetch_search(q) for _, q in cat_query_pairs])

    seen_set = set(seen_articles)
    buckets: dict[str, list[str]] = {cat: [] for cat in selected_categories}
    seen_in_bucket: dict[str, set[str]] = {cat: set() for cat in selected_categories}

    for (cat, _), batch in zip(cat_query_pairs, raw):
        for title in batch:
            if _is_valid_article(title) and title not in seen_set and title not in seen_in_bucket[cat]:
                buckets[cat].append(title)
                seen_in_bucket[cat].add(title)

    popularity = get_popularity()
    for cat in selected_categories:
        buckets[cat].sort(key=lambda t: popularity.get(t, 501))

    # Full round-robin across all categories until `count` is reached.
    # This ensures every selected interest gets representation before any
    # category takes a second slot — no global popularity fill that lets
    # one dominant category crowd out others.
    return _round_robin(buckets, selected_categories, set(), count)


async def _gather_candidates(
    profile: UserProfile, seen: set[str]
) -> tuple[list[str], dict[str, int], dict[str, str]]:
    """Return (ordered_candidates, appearance_counts, category_source).

    category_source maps each candidate title to the onboarding category
    whose seed query first returned it, enabling category-aware diversity
    in generate_warm_user.
    """
    cat_query_pairs: list[tuple[str, str]] = [
        (cat, q)
        for cat in profile.selected_categories
        for q in get_daily_seeds(cat)
    ]
    history_titles = [e.title for e in profile.reading_history]

    seed_results = await asyncio.gather(*[fetch_search(q) for _, q in cat_query_pairs])
    history_results = await asyncio.gather(*[fetch_search(t) for t in history_titles])

    appearance_counts: dict[str, int] = {}
    seen_candidates: set[str] = set()
    ordered: list[str] = []
    category_source: dict[str, str] = {}

    for (cat, _), batch in zip(cat_query_pairs, seed_results):
        for title in batch:
            if not _is_valid_article(title) or title in seen:
                continue
            if title not in seen_candidates:
                ordered.append(title)
                seen_candidates.add(title)
                category_source[title] = cat
            appearance_counts[title] = appearance_counts.get(title, 0) + 1

    for batch in history_results:
        for title in batch:
            if _is_valid_article(title) and title not in seen and title not in seen_candidates:
                ordered.append(title)
                seen_candidates.add(title)
                appearance_counts[title] = appearance_counts.get(title, 0) + 1

    return ordered[:MAX_CANDIDATES], appearance_counts, category_source


async def generate_warm_user(profile: UserProfile, seen: set[str]) -> list[str]:
    candidate_titles, appearance_counts, category_source = await _gather_candidates(profile, seen)

    if not candidate_titles:
        return await generate_cold_start(
            profile.selected_categories, list(seen), count=settings.feed_size
        )

    history_titles = [e.title for e in profile.reading_history]
    all_titles = list(dict.fromkeys(history_titles + candidate_titles))

    all_summaries = await fetch_summaries_bulk(all_titles)
    summary_map = {s.title: s for s in all_summaries}

    history_summaries = [summary_map[t] for t in history_titles if t in summary_map]
    candidate_summaries = [summary_map[t] for t in candidate_titles if t in summary_map]

    taste_vector = embeddings.compute_taste_vector(profile, history_summaries)
    if taste_vector is None:
        return await generate_cold_start(
            profile.selected_categories, list(seen), count=settings.feed_size
        )

    embed_scores = embeddings.score_candidates(taste_vector, candidate_summaries)

    max_count = max(appearance_counts.values(), default=1)
    cat_scores = {t: c / max_count for t, c in appearance_counts.items()}

    popularity = get_popularity()

    def _pop_score(title: str) -> float:
        rank = popularity.get(title)
        return 0.0 if rank is None else math.exp(-(rank - 1) / 150)

    n = len(profile.reading_history)
    pop_weight = max(0.20, 0.4 * math.exp(-n * 0.05))
    remaining = 1.0 - pop_weight
    # Slow alpha: taste vector reaches full weight at 20 articles (was 10).
    # Keeps scoring more exploratory during the early warm period.
    alpha = min(n / 20, 1.0)
    embed_weight = alpha * remaining
    cat_weight = (1.0 - alpha) * remaining

    scored = [
        (title, embed_weight * embed_scores.get(title, 0.0)
               + cat_weight * cat_scores.get(title, 0.0)
               + pop_weight * _pop_score(title))
        for title in candidate_titles
        if title in summary_map
    ]

    # Always apply category-aware round-robin for multi-category users.
    # Convergence happens organically: if the user consistently ignores a
    # category, its articles cycle into shown_articles cooldown and the
    # bucket naturally empties — no arbitrary article-count threshold needed.
    if len(profile.selected_categories) > 1:
        cat_buckets: dict[str, list[str]] = {cat: [] for cat in profile.selected_categories}
        overflow: list[tuple[str, float]] = []
        for title, score in sorted(scored, key=lambda x: x[1], reverse=True):
            cat = category_source.get(title)
            if cat in cat_buckets:
                cat_buckets[cat].append(title)
            else:
                overflow.append((title, score))

        results = _round_robin(cat_buckets, profile.selected_categories, set(), settings.feed_size)

        # Fill any remaining slots from history-sourced candidates (no category tag)
        picked = set(results)
        for title, _ in overflow:
            if len(results) >= settings.feed_size:
                break
            if title not in picked:
                results.append(title)
                picked.add(title)

        return results

    # Single-category user: pure score ordering is fine
    scored.sort(key=lambda x: x[1], reverse=True)
    return [t for t, _ in scored[: settings.feed_size]]


async def generate_feed(profile: UserProfile) -> list[str]:
    # Exclude both permanently-visited and recently-shown articles from candidates.
    seen = set(profile.seen_articles) | set(profile.shown_articles) | set(profile.current_articles)
    if len(profile.reading_history) < 3:
        return await generate_cold_start(
            profile.selected_categories, list(seen), count=settings.feed_size
        )
    return await generate_warm_user(profile, seen)
