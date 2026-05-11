# Rabbit Hole - Daily Wikipedia Recommendation System
Rabbit Hole is a self-hosted app that delivers 5 daily Wikipedia article
recommendations, refreshing at midnight in the user's local timezone.
Recommendations are tailored to each user's preferences and interests, inferred
from browsing history, article interaction patterns, scroll depth tracking, and
a 1–5 star feedback system. All user state is stored client-side via signed
cookies — no user database required.

### Hardware
Dell Laptop with a GTX 1650 Ti, hosting an Ubuntu remote server

### Tech Stack
- Python (FastAPI) — async backend + API routes
- Wikipedia REST API — article fetching, summaries, related articles, categories
- Sentence Transformers (all-MiniLM-L6-v2) — semantic article embeddings (384-dim)
- scikit-learn — cosine similarity scoring
- itsdangerous — signed cookie serialization
- Nginx + Uvicorn — reverse proxy and ASGI server
- Jinja2 — server-side HTML templating
- Let's Encrypt (Certbot) — HTTPS

---

# Recommendation System

## Candidate Generation
Candidates are sourced from two Wikipedia API endpoints:
- `GET /api/rest_v1/page/related/{title}` — for each article in reading history
- Category member API — for each category in the user's weighted category map
Candidates are deduplicated and filtered against already-seen articles before scoring.

## Hybrid Scoring (Category Graph + Sentence Embeddings)

### Cold Start (fewer than 3 articles in reading history)
1. Build a weighted category frequency map from the user's selected topics
   and any reading history available
2. Fetch candidate articles from those categories via the Wikipedia API
3. Score candidates by category weight; return top 5

### Warm Users (3+ articles in reading history)
1. Fetch the summary text of each article in the user's reading history
2. Embed each summary into a 384-dimension vector using all-MiniLM-L6-v2
3. Compute a weighted average of all embeddings to form a user taste vector:
   - Base weight determined by star rating: unrated=1.0, 1★=0.2, 2★=0.5,
  3★=1.0, 4★=1.5, 5★=2.0
   - Scroll depth bonus: >75% scroll adds +0.3 to weight; <25% subtracts 0.3
4. Embed each candidate article's summary using the same model
5. Score each candidate by cosine similarity to the user taste vector
6. Blend with category score: weight shifts fully to embeddings by 10 articles read
   alpha = min(len(reading_history) / 10, 1.0)
   final_score = alpha * embedding_score + (1 - alpha) * category_score
7. Return top 5 ranked candidates

## Daily Reset (Local Time)
- On first visit, JS detects the user's timezone via:
  Intl.DateTimeFormat().resolvedOptions().timeZone
  and stores it in the cookie alongside the date recommendations were last generated
- On each visit, the server compares today's date in the user's stored timezone
  against the stored generation date; if different, recommendations regenerate
- The 5 daily articles are cached in the cookie until the next reset
- A manual refresh button allows the user to regenerate 5 new articles at any time
  with no cooldown limit; articles from the replaced set are added to the seen
  history and will not appear in future recommendations

---

# Preset Categories (Onboarding)
Users select 1–5 categories from the following list:

| Category | Wikipedia Seed |
|---|---|
| Science & Mathematics | Science, Mathematics |
| History | History |
| Technology & Computing | Technology, Computing |
| Arts & Culture | Arts, Culture |
| Geography & Places | Geography |
| Philosophy & Religion | Philosophy, Religion |
| Sports & Recreation | Sports, Recreation |
| Nature & Environment | Nature, Environment |
| Politics & Society | Politics, Society |
| Literature & Writing | Literature |
| Music | Music |
| Film & Television | Film, Television |
| Food & Cuisine | Food, Cuisine |
| Economics & Business | Economics, Business |
| Health & Medicine | Health, Medicine |
| Space & Astronomy | Astronomy, Space |

---

# Phase 1: Core Loop
Focus: Scaffold the app and establish reliable Wikipedia data fetching.

Goals:
- Set up FastAPI project structure with working routes
- Integrate Wikipedia REST API (search, summary, related, categories)
- Implement signed cookie read/write for user profile storage:
  (selected categories, reading history, feedback history,
  scroll depth history, timezone, last generation date)
- Serve a basic home feed of 5 articles seeded by user-selected categories
- Daily reset logic: on each visit compare today's date in the user's stored
  timezone against last generation date; regenerate if different
- Add a refresh button that calls POST /api/refresh to regenerate 5 new articles
  on demand; preserves all history, feedback, and scroll data in the cookie

