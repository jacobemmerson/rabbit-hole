# Rabbit Hole — Interview Prep

A walkthrough of the repository and a bank of likely interview Q&As. The goal is to be able to
explain not just *what* the code does but *why* each decision was made and what the alternatives were.

---

## 1. Elevator pitch

Rabbit Hole is a self-hosted Wikipedia recommender. Once a day it surfaces a small feed of articles
tailored to the user, learned from three signals: which articles they open, how far they scroll, and
explicit 👍/👎 ratings. There is **no database** — all user state lives in a signed cookie plus the
browser's IndexedDB. The server's only real job is embedding inference and scoring; every Wikipedia
API call is made from the user's own browser.

**Stack:** FastAPI + Uvicorn, `sentence-transformers` (`all-MiniLM-L6-v2`), scikit-learn (k-means +
TF-IDF), `itsdangerous` for signed cookies, Jinja2 + vanilla JS on the front end, Nginx + Let's
Encrypt in front.

---

## 2. The central architectural idea: split state, client-side I/O

This is the single most important thing to be able to explain, because almost every other decision
follows from it.

**State is split three ways:**

| Location | Holds | Why there |
|---|---|---|
| Signed `itsdangerous` cookie (httpOnly) | `timezone`, `selected_categories`, `custom_interests`, `reading_history` | The *minimum* the server must trust to score. Signed so the client can't forge ratings. |
| Browser IndexedDB | Full profile, candidate/summary caches, bandit state, per-article feature vectors | Large, client-owned, never needs server trust. |
| In-memory server caches | Popularity rankings, candidate search results | Shared across all users, cheap to rebuild, not per-user. |

**Wikipedia calls happen in the browser, not the server.** The client fetches search results and
article summaries directly from Wikipedia's API using its own network. The server never proxies that
traffic (except the article reader page). This means the server's rate-limit budget doesn't scale
with user count — 1,000 users making Wikipedia calls looks like 1,000 independent clients to
Wikipedia, not one hammering server.

**The `__RH_INIT__` handoff.** The cookie is httpOnly, so JS can't read it. On `GET /`, the server
injects `<script>window.__RH_INIT__ = {...}</script>` with the cookie's contents so the client can
seed IndexedDB on first load (`main.js`). If categories/custom interests changed since last visit,
the client detects the mismatch and nulls `last_generated` to force a feed regeneration.

**Why this design?** The stated principle is "simplicity means reliability." No database means no
migrations, no backups, no connection pool, no persistence layer to operate. The cost is that the
server is *stateless per user* — it can't run a batch recommendation job overnight, and it must
receive the reading history on each scoring call. That trade is deliberate.

---

## 3. Request / data flow

### Feed generation (client-orchestrated — see `feed.js::generateFeed`)

1. `needsReset(profile)` — compares today's date *in the user's timezone* against `last_generated`.
   (This is the JS reimplementation of what used to be a server-side `reset.py`.)
2. `GET /api/seed-queries` — server returns today's search queries per interest group. **Pure
   computation, no Wikipedia I/O.**
3. The browser runs the Wikipedia searches in parallel (seed queries + one search per history
   article + learned TF-IDF queries), IndexedDB-cached 24 h.
4. `bulkSummaries()` fetches extracts for candidates in batches, IndexedDB-cached 6 h.
5. `POST /api/score` — server embeds candidates, builds feature vectors, ranks with the bandit,
   and returns `titles`, `phi_map`, `bandit_state`, and freshly-learned `seed_queries`.
6. Client stores the result in IndexedDB and renders.

### Article read (`routes/pages.py::article_detail`)

- The server **does** proxy the full Wikipedia article HTML here (`fetch_wiki_page`), strips its
  scripts, hides its chrome via injected CSS, rewrites internal links to stay in-app, and injects the
  rating pill. Proxied HTML is cached by title (no user data); the rating pill is injected at serve
  time (user-specific, not cached).
- Reading history is updated in the signed cookie so `/api/score` has fresh data next time.

### Feedback loop

- 👍/👎 → `POST /api/feedback`; scroll milestones → `POST /api/scroll`. Both update the cookie; JS
  mirrors the same change into IndexedDB optimistically.
