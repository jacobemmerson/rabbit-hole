# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

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

**Split state model.** User state is divided between a minimal signed `itsdangerous` cookie (server-trusted) and the client's IndexedDB (full state + caches). Wikipedia API calls happen from the user's browser, not the server. The server only runs embedding inference and scoring.

### Signed cookie (server-trusted, 4 fields only)
```json
{ "timezone", "selected_categories", "custom_interests", "reading_history" }
```
Read by the server for `/api/score` (embedding weights) and `/article/{title}` (current rating for the thumbs widget). Updated by `/api/feedback`, `/api/scroll`, and `/article/{title}`.

### IndexedDB (client-side, 6 stores)
| Store | Contents |
|---|---|
| `profile` | Full user state: categories, history, current/seen/shown articles, timezone, last_generated |
| `candidates` | Wikipedia search results keyed by query string (24 h TTL) |
| `summaries` | Article summaries including `wordCount` (6 h TTL) |
| `bandit` | Linear bandit state: θ, A (9×9), b, n_updates |
| `phi` | Feature vectors per article, keyed by title; populated by `/api/score`, consumed by `/api/bandit/update` |

### Module layout

**Server (`app/`)**
- `main.py`: FastAPI app factory + lifespan; loads embedding model, warms popularity/candidate caches, runs 12-hour background refresh
- `config.py`: pydantic-settings config from `.env`
- `models.py`: `UserProfile` (slim cookie model), `ReadingHistoryEntry`, `ArticleSummary`, `CandidateItem`, `ScoreRequest`, `BanditStateModel`
- `routes/pages.py`: HTML routes: `/` (feed shell + `__RH_INIT__` injection), `/onboarding`, `/article/{title}`
- `routes/api.py`: API routes (see below)
- `services/cookie.py`: `load_profile` / `dump_profile` using `itsdangerous.URLSafeSerializer`
- `services/wikipedia.py`: Wikipedia REST + Action API client; in-memory TTL caches; rate-limit semaphores (`_rest_sem=2`, `_action_sem=2`, both 0.15 s sleep); candidate cache persisted to `.cache/candidates.json`
- `services/embeddings.py`: `all-MiniLM-L6-v2` wrapper; `compute_taste_vectors` (multi-vector k-means + recency decay + negative signal); `score_candidates_multi` (batch embed once, dot-product k times)
- `services/recommendations.py`: `generate_cold_start`, `generate_warm_user`, `score_candidates_for_profile` (used by `/api/score`); `CATEGORY_SEED_POOL` + `get_daily_seeds`
- `services/bandits.py`: Linear Thompson Sampling: `default_state`, `compute_phi`, `update`, `reward_from_feedback`, `scroll_reward`

**Client (`app/static/`)**
- `db.js`: IndexedDB wrapper: all store helpers, TTL constants
- `wikipedia.js`: Client-side Wikipedia API calls with IDB caching; `search`, `bulkSummaries` (20-title batches, `exlimit=max`), `topDailySummaries`; includes `wordCount` from `page.length/6`
- `feed.js`: Feed orchestration: `needsReset`, `generateFeed` (seed-queries -> Wikipedia searches -> score -> render), `doRefresh`
- `main.js`: ES module; seeds IDB from `__RH_INIT__`; drives feed page (tab switching, refresh, clear-data modal, bandit updates on feedback/scroll)

**Media (`app/media/`)**
- `idle.gif`, `hopping.gif`: Rabbit wordmark animation, served at `/media/`

### Server API routes

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/seed-queries` | Today's Wikipedia search queries per category/custom interest group |
| `POST` | `/api/score` | Embed candidates + rank via bandit θ·φ; returns `titles`, `phi_map`, `bandit_state` |
| `POST` | `/api/bandit/update` | Online LinTS update: A += φφᵀ, b += r·φ, θ = solve(A, b) |
| `POST` | `/api/onboard` | Set slim cookie; redirect to `/` |
| `POST` | `/api/feedback` | Update rating in signed cookie (JS also writes IDB) |
| `POST` | `/api/scroll` | Update scroll depth in signed cookie (JS also writes IDB) |
| `POST` | `/api/reset` | Delete signed cookie (clear-data flow) |

### Feed generation flow (client-side)

1. `needsReset(profile)`: compares `last_generated` to today in user's timezone (JS equivalent of the old `reset.py`)
2. `GET /api/seed-queries`: server returns today's queries (no Wikipedia I/O)
3. `wikipedia.js search()` x N queries: parallel, IDB-cached 24 h
4. `wikipedia.js bulkSummaries()`: 20-title batches, IDB-cached 6 h
5. `POST /api/score`: server embeds + ranks; reads cookie for trusted reading history weights
6. Result stored in IDB `profile.current_articles`; bandit phi_map stored in IDB `phi`

### Recommendation pipeline

**Taste vectors** (`compute_taste_vectors`):
- Positives (rating ≠ 1): embedded with recency decay `0.95^age` × rating weight (👍=2.0, unrated=1.0)
- Custom interests: embedded at fixed weight 1.5 (no decay)
- 1★ articles: embedded separately → subtracted as negative centroid (`NEGATIVE_LAMBDA=0.3`)
- k-means clustering (`MAX_CLUSTERS=5`, `MIN_CLUSTER_SIZE=5`) for users with ≥10 positive articles
- Candidates scored via `score_candidates_multi`: embed once, dot-product against each cluster

**Bandit scoring** (`score_candidates_for_profile`):
Feature vector φ = `[sim₀…sim₄, pop_score, cat_score, from_history, 1.0]` (9 dims). Final score = θ·φ replaces the old fixed-weight formula. θ warm-starts from the heuristic weights and personalises over time via LinTS updates.

**Reward signals**: 👍→1.0, 👎→0.0, scroll≥75%→`0.65 + 0.25·exp(−0.1·(depth−75))`, mid-scroll→no update.

**Cold start** (< 3 history articles): `generate_cold_start`, daily-rotating seed queries, popularity-sorted, joint round-robin across categories + custom interests.

**Warm user**: `_gather_candidates` → `generate_warm_user` → multi-vector taste + bandit scoring + category/custom round-robin.

### Wikipedia API client

Server-side `fetch_summaries_bulk` (`services/wikipedia.py`) batches 50 titles per action API request, with two-pass extraction: `exintro=1` then `exsentences=3` fallback. Client-side `bulkSummaries` (`static/wikipedia.js`) batches 20 titles, the `prop=extracts` hard limit under `exlimit=max`, with the same sentence fallback plus redirect-alias handling so Wikimedia pageviews titles match Wikipedia canonical titles.

### `__RH_INIT__` handoff

`GET /` injects `<script>window.__RH_INIT__ = {...}</script>` with the server's cookie data. JS uses this to seed IDB on first load (or after clear) without reading the httpOnly cookie. If categories/custom interests changed (detected by comparison), `last_generated` is reset to null to force feed regeneration.

### Tests

`conftest.py` patches `load_model` and `fetch_top_articles` session-wide. `respx` mocks Wikipedia HTTP calls. `extract_profile` reads `UserProfile` from the `set-cookie` response header. Key test files: `test_score.py`, `test_bandit.py`, `test_seed_queries.py`, `test_onboarding.py`.
