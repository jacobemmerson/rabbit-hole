# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with this repository.

## Commands

All commands use `uv`:

```bash
# Run dev server (auto-reload)
uv run uvicorn app.main:app --reload

# Run all tests
uv run pytest

# Run a single test file or test
uv run pytest tests/test_recommendations.py
uv run pytest tests/test_feed.py::test_name
```

The app requires a `.env` file with at minimum:
```
SECRET_KEY=<long random string>
```
See `.env.example` for all options (`DEBUG`, `REGION`).

## Architecture

**No database.** All user state is stored in a signed `itsdangerous` cookie (`UserProfile`). Cookie reads/writes happen on every request in `app/dependencies.py` (read) and inline in each route handler (write).

### Module layout

- `app/main.py` — FastAPI app factory + lifespan (loads embedding model, warms popularity cache at startup, runs 12-hour background refresh)
- `app/config.py` — pydantic-settings config from `.env`
- `app/models.py` — `UserProfile`, `ReadingHistoryEntry`, `ArticleSummary`, `FeedResponse`
- `app/routes/pages.py` — HTML routes: `/` (feed), `/onboarding`, `/article/{title}`
- `app/routes/api.py` — JSON/form routes: `POST /api/refresh`, `POST /api/onboard`, `POST /api/feedback`, `POST /api/scroll`, `GET /api/top-daily`
- `app/services/cookie.py` — `load_profile` / `dump_profile` using `itsdangerous.URLSafeSerializer`
- `app/services/wikipedia.py` — Wikipedia REST API + Action API client with in-memory TTL caches and rate-limit semaphores
- `app/services/embeddings.py` — `all-MiniLM-L6-v2` model wrapper (loaded once at startup into a module-level global)
- `app/services/recommendations.py` — `generate_feed` dispatcher, `generate_cold_start`, `generate_warm_user`
- `app/services/reset.py` — daily reset logic (compares local date in user's stored timezone)

### Recommendation pipeline

**Cold start** (< 3 articles in reading history): Wikipedia full-text search queries from `CATEGORY_SEED_POOL` (8 queries per category, 2 active per day via daily rotation), sorted by pageview popularity, round-robin balanced across selected categories.

**Warm user** (3+ articles): same candidate generation plus history-based search → batch-fetch summaries → embed with `all-MiniLM-L6-v2` → weighted taste vector (weights from star ratings + scroll depth bonus/penalty) → cosine similarity scoring. Final score blends embedding, category, and popularity signals:
- `alpha = min(len(reading_history) / 20, 1.0)` — embedding weight, full at 20 articles
- `pop_weight = max(0.20, 0.4 * exp(-n * 0.05))` — popularity weight, decays with history length
- Multi-category users get round-robin category balancing after scoring

**Seen article exclusion**: `seen_articles` (permanently visited) + `shown_articles` (sliding 42-article cooldown window from daily resets and manual refreshes) are both excluded from candidates.

### Wikipedia API client

Two separate rate-limit semaphores: `_rest_sem` (2 concurrent) for `/api/rest_v1/` and `_action_sem` (1 concurrent, serialized) for `/w/api.php`. `fetch_summaries_bulk` uses the MediaWiki batch action API (up to 50 titles per request) to avoid the per-article REST rate limiting that occurs during warm-user feed generation.

**Article proxying**: `fetch_wiki_page` proxies full Wikipedia HTML, strips navigation via injected CSS, rewrites `/wiki/` links to stay in-app at `/article/{title}`, and caches the result. User-specific chrome (back button, thumbs rating widget) is injected at serve time in `inject_article_chrome` — not cached — because it contains the user's current rating.

### Tests

`conftest.py` patches `load_model` and `fetch_top_articles` session-wide (GPU model load is skipped). `respx` is used to mock Wikipedia HTTP calls. The `extract_profile` helper in `conftest.py` reads `UserProfile` back out of the `set-cookie` response header to assert cookie state after API calls.