- On page exit (`pagehide`), the client computes a single reward and calls `POST /api/bandit/update`.
  See §5 for the subtleties.

---

## 4. The recommendation pipeline

### Cold start (< 3 articles read)

`generate_cold_start`: candidates come from daily-rotating seed queries per selected category
(`CATEGORY_SEED_POOL`, 8 queries/category, 2 chosen per day via a date-ordinal rotation). Results are
sorted by pageview popularity and distributed **round-robin** across categories and custom interests
so no single interest dominates the feed.

### Warm user (3+ articles) — taste vectors

`compute_taste_vectors` (in `embeddings.py`) is the heart of the system. It improves on a naive
"average all embeddings" in three ways:

1. **Recency decay** — each history article's weight is multiplied by `0.95^age`, so the vector
   tracks *current* interests rather than everything ever read.
2. **Negative signal** — 1★ articles are embedded separately, their centroid is normalized, and it's
   subtracted (`NEGATIVE_LAMBDA = 0.3`) from every taste vector, pushing recommendations *away* from
   disliked content.
3. **Multi-vector via k-means** — for users with ≥ ~10 signals, positive embeddings are clustered
   (up to `MAX_CLUSTERS = 5`). A user who likes *both* physics and cooking gets one vector per topic
   instead of a muddled midpoint between them. Scoring takes the **max** cosine similarity across
   clusters, so a candidate only needs to match *one* interest.

Per-article weight combines rating (👍=2.0, unrated=1.0, 👎=0.2) with scroll (>75% → +0.3,
<25% → −0.3). Custom interests are permanent anchors embedded at weight 1.5 with no recency decay.

### Bandit scoring — Linear Thompson Sampling (`bandits.py`)

The final ranking isn't a fixed formula; it's a **9-dimensional linear contextual bandit** learned
per user. The feature vector φ for a candidate is:

```
[sim₀, sim₁, sim₂, sim₃, sim₄,   ← cosine sims to the ≤5 taste clusters, sorted desc, zero-padded
 pop_score,                       ← exp(-(rank-1)/150) from daily pageviews
 cat_score,                       ← 1 if the article came from a selected category
 from_history,                    ← 1 if surfaced by a reading-history search
 1.0]                             ← bias
```

Score = θ·φ. The online update on each reward is standard ridge-regression / LinUCB bookkeeping:

```
A ← A + φφᵀ
b ← b + r·φ
θ ← solve(A, b)      # numerically stabler than A⁻¹b
```

**Warm start:** θ is initialized (`_THETA_WARM`) to approximate the old hand-tuned fixed-weight
formula (embed ≈ 0.38 split across cluster slots, pop ≈ 0.24, cat ≈ 0.38). `b = Aθ₀` with `A = λI`
means `A⁻¹b = θ₀` exactly, so a brand-new user gets sensible rankings before any feedback, and the
bandit smoothly personalizes from there. This is the "degrade gracefully" property.

**Rewards** (`reward_from_feedback`): 👍→1.0, 👎→0.0, scroll ≥75% → `0.65 + 0.25·exp(-0.1·(depth-75))`
(peaks ~0.90 at the 75% threshold, decays toward 0.65). Mid-scroll with no rating → `None` = no
update (signal too ambiguous to learn from).

### Learned queries — TF-IDF (`query_gen.py`)

As reading history grows, the seed-query mix **tapers**: category queries drop from 2→1→0 (at
history lengths 4 and 8), custom queries persist until length 10. Filling the gap, `extract_seed_bigrams`
runs TF-IDF over the user's history extracts (with candidate extracts as background IDF corpus when
history is thin), pulls the top bigrams per document weighted by recency×rating, then **reranks them
by cosine similarity to the taste vectors** and drops anti-correlated noise. These learned bigrams
become search queries on the next feed generation — the system discovers a user's actual vocabulary
rather than relying only on the coarse onboarding categories.

---

## 5. Front-end subtleties worth knowing

- **Instant paint, then refresh.** On load, `main.js` renders yesterday's cached articles from
  IndexedDB immediately, *then* regenerates in the background if the day rolled over. The page never
  blocks on network.