Tests:
- Wikipedia API client returns valid summaries and handles missing articles gracefully
- Cookie is correctly signed, parsed, and updated on each request
- Home feed renders exactly 5 articles for a brand-new user (empty cookie)
- Home feed renders exactly 5 articles for a returning user (populated cookie)
- Midnight reset produces a new set of 5 articles distinct from the previous day
- Reset triggers correctly for a user whose stored timezone is behind the server timezone
- Reset does not trigger mid-day for a returning user
- An unrated article defaults to a neutral weight of 1.0 in the user taste vector
- Clicking refresh generates 5 articles different from the current set without
  wiping reading history, feedback scores, or scroll depth data

---

# Phase 2: Recommendations
Focus: Build a hybrid recommender that improves with use.

Goals:
- Implement Wikipedia category graph scoring (cold start — fewer than 3 articles read)
- Implement sentence embedding scoring using all-MiniLM-L6-v2 (3+ articles read)
- Blend both signals using alpha = min(len(reading_history) / 10, 1.0)
- Weight embeddings by star rating (1★=0.2, 2★=0.5, 3★=1.0, 4★=1.5, 5★=2.0)
  and scroll depth bonus/penalty (>75% = +0.3, <25% = -0.3)
- Add 1–5 star feedback widget; store ratings in cookie
- Track scroll depth per article via JS milestone pings (25/50/75/100%);
  store highest reached depth in cookie
- Candidate generation via Wikipedia related-article and category-member APIs
- Deduplicate candidates against all seen articles, including those dismissed
  via manual refresh

Tests:
- Cold-start user (0–2 articles) receives 5 category-based recommendations
- Warm user (3+ articles) receives embedding-scored recommendations
- User taste vector updates correctly after a new article is read
- Feedback score of 1–2 reduces cosine similarity weighting of similar articles
- Feedback score of 4–5 increases cosine similarity weighting of similar articles
- Scroll depth >75% applies +0.3 bonus to article weight
- Scroll depth <25% applies -0.3 penalty to article weight
- No article appears in recommendations twice in the same daily set
- Recommendations for two users with different histories are meaningfully different
- Alpha blending reaches 1.0 (full embedding weight) at exactly 10 articles read
- Clicking refresh generates 5 articles different from the current set without
  wiping reading history, feedback scores, or scroll depth data
- Articles replaced by a manual refresh are added to the seen history and do
  not appear in any future recommendation set

---

# Phase 3: Frontend & Deployment
Focus: Polish the UI and deploy to the VPS.

Goals:
- Design and implement onboarding flow:
  - Display the 16 preset categories as a visual picker
  - Enforce selection of 1–5 categories
  - Detect and store user timezone via Intl.DateTimeFormat().resolvedOptions().timeZone
  - Write selections and timezone to cookie on submit; redirect to feed
- Build the main feed UI (5 article cards with title, summary, category tags)
- Build the article detail view (full summary, feedback widget, scroll tracking)
- Implement scroll depth tracking: ping /api/scroll at 25/50/75/100% milestones;
  store highest reached depth in cookie
- Configure Nginx as a reverse proxy in front of Uvicorn
- Set up systemd service to keep the app alive across reboots
- Obtain HTTPS certificate via Certbot / Let's Encrypt
- Add cookie consent notice (GDPR baseline)
- Add a refresh button to the feed UI that calls POST /api/refresh and re-renders
  the feed without a full page reload

Tests:
- Onboarding displays all 16 preset categories
- Onboarding enforces minimum of 1 and maximum of 5 category selections
- Timezone is correctly detected and stored in cookie on onboarding submit
- Article cards render correctly on desktop and mobile viewports
- Scroll depth milestones (25/50/75/100%) are correctly recorded in the cookie
- Feedback widget (1–5 stars) submits and updates cookie without page reload
- App survives a simulated server reboot (systemd restarts Uvicorn automatically)
- All routes are reachable over HTTPS with no mixed-content warnings
- Invalid or expired cookies are handled gracefully (user is reset to onboarding)
- Refresh button updates the feed with 5 new articles and correctly marks the
  previous 5 as seen in the cookie, without resetting category selections,
  reading history, or feedback data

# Cookie Structure Example

```
{
  "timezone": "America/New_York",
  "last_generated": "2026-05-11",
  "selected_categories": ["Science & Mathematics", "History"],
  "current_articles": ["Quantum_mechanics", "Roman_Empire", "..."],
  "seen_articles": ["Photosynthesis", "Napoleon", "..."],
  "reading_history": [
    { "title": "Quantum_mechanics", "rating": 4, "scroll_depth": 82 },
    { "title": "Photosynthesis", "rating": null, "scroll_depth": 30 }
  ]
}
```
