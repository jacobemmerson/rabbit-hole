# Rabbit Hole

A self-hosted Wikipedia recommendation app. Each day it surfaces 8 articles tailored to your interests, inferred from reading history, scroll depth, and thumbs ratings. All user state lives in a signed cookie — no database required.

---

## Tech stack

| Layer | Technology |
|---|---|
| Backend | Python, FastAPI, Uvicorn |
| Recommendations | Sentence Transformers (`all-MiniLM-L6-v2`), scikit-learn |
| Data | Wikipedia REST API + MediaWiki Action API |
| User state | `itsdangerous` signed cookies |
| Frontend | Jinja2 templates, vanilla JS |
| Reverse proxy | Nginx + Let's Encrypt |

---

## Network setup

**Prerequisites:** [uv](https://docs.astral.sh/uv/getting-started/installation/)

```bash
git clone <repo-url> && cd rabbit-hole

# Install dependencies
uv sync

# Configure environment
cp .env.example .env
# Edit .env — set SECRET_KEY to a long random string
```

Generate a secret key:
```bash
python3 -c "import secrets; print(secrets.token_hex(32))"
```

Start the server, bound to all interfaces:
```bash
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Find the machine's local IP and open `http://<local-ip>:8000` from any device on the same network:
```bash
ip route get 1 | awk '{print $7; exit}'   # Linux
```

The embedding model loads on first startup (~10 seconds).

### Environment variables

| Variable | Default | Description |
|---|---|---|
| `SECRET_KEY` | *(required)* | Signs cookies — change before deploying |
| `DEBUG` | `false` | Enables FastAPI debug mode |
| `REGION` | *(empty)* | ISO 3166-1 alpha-2 code (e.g. `US`) for regional Top Daily |

---

## Self-hosting with systemd + Nginx

### 1. systemd service

Create `/etc/systemd/system/rabbit-hole.service`:

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

Enable and start:
```bash
sudo systemctl daemon-reload
sudo systemctl enable rabbit-hole
sudo systemctl start rabbit-hole
sudo systemctl status rabbit-hole
```

### 2. Nginx reverse proxy

Create `/etc/nginx/sites-available/rabbit-hole`:

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
sudo nginx -t
sudo systemctl reload nginx
```

### 3. HTTPS certificate

```bash
sudo certbot --nginx -d yourdomain.com
```

Certbot auto-renews via a systemd timer or cron job installed alongside it.

---

## Recommendation system

### Cold start (fewer than 3 articles read)
Candidates come from Wikipedia full-text search using daily-rotating seed queries for each selected category. Results are sorted by pageview popularity and distributed round-robin across categories.

### Warm users (3+ articles read)
Each article in reading history is embedded with `all-MiniLM-L6-v2` and averaged into a taste vector, weighted by:
- Star rating: unrated=1.0, 👎=0.2, 👍=2.0
- Scroll depth: >75% adds +0.3, <25% subtracts 0.3

Candidates are scored by cosine similarity to the taste vector, blended with category relevance and pageview popularity. The embedding signal reaches full weight at 20 articles read.

### Daily reset
On each visit the server compares today's date in the user's stored timezone against `last_generated`. If different, the feed regenerates via JS auto-refresh so the page loads instantly with the previous day's articles while the new set fetches in the background.

---

## Cookie structure

```json
{
  "timezone": "America/New_York",
  "last_generated": "2026-05-11",
  "selected_categories": ["Science & Mathematics", "History"],
  "current_articles": ["Quantum_mechanics", "Roman_Empire"],
  "seen_articles": ["Photosynthesis", "Napoleon"],
  "shown_articles": ["..."],
  "reading_history": [
    { "title": "Quantum_mechanics", "rating": 5, "scroll_depth": 82, "source": null },
    { "title": "Photosynthesis",    "rating": null, "scroll_depth": 30, "source": null }
  ]
}
```

`seen_articles` are permanently excluded from future recommendations. `shown_articles` is a 42-article sliding window that prevents recently-cycled articles from immediately reappearing.