- **Reliable bandit update on exit.** IndexedDB writes don't survive `pagehide`. So the client writes
  the pending `(φ, reward, bandit_state)` payload to **`localStorage` synchronously** (which does
  survive unload) *and* fires a `keepalive: true` fetch. Whichever wins: the next feed load applies
  the localStorage payload before scoring; if the keepalive response came back first, it clears the
  localStorage entry to avoid double-applying.
- **High-water reward guard.** Repeated click→back on the same article shouldn't inflate its reward.
  A per-article high-water mark in localStorage means an update only fires when *this* visit produced
  a strictly stronger signal than any prior visit.
- **`FEED_SIZE` mismatch is intentional.** The client requests 12 candidates ranked (`feed.js`) while
  the server default `feed_size` is 8 — the client asks for a slightly larger ranked set than it
  strictly renders. (Worth flagging as a place a reviewer might ask about.)

---

## 6. Operational details

- **Startup (`main.py` lifespan):** loads the embedding model (~10 s), warms the candidate cache from
  disk (`.cache/candidates.json`), warms the popularity cache from Wikimedia pageviews, and launches
  a 12-hour background refresh loop. On shutdown it persists the candidate cache back to disk.
- **Popularity refresh every 12 h** — pageview *data* for a given day never changes, but "yesterday"
  rolls over at midnight; refreshing twice a day guarantees the new day is picked up within 12 h.
- **Static assets served with `Cache-Control: no-cache`** (`NoCacheStaticFiles`) so a changed JS/CSS
  file is picked up immediately (still cheap — StaticFiles returns 304 when unchanged). This prevents
  a stale script running against fresh markup.
- **Wikipedia rate limiting** (`wikipedia.py`): separate semaphores for REST vs Action API, a 0.15 s
  sleep per call, and exponential backoff retry on HTTP 429.
- **Deploy:** systemd unit → Uvicorn on `127.0.0.1:8000`, Nginx reverse proxy, Certbot for TLS.

---

## 7. Testing

- `conftest.py` patches `load_model` and `fetch_top_articles` session-wide so tests never load the
  real model or hit Wikimedia. `respx` mocks Wikipedia HTTP calls. `extract_profile` reads the
  `UserProfile` back out of the `set-cookie` header.
- Fixtures model the lifecycle: `empty_profile`, `cold_profile`, `returning_profile`, `warm_profile`.
- ~1,400 lines of tests across scoring, bandit math, seed-query tapering, onboarding, cookie
  round-tripping, the Wikipedia client, and the article page.

---

## 8. Q&A bank

### System design & architecture

**Q: Why no database?**
The app's whole model is per-user personalization from a small amount of state that the user's own
device can hold. A signed cookie (server-trusted bits) plus IndexedDB (everything else) covers it.
Dropping the DB removes an entire operational surface — migrations, backups, connection management —
in exchange for the server being stateless per user. Given "simplicity means reliability," that's the
right trade for a self-hosted single-node app. The cost: no cross-device sync beyond what the cookie
carries, and no server-side batch jobs.

**Q: Why do Wikipedia calls happen in the browser instead of the server?**
Rate limits. If the server made every Wikipedia call, its shared rate-limit budget would throttle as
users grew. Pushing the I/O to each client makes the traffic look like N independent browsers to
Wikipedia, which is exactly what it is. The server keeps only the work that *needs* the model
(embedding + scoring), which can't run client-side. Downside: we trust the client to send back
candidate extracts, and we can't cache summaries server-side across users (each browser re-fetches).

**Q: How does the httpOnly cookie coexist with client JS that needs the same data?**
The `__RH_INIT__` inline-script handoff. The server serializes the four trusted cookie fields into a
one-time `window.__RH_INIT__` object on `GET /`, and JS seeds IndexedDB from it. This avoids a
second, non-httpOnly cookie (which would be forgeable and readable by any script/XSS). The signed
httpOnly cookie stays the single source of truth for anything the server must trust.

