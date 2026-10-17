# Rabbit Hole

A self-hosted Wikipedia recommender. Each day it shows 12 articles picked for the reader, and it
learns from what they open, how far they scroll, and thumbs up or down. There is no server-side
database: the server keeps a small signed cookie, and everything else lives in the browser's
IndexedDB.

## How ranking works

The baseline is ranking by popularity within the reader's chosen categories. Rabbit Hole adds two
things on top of that.

1. **Taste vectors.** The reader's history is embedded with `all-MiniLM-L6-v2` and clustered with
   k-means into up to 5 taste vectors (one cluster per 5 articles read). Embeddings are weighted by
   rating (👍 2.0, 👎 0.2, unrated 1.0), scroll depth (+0.3 above 75%, -0.3 below 25%) and recency
   (x0.95 per article back). The centroid of disliked articles is subtracted from every cluster.
   A candidate only needs to be close to one cluster to score well (`app/services/embeddings.py`).
2. **Per-reader learned weights.** Each candidate gets a 9-feature vector: its cosine similarity
   to each taste vector (sorted, padded to 5), popularity, selected-category flag, came-from-history
   flag, and a bias term. The score is a linear model over those features. The weights start at a
   hand-set prior (popularity 0.24, category 0.38, similarity 0.38 split across clusters) and are
   refit online after each piece of feedback with a ridge-regression update (`A += φφᵀ`,
   `b += rφ`, `θ = A⁻¹b`). The reward is 1 for 👍, 0 for 👎, and 0.65-0.90 for scrolling past 75%
   without a rating (`app/services/bandits.py`).

Ranking uses the current weight estimate. It does not sample from the posterior, so there is no
exploration step; this is online linear regression, not Thompson sampling.

Candidates come from Wikipedia full-text search. Seed queries rotate daily per category, and
TF-IDF queries learned from reading history take over as history grows
(`app/services/query_gen.py`). Read articles are never shown again, and a sliding window keeps
recently shown ones from reappearing.

## Request flow

1. The browser calls `/api/seed-queries`, searches Wikipedia directly (MediaWiki Action API), and
   collects up to 36 candidates with extracts.
2. It posts the candidates, reading-history extracts and its stored weights to `/api/score`. The
   server embeds them, builds the feature vectors, and returns the ranked titles, each candidate's
   feature vector, and the weights.
3. Feedback (`/api/feedback`, `/api/scroll`) updates the cookie. `/api/bandit/update` returns new
   weights, which the browser stores in IndexedDB.
4. When the date changes in the reader's timezone, the browser shows yesterday's feed and builds the
   new one in the background.

| where | holds |
|---|---|
| signed cookie (`itsdangerous`) | timezone, selected categories, custom interests, reading history (title, rating, scroll depth) |
| IndexedDB | full profile, candidate and summary caches, learned weights, per-article feature vectors, learned queries |

## Stack

Python, FastAPI, Uvicorn, Sentence Transformers, scikit-learn, httpx, Jinja2 templates, vanilla JS.
Wikipedia REST API, MediaWiki Action API, and Wikimedia pageviews API.

## Run locally

```bash
uv venv && uv pip install -r requirements.txt
cp .env.example .env
python3 -c "import secrets; print(secrets.token_hex(32))"   # paste into SECRET_KEY in .env
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Open `http://localhost:8000`, or `http://<machine-ip>:8000` from another device on the network. The
embedding model loads at startup (about 10 seconds).

Tests (96, Wikipedia calls mocked with `respx`):

```bash
uv run pytest
```

| variable | default | purpose |
|---|---|---|
| `SECRET_KEY` | required | signs the cookie |
| `DEBUG` | `false` | FastAPI debug mode |
| `REGION` | empty | ISO 3166-1 alpha-2 code (e.g. `US`) for regional top articles; empty uses global English Wikipedia |

## Deploy with systemd and Nginx

`/etc/systemd/system/rabbit-hole.service`:

```ini
[Unit]
Description=Rabbit Hole
After=network.target

[Service]
Type=simple
User=<your-user>
WorkingDirectory=/path/to/rabbit-hole
ExecStart=/path/to/rabbit-hole/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload && sudo systemctl enable --now rabbit-hole
```

`/etc/nginx/sites-available/rabbit-hole`:

```nginx
server {
    listen 80;
    server_name yourdomain.com;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl;
    server_name yourdomain.com;

    ssl_certificate     /etc/letsencrypt/live/yourdomain.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/yourdomain.com/privkey.pem;

    location / {
        proxy_pass         http://127.0.0.1:8000;
        proxy_set_header   Host $host;
        proxy_set_header   X-Real-IP $remote_addr;
        proxy_set_header   X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
    }
}
```

```bash
sudo ln -s /etc/nginx/sites-available/rabbit-hole /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
sudo certbot --nginx -d yourdomain.com   # HTTPS; certbot installs its own renewal timer
```

`INTERVIEW.md` has a longer walkthrough of the architecture.
