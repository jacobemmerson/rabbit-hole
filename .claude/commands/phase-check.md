Compare the current codebase against the goals and test checklist for a given phase and report what's implemented vs. missing.

**Usage:** `/phase-check <1|2|3>`

**Arguments:** $ARGUMENTS

## Phase Definitions

### Phase 1 — Core Loop
Goals: FastAPI project structure, Wikipedia API client, signed cookie read/write, home feed of 5 articles, daily reset logic, refresh endpoint.

Tests:
- Wikipedia API client returns valid summaries and handles missing articles gracefully
- Cookie is correctly signed, parsed, and updated on each request
- Home feed renders exactly 5 articles for a brand-new user (empty cookie)
- Home feed renders exactly 5 articles for a returning user (populated cookie)
- Midnight reset produces a new set of 5 articles distinct from the previous day
- Reset triggers correctly for a user whose stored timezone is behind the server timezone
- Reset does not trigger mid-day for a returning user
- An unrated article defaults to a neutral weight of 1.0
- Clicking refresh generates 5 articles different from the current set without wiping history

### Phase 2 — Recommendations
Goals: Category graph scoring (cold start), sentence embedding scoring (warm user), alpha blending, star feedback widget, scroll depth tracking, candidate generation and deduplication.

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
- Alpha blending reaches 1.0 at exactly 10 articles read
- Clicking refresh generates 5 articles different from the current set without wiping history
- Articles replaced by a manual refresh are added to seen history

### Phase 3 — Frontend & Deployment
Goals: Onboarding flow, main feed UI, article detail view, scroll depth tracking JS, Nginx + Uvicorn, systemd service, HTTPS via Certbot, cookie consent, refresh button.

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
- Refresh button updates the feed with 5 new articles and marks previous 5 as seen

## Steps

1. Parse the phase number from the argument (default to 1 if missing).
2. Explore the project structure: routes, services, templates, tests directories.
3. For each goal in the selected phase, check whether the corresponding code exists and appears complete.
4. For each test case in the selected phase, check whether a corresponding test exists in the test suite.
5. Output two sections:
   - **Implemented** — goals and tests that are present and appear correct
   - **Missing / Incomplete** — goals and tests with no implementation or a stub
6. Suggest a priority order for tackling the missing items.