**Q: What stops a user from forging a bunch of 👍 ratings to game recommendations?**
The reading history lives in an `itsdangerous`-signed cookie. The client can *read* it (via the
handoff) but can't produce a validly-signed cookie without the server's `SECRET_KEY`. Ratings only
affect *that user's own* feed anyway, so the incentive is low — but the signing prevents tampering
with what the server treats as trusted input to scoring.

**Q: Where's the concurrency risk with the in-memory caches?**
The popularity and candidate caches are module-level dicts mutated by a background task and read by
request handlers. Python's GIL makes individual dict operations atomic, and the refresh does a whole-
dict *replacement* (`_popularity_cache = result`) rather than in-place mutation, so readers always see
a complete old or new snapshot — no torn reads. It's single-process by design (systemd runs one
Uvicorn); multi-worker would need shared state.

### ML / recommendations

**Q: Why `all-MiniLM-L6-v2` and not a larger embedding model?**
It's small (~22M params, 384-dim), fast enough to run inference synchronously inside a request on
CPU, and good enough for short encyclopedia extracts. The app self-hosts on a laptop-class machine;
a larger model would blow the latency budget for the interactive `/api/score` call. Embeddings are
normalized so cosine similarity is a plain dot product.

**Q: Why multi-vector taste (k-means) instead of a single averaged vector?**
Averaging collapses genuinely diverse interests into a meaningless midpoint — the average of
"quantum physics" and "medieval cooking" recommends neither well. Clustering keeps one vector per
topic, and scoring by the **max** similarity across clusters means a candidate is rewarded for
matching *any one* interest. It only kicks in with enough signal (`total_seen // MIN_CLUSTER_SIZE`,
capped at 5); below that it's a single weighted mean.

**Q: How does the negative signal work, and why subtract a centroid rather than just down-weight?**
Down-weighting a 1★ article (weight 0.2) just makes it contribute *less* to the positive vector — it
can't push *away* from a disliked topic. Instead, 1★ articles are pooled into a separate normalized
centroid and subtracted (`v - 0.3·neg_unit`, then re-normalized) from every taste vector. That
actively steers candidates away from the disliked region of embedding space.

**Q: Why a contextual bandit on top of embeddings? Isn't cosine similarity enough?**
Cosine similarity to the taste vector is *one* feature. But how much should popularity matter vs.
topical match vs. category membership? Those weights differ per user and change as history grows. The
bandit *learns* θ (the weighting) from each user's rewards instead of hard-coding it. The warm-start
θ reproduces the old fixed formula, so we lose nothing on day one and gain personalization over time.

**Q: Why Thompson Sampling / this LinUCB-style update specifically?**
It's the simplest online linear method that (a) has a closed-form incremental update — just
`A += φφᵀ`, `b += r·φ`, resolve θ — with no gradient steps or training loop, (b) naturally balances
exploration/exploitation via the posterior covariance `A⁻¹`, and (c) serializes to a tiny JSON blob
(θ, flattened A, b) that lives in the client's IndexedDB. It fits the "no training infra, no DB"
constraint exactly. `np.linalg.solve(A, b)` is used instead of forming `A⁻¹` for numerical stability.

**Q: What's the reward design, and why is mid-scroll "no update"?**
👍/👎 are unambiguous (1.0/0.0). Scroll ≥75% is a strong implicit positive (reward peaks ~0.90 at 75%
and decays toward 0.65 — later percent matters less). But a 30–70% scroll is genuinely ambiguous:
maybe they skimmed, maybe they bounced. Feeding that as a middling reward would inject noise into θ,
so it returns `None` and no update happens. Better to learn from clear signals only.

**Q: How does the system avoid recommending the same articles repeatedly?**
`seen_articles` (permanently excluded) plus a 42-article `shown_articles` sliding window that keeps
recently-cycled articles from immediately reappearing. Both are computed client-side and folded into
the `seen` set before candidate allocation in `feed.js`.

**Q: Walk me through the cold-start → warm-user transition.**
Under 3 history articles: pure popularity-sorted round-robin over category seed queries. At 3+, taste
vectors + bandit scoring turn on. Meanwhile seed queries taper (2→1→0 category queries as history
hits 4 and 8) while TF-IDF-learned bigram queries take over, so the candidate *source* also shifts
from broad category seeds to the user's learned vocabulary. Both the scoring and the candidate
sourcing personalize in lockstep.

### Backend / FastAPI

**Q: How is the embedding model loaded and shared?**
Once, in the lifespan startup, into a module-global (`_model`). Requests share the single instance.
Encoding is synchronous NumPy work inside the async handler — acceptable because the model is small
and this is single-process; a heavier model would warrant a thread pool or a separate inference
service.

**Q: The article reader proxies Wikipedia HTML — what are the risks and how are they handled?**
Scripts are stripped (`<script>` removed) so Wikipedia's JS can't phone home or run in our origin.
Internal `/wiki/` links are rewritten to `/article/...?from=...` to stay in-app and preserve the
source chain. Protocol- and root-relative URLs are absolutized so CSS/images still load. The main
residual risk is that we're serving third-party HTML from our origin — mitigated by script removal,
but a stricter CSP would be the hardening step.

**Q: Why re-issue the cookie on every home visit?**
To keep the TTL rolling (`cookie_max_age` is a year, but each visit refreshes it) and to persist any
reading-history changes made server-side. It's cheap — just a signed-serialize.

### Front-end

**Q: How do you guarantee a bandit update survives the user navigating away?**
Three layers. (1) A synchronous `localStorage.setItem` in the `pagehide` handler — synchronous writes
survive unload where async IndexedDB writes don't. (2) A `keepalive: true` fetch that the browser
lets outlive the page. (3) Idempotency: the next feed load applies the localStorage payload before
scoring, and if the keepalive response already landed it clears the entry — so it's applied exactly
once regardless of which path wins.

**Q: Why compute the daily reset in JS instead of the server?**
The reset depends on the user's *local* date (`Intl.DateTimeFormat('en-CA', {timeZone})`), and the
whole feed pipeline is already client-orchestrated. Doing it server-side would mean round-tripping
the timezone and duplicating the date logic. It's the JS equivalent of the retired `reset.py`.

### Trade-offs & "what would you change"

**Q: Biggest weaknesses of this design?**
- **Cold summary cache per client** — no cross-user summary sharing means every browser re-fetches
  the same popular articles. A shared server-side summary cache would cut Wikipedia traffic but
  reintroduce the rate-limit coupling we deliberately removed.
- **Single process / single node** — module-global caches and one model instance don't scale
  horizontally without shared state (Redis, etc.).
- **Trusting client-sent extracts** at scoring time — a malicious client could send junk extracts,
  though only to poison its own feed.
- **Cookie size** — reading history grows unbounded in the cookie; eventually it'll hit the ~4KB
  cookie limit and need truncation or a move to a server store.

**Q: The client requests `FEED_SIZE = 12` but the server default is 8 — is that a bug?**
It's a deliberate over-fetch: the client asks the server to rank a slightly larger set than it needs
so it has headroom after filtering out summaries that come back empty. It's the kind of small
inconsistency worth calling out and confirming the intent of — a good honest answer in an interview.

**Q: How would you evaluate whether the recommendations are actually good?**
Offline: replay logged reading histories and measure whether held-out 👍 articles rank highly
(precision@k / MAP against the user's own future ratings). Online: A/B the bandit against the
fixed-weight baseline on engagement (open rate, scroll-depth distribution, 👍 rate). The bandit's
warm-start makes this clean — the control is literally the bandit's day-zero θ.

---

## 9. Things to be able to draw on a whiteboard

1. **The state-split triangle** — cookie (trusted, small) / IndexedDB (everything) / server memory
   (shared caches), and which reads/writes touch which.
2. **The feed sequence** — `needsReset` → seed-queries → browser Wikipedia searches → `/api/score`
   → render → persist, noting where I/O happens (browser) vs. compute (server).
3. **φ and the bandit update** — the 9-dim vector, `θ·φ` scoring, and the three-line online update.
4. **The taste-vector pipeline** — embed history → recency-weight → k-means clusters → subtract
   negative centroid → max-similarity scoring.
