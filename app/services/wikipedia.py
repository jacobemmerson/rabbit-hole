import asyncio
import json
import logging
import re
from datetime import datetime, timedelta
from pathlib import Path

import httpx

from app.config import settings
from app.models import ArticleSummary

logger = logging.getLogger(__name__)

_client: httpx.AsyncClient | None = None

# Separate semaphores for the two Wikipedia APIs — they have independent rate limits.
# REST API (summaries) is more lenient; action API (category/search) is stricter.
_rest_sem: asyncio.Semaphore | None = None   # for /api/rest_v1/ calls
_action_sem: asyncio.Semaphore | None = None  # for /w/api.php calls

# Summaries: cache for 6 hours (article text rarely changes within a session)
_summary_cache: dict[str, tuple[ArticleSummary | None, datetime]] = {}

# Full article HTML: cache for 6 hours (value is str | tuple[str,str] | None)
_html_cache: dict[str, tuple[object, datetime]] = {}
_HTML_TTL = timedelta(hours=6)
_SUMMARY_TTL = timedelta(hours=6)

# Candidate lists: cache for 24 hours (category membership is very stable)
_candidate_cache: dict[str, tuple[list[str], datetime]] = {}
_CANDIDATE_TTL = timedelta(hours=24)

# Popularity rankings: cache for 7 days (monthly pageview data doesn't change)
_popularity_cache: dict[str, int] = {}        # {canonical_title: rank}
_popularity_cached_at: datetime | None = None
_POPULARITY_TTL = timedelta(days=7)

# Pages to exclude from popularity rankings (non-article Wikipedia pages)
_SKIP_PREFIXES = (
    "Main Page", "Special:", "Wikipedia:", "Help:", "File:",
    "Portal:", "Talk:", "User:", "Template:", "Category:",
)

_WIKIMEDIA_PAGEVIEWS_URL = "https://wikimedia.org/api/rest_v1/metrics/pageviews/top/en.wikipedia/all-access"


def init_semaphore() -> None:
    global _rest_sem, _action_sem
    _rest_sem = asyncio.Semaphore(2)    # allow 2 concurrent summary fetches
    _action_sem = asyncio.Semaphore(2)  # allow 2 concurrent category/search calls


async def _rest_get(url: str, **kwargs) -> httpx.Response:
    """Rate-limited GET for the Wikipedia REST API with retry on 429."""
    for attempt in range(5):
        async with _rest_sem:
            await asyncio.sleep(0.15)
            resp = await _client.get(url, **kwargs)
        if resp.status_code != 429:
            return resp
        wait = 2 ** attempt
        logger.warning("REST rate limited, retrying in %ds (attempt %d/5)", wait, attempt + 1)
        await asyncio.sleep(wait)
    return resp


async def _action_get(url: str, **kwargs) -> httpx.Response:
    """Rate-limited GET for the Wikipedia action API with retry on 429."""
    for attempt in range(5):
        async with _action_sem:
            await asyncio.sleep(0.15)
            resp = await _client.get(url, **kwargs)
        if resp.status_code != 429:
            return resp
        wait = 2 ** attempt
        logger.warning("Action API rate limited, retrying in %ds (attempt %d/5)", wait, attempt + 1)
        await asyncio.sleep(wait)
    return resp


async def fetch_summary(title: str) -> ArticleSummary | None:
    cached = _summary_cache.get(title)
    if cached is not None:
        result, cached_at = cached
        if datetime.now() - cached_at < _SUMMARY_TTL:
            return result

    try:
        resp = await _rest_get(f"{settings.wikipedia_base_url}/page/summary/{title}")
        if resp.status_code == 404:
            _summary_cache[title] = (None, datetime.now())
            return None
        resp.raise_for_status()
        data = resp.json()
        article = ArticleSummary(
            title=data.get("title", title),
            extract=data.get("extract", ""),
            page_url=data.get("content_urls", {}).get("desktop", {}).get("page", ""),
            thumbnail_url=data.get("thumbnail", {}).get("source") if data.get("thumbnail") else None,
            page_type=data.get("type", "standard"),
        )
        _summary_cache[title] = (article, datetime.now())
        return article
    except (httpx.HTTPError, httpx.TimeoutException, Exception) as e:
        logger.warning("fetch_summary failed for %s: %s", title, e)
        return None


async def fetch_category_members(category_name: str, limit: int = 20) -> list[str]:
    """Return article titles that are members of the given Wikipedia category.
    Results are cached for 24 hours — category membership rarely changes.
    """
    if not category_name.startswith("Category:"):
        category_name = f"Category:{category_name}"

    cached = _candidate_cache.get(category_name)
    if cached is not None:
        result, cached_at = cached
        if datetime.now() - cached_at < _CANDIDATE_TTL:
            return result

    params = {
        "action": "query",
        "list": "categorymembers",
        "cmtitle": category_name,
        "cmlimit": limit,
        "cmnamespace": 0,
        "format": "json",
    }
    try:
        resp = await _action_get(settings.wikipedia_action_url, params=params)
        resp.raise_for_status()
        data = resp.json()
        titles = [m["title"] for m in data.get("query", {}).get("categorymembers", [])]
        _candidate_cache[category_name] = (titles, datetime.now())
        return titles
    except (httpx.HTTPError, httpx.TimeoutException, Exception) as e:
        logger.warning("fetch_category_members failed for %s: %s", category_name, e)
        return []


async def fetch_search(query: str, limit: int = 10) -> list[str]:
    """Full-text search for articles related to a query string.
    Results are cached for 24 hours.
    """
    cache_key = f"search:{query}"
    cached = _candidate_cache.get(cache_key)
    if cached is not None:
        result, cached_at = cached
        if datetime.now() - cached_at < _CANDIDATE_TTL:
            return result

    params = {
        "action": "query",
        "list": "search",
        "srsearch": query,
        "srlimit": limit,
        "srnamespace": 0,
        "format": "json",
    }
    try:
        resp = await _action_get(settings.wikipedia_action_url, params=params)
        resp.raise_for_status()
        data = resp.json()
        titles = [r["title"] for r in data.get("query", {}).get("search", [])]
        _candidate_cache[cache_key] = (titles, datetime.now())
        return titles
    except (httpx.HTTPError, httpx.TimeoutException, Exception) as e:
        logger.warning("fetch_search failed for %s: %s", query, e)
        return []


_HIDE_NAV_CSS = """<style id="rh-chrome">
/* Hide Wikipedia navigation — keep only article content */
.vector-header,.mw-header,#mw-navigation,.vector-column-start,
#mw-panel,.vector-sidebar-container,.vector-column-end,
.vector-toc-landmark,.vector-page-toolbar,.vector-sticky-header,
.vector-site-notice,#p-cactions,.mw-portlet-lang,
.catlinks,#footer,.mw-jump-link,
.minerva-header,#mw-mf-page-left { display:none !important; }

/* Remove sidebar margin offsets */
.mw-page-container,.mw-body,.mw-content-container,
#content,.vector-body { margin:0 !important; max-width:none !important; }

/* Space for our fixed header */
html { scroll-padding-top:52px; }
body { padding-top:52px !important; }
</style>"""


def _process_wiki_page(html: str, from_title: str) -> str:
    """Keep the full Wikipedia HTML document but rewrite links and make URLs absolute.
    The full document structure is preserved so all CSS selectors keep working.
    Cached by title — does not contain user-specific data (stars rating).
    """
    import re
    from urllib.parse import quote

    # Remove scripts — Wikipedia's JS tries to contact its own servers
    html = re.sub(r'<script\b[^>]*>.*?</script>', '', html, flags=re.DOTALL | re.IGNORECASE)

    # Rewrite /wiki/ article links to stay in the app
    html = re.sub(
        r'href="/wiki/([^"#?]*)',
        lambda m: f'href="/article/{m.group(1)}?from={quote(from_title)}"',
        html,
    )

    # Make protocol-relative URLs absolute
    html = re.sub(r'(src|href|action)="//', r'\1="https://', html)

    # Make root-relative URLs absolute (CSS bundles, images, API calls)
    html = re.sub(
        r'(src|href)="(/(?!article|api|static)[^"]*)"',
        lambda m: f'{m.group(1)}="https://en.wikipedia.org{m.group(2)}"',
        html,
    )

    # Inject our hide-nav CSS right after <head>
    html = re.sub(r'(<head\b[^>]*>)', r'\1' + _HIDE_NAV_CSS, html, count=1, flags=re.IGNORECASE)

    return html


_THUMBS = [("👍", 5), ("👎", 1)]


def inject_article_chrome(html: str, page_title: str, current_rating: int | None) -> str:
    """Inject our app header, rating pill, and main.js into the processed page.
    Called at serve time — not cached — because it contains the user's rating.
    """
    header = (
        '<div id="rh-header" style="position:fixed;top:0;left:0;right:0;z-index:99999;'
        'background:#f8f9fa;border-bottom:1px solid #a2a9b1;padding:.4rem 1.25rem;'
        'display:flex;align-items:center;gap:.75rem;font-family:system-ui,sans-serif;font-size:.875rem;">'
        '<a href="/" style="color:#3366cc;text-decoration:none;flex-shrink:0;">← Back to feed</a>'
        f'<span style="color:#54595d;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1;min-width:0;">{page_title}</span>'
        '</div>'
        f'<div id="article-meta" data-title="{page_title}" style="display:none;"></div>'
    )

    thumbs_inner = ''.join(
        f'<span class="thumb" data-rating="{val}" '
        f'style="font-size:1.6rem;cursor:pointer;line-height:1;flex-shrink:0;user-select:none;'
        f'transition:opacity .15s,transform .15s;opacity:{"1" if current_rating == val else "0.35"};">'
        f'{emoji}</span>'
        for emoji, val in _THUMBS
    )
    indicator_content = {5: '👍', 1: '👎'}.get(current_rating, '')
    pill_css = (
        '<style>'
        '#thumbs-widget{'
        'position:fixed;bottom:1.75rem;left:50%;'
        'transform:translateX(-50%) translateY(14px);'
        'display:flex;align-items:center;gap:.6rem;overflow:hidden;cursor:default;'
        'background:rgba(255,255,255,0.97);border:1.5px solid #e5e7eb;'
        'border-radius:100px;padding:.6rem 1rem .6rem 1.4rem;'
        'box-shadow:0 4px 24px rgba(0,0,0,.12);'
        'opacity:0;pointer-events:none;'
        'transition:opacity .35s ease,transform .35s ease,bottom .3s ease,padding .3s ease,gap .3s ease;'
        'z-index:99998;}'
        '#thumbs-widget.visible{opacity:1;pointer-events:auto;transform:translateX(-50%) translateY(0);}'
        '#thumbs-widget.minimized{bottom:.6rem;padding:.35rem .85rem .35rem .95rem;gap:.3rem;cursor:pointer;}'
        '.pill-rating{display:flex;gap:.75rem;max-width:5rem;overflow:hidden;'
        'transition:max-width .3s ease,opacity .2s ease;}'
        '#thumbs-widget.minimized .pill-rating{max-width:0;opacity:0;pointer-events:none;}'
        '#thumbs-widget .thumb:hover{transform:scale(1.2);}'
        '.pill-indicator{font-size:1.3rem;line-height:1;max-width:0;overflow:hidden;opacity:0;'
        'flex-shrink:0;user-select:none;white-space:nowrap;'
        'transition:max-width .3s ease,opacity .2s ease .1s;}'
        '#thumbs-widget.minimized .pill-indicator{max-width:2rem;opacity:1;}'
        '.pill-toggle{background:none;border:none;cursor:pointer;color:#9ca3af;'
        'padding:.1rem .15rem;font-size:1rem;line-height:1;flex-shrink:0;'
        'border-radius:4px;transition:color .15s,transform .3s ease;}'
        '.pill-toggle:hover{color:#374151;}'
        '#thumbs-widget.minimized .pill-toggle{transform:rotate(180deg);}'
        '@keyframes rh-pill-pulse{'
        '0%,100%{box-shadow:0 4px 24px rgba(0,0,0,.12);}'
        '50%{box-shadow:0 4px 32px rgba(37,99,235,.4);}}'
        '#thumbs-widget.pulse{animation:rh-pill-pulse .4s ease;}'
        '</style>'
    )
    pill = (
        f'<div id="thumbs-widget" data-saved="{current_rating or ""}">'
        f'<div class="pill-rating">{thumbs_inner}</div>'
        f'<span class="pill-indicator">{indicator_content}</span>'
        f'<button class="pill-toggle" aria-label="Minimize rating">▾</button>'
        f'</div>'
    )

    rating_hint = (
        '<div id="rh-rating-hint" style="display:none;position:fixed;top:0;left:0;right:0;bottom:0;'
        'z-index:999999;background:rgba(0,0,0,0.45);align-items:center;justify-content:center;padding:1rem;">'
        '<div style="background:#fff;border-radius:16px;padding:1.75rem 2rem;max-width:320px;width:100%;'
        'box-shadow:0 20px 60px rgba(0,0,0,0.25);font-family:system-ui,sans-serif;text-align:center;">'
        '<div style="font-size:2rem;margin-bottom:.75rem;letter-spacing:.15em;">👍 👎</div>'
        '<p style="font-size:.95rem;font-weight:600;color:#111;margin-bottom:.4rem;">Rate what you read</p>'
        '<p style="font-size:.82rem;color:#6b7280;line-height:1.55;margin-bottom:1.25rem;">'
        'Use the pill at the bottom to rate articles — your ratings help us find better recommendations.'
        '</p>'
        '<button id="rh-hint-dismiss" style="background:#2563eb;color:#fff;border:none;border-radius:8px;'
        'padding:.55rem 1.25rem;font-size:.875rem;font-weight:600;cursor:pointer;'
        'font-family:system-ui,sans-serif;">Got it</button>'
        '</div></div>'
    )

    # Inject header + hint right after <body>
    html = re.sub(r'(<body\b[^>]*>)', r'\1' + header + rating_hint, html, count=1, flags=re.IGNORECASE)
    # Inject pill CSS + pill element + our JS before </body>
    html = html.replace(
        '</body>',
        pill_css + pill + '<script type="module" src="/static/main.js"></script>\n</body>',
        1,
    )
    return html


async def fetch_wiki_page(title: str) -> str | None:
    """Proxy the full Wikipedia article page (document structure intact, nav hidden via CSS).
    Returns processed HTML without user-specific data — safe to cache by title.
    """
    cache_key = f"proxy:{title}"
    cached = _html_cache.get(cache_key)
    if cached is not None:
        result, cached_at = cached
        if datetime.now() - cached_at < _HTML_TTL:
            return result  # type: ignore[return-value]

    url = f"https://en.wikipedia.org/wiki/{title}"
    try:
        async with _rest_sem:
            await asyncio.sleep(0.15)
            resp = await _client.get(url, follow_redirects=True)
        if resp.status_code == 404:
            _html_cache[cache_key] = (None, datetime.now())
            return None
        resp.raise_for_status()
        result = _process_wiki_page(resp.text, title)
        _html_cache[cache_key] = (result, datetime.now())
        return result
    except (httpx.HTTPError, httpx.TimeoutException, Exception) as e:
        logger.warning("fetch_wiki_page failed for %s: %s", title, e)
        return None


def get_popularity() -> dict[str, int]:
    """Return the in-memory popularity cache synchronously.
    Populated at startup and refreshed daily by a background task.
    Safe to call from any async context — no I/O.
    """
    return _popularity_cache


# Per-article view count cache: {title: monthly_views}, TTL same as candidates
_views_cache: dict[str, tuple[int, datetime]] = {}


async def fetch_article_views(title: str) -> int:
    """Return the monthly pageview count for a single article.
    Used to score candidates that fall outside the top-500 popularity list.
    Returns 0 on any error.
    """
    cached = _views_cache.get(title)
    if cached is not None:
        views, cached_at = cached
        if datetime.now() - cached_at < _CANDIDATE_TTL:
            return views

    import calendar
    now = datetime.now()
    month = now.month - 1 or 12
    year = now.year if now.month > 1 else now.year - 1
    last_day = calendar.monthrange(year, month)[1]
    start = f"{year}{month:02d}01"
    end = f"{year}{month:02d}{last_day:02d}"

    slug = title.replace(" ", "_")
    url = (
        f"https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article"
        f"/en.wikipedia/all-access/all-agents/{slug}/monthly/{start}/{end}"
    )

    try:
        async with _rest_sem:
            await asyncio.sleep(0.15)
            resp = await _client.get(url)
        if resp.status_code in (404, 400):
            _views_cache[title] = (0, datetime.now())
            return 0
        resp.raise_for_status()
        items = resp.json().get("items", [])
        views = sum(item.get("views", 0) for item in items)
        _views_cache[title] = (views, datetime.now())
        return views
    except (httpx.HTTPError, httpx.TimeoutException, Exception) as e:
        logger.warning("fetch_article_views failed for %s: %s", title, e)
        return 0


async def fetch_top_articles(limit: int = 500) -> None:
    """Fetch the top Wikipedia articles by pageviews for yesterday and update the cache.
    Uses the daily endpoint so Top Daily reflects what people actually read today.
    Yesterday's data is always complete; today's may still be accumulating.
    Called once at startup and then every 12 hours by a background task in main.py.
    Result is stored in _popularity_cache as {canonical_title: rank}.
    """
    global _popularity_cache, _popularity_cached_at

    yesterday = datetime.now() - timedelta(days=1)
    year, month, day = yesterday.year, yesterday.month, yesterday.day

    if settings.region:
        # Per-country top articles: what people in that country viewed on English Wikipedia
        url = (
            f"https://wikimedia.org/api/rest_v1/metrics/pageviews/top-per-country"
            f"/{settings.region.upper()}/all-access/{year}/{month:02d}/{day:02d}"
        )
    else:
        url = f"{_WIKIMEDIA_PAGEVIEWS_URL}/{year}/{month:02d}/{day:02d}"
    try:
        async with _rest_sem:
            await asyncio.sleep(0.15)
            resp = await _client.get(url)
        resp.raise_for_status()
        articles = resp.json()["items"][0]["articles"]

        result: dict[str, int] = {}
        rank = 1
        for item in articles:
            title = item["article"].replace("_", " ")
            if any(title.startswith(p) for p in _SKIP_PREFIXES):
                continue
            # Per-country data spans all Wikipedia language editions.
            # Non-ASCII titles (Japanese, Arabic, etc.) and namespace:title
            # patterns from other-language Wikis (Especial:Pesquisar,
            # Istimewa:Pencarian, Wikidata:Main Page, etc.) are filtered here;
            # any remaining non-English articles naturally return 404 when we
            # try to fetch their English Wikipedia summary.
            if settings.region:
                try:
                    title.encode("ascii")
                except UnicodeEncodeError:
                    continue
                if ":" in title:   # namespace:title pattern → not an article
                    continue
            result[title] = rank
            rank += 1
            if rank > limit:
                break

        _popularity_cache = result
        _popularity_cached_at = datetime.now()
        logger.info("Popularity cache updated: %d articles (rank 1: %s)", len(result), next(iter(result), "—"))
    except (httpx.HTTPError, httpx.TimeoutException, Exception) as e:
        logger.warning("fetch_top_articles failed: %s — keeping stale cache (%d articles)", e, len(_popularity_cache))


async def fetch_summaries_bulk(titles: list[str]) -> list[ArticleSummary]:
    """Fetch summaries for multiple articles using the MediaWiki batch query API.

    Uses action=query with up to 50 titles per request instead of one REST call
    per article, reducing 40 REST calls to 1 action API call and avoiding the
    rate limiting that occurs when refreshing warm-user feeds.

    Falls back to individual REST fetches for any titles that come back missing.
    Single-article fetches (article detail page) still use fetch_summary() directly.
    """
    if not titles:
        return []

    results: list[ArticleSummary] = []
    uncached: list[str] = []

    for title in titles:
        cached = _summary_cache.get(title)
        if cached is not None:
            article, cached_at = cached
            if datetime.now() - cached_at < _SUMMARY_TTL:
                if article is None or article.page_type == "disambiguation":
                    continue  # genuinely missing/disambiguation — skip
                if article.extract:
                    results.append(article)
                    continue  # good cache hit with content
                # empty extract within TTL — fall through to re-fetch so the
                # two-stage fallback gets another chance to fill it in
        uncached.append(title)

    # Batch uncached titles — action API allows up to 50 titles per request
    for i in range(0, len(uncached), 50):
        batch = uncached[i : i + 50]
        params = {
            "action": "query",
            "titles": "|".join(batch),
            "prop": "extracts|pageimages|pageprops|info",
            "exintro": "1",
            "explaintext": "1",
            "pithumbsize": "200",
            "inprop": "url",
            "redirects": "1",
            "format": "json",
        }
        try:
            resp = await _action_get(settings.wikipedia_action_url, params=params)
            resp.raise_for_status()
            pages = resp.json().get("query", {}).get("pages", {})
            batch_articles: list[ArticleSummary] = []
            for page in pages.values():
                if "missing" in page:
                    continue
                page_title = page.get("title", "")
                page_type = (
                    "disambiguation"
                    if "disambiguation" in page.get("pageprops", {})
                    else "standard"
                )
                article = ArticleSummary(
                    title=page_title,
                    extract=page.get("extract", "").strip(),
                    page_url=page.get("fullurl", f"https://en.wikipedia.org/wiki/{page_title.replace(' ', '_')}"),
                    thumbnail_url=page.get("thumbnail", {}).get("source") if page.get("thumbnail") else None,
                    page_type=page_type,
                )
                # Only cache if the extract is non-empty — caching an empty
                # extract here would poison the cache and prevent the fallback
                # from ever running again for this article on subsequent calls.
                if article.extract or page_type == "disambiguation":
                    _summary_cache[page_title] = (article, datetime.now())
                if page_type != "disambiguation":
                    batch_articles.append(article)

            # For any article that still has no extract, issue a second action
            # API batch call using sentence-based extraction instead of intro-only.
            # This is ONE extra call (via _action_sem) instead of N individual
            # REST calls, which avoids the rate-limiting the REST fallback caused.
            empty = [a for a in batch_articles if not a.extract]
            if empty:
                try:
                    fb_params = {
                        "action": "query",
                        "titles": "|".join(a.title for a in empty),
                        "prop": "extracts",
                        "exsentences": "3",
                        "explaintext": "1",
                        "redirects": "1",
                        "format": "json",
                    }
                    fb_resp = await _action_get(settings.wikipedia_action_url, params=fb_params)
                    fb_resp.raise_for_status()
                    fb_pages = fb_resp.json().get("query", {}).get("pages", {})
                    fb_map = {
                        p["title"]: p.get("extract", "").strip()
                        for p in fb_pages.values()
                        if "missing" not in p and p.get("extract", "").strip()
                    }
                    updated: list[ArticleSummary] = []
                    for a in batch_articles:
                        if not a.extract and a.title in fb_map:
                            filled = ArticleSummary(
                                title=a.title,
                                extract=fb_map[a.title],
                                page_url=a.page_url,
                                thumbnail_url=a.thumbnail_url,
                                page_type=a.page_type,
                            )
                            _summary_cache[a.title] = (filled, datetime.now())
                            updated.append(filled)
                        else:
                            updated.append(a)
                    batch_articles = updated
                except (httpx.HTTPError, httpx.TimeoutException, Exception) as fb_e:
                    logger.warning("fetch_summaries_bulk sentence fallback failed: %s", fb_e)

            # Any articles still empty after both attempts get cached with a
            # 1-hour TTL so we retry soon without hammering the API every call.
            for art in batch_articles:
                if not art.extract and art.title not in _summary_cache:
                    _summary_cache[art.title] = (
                        art,
                        datetime.now() - _SUMMARY_TTL + timedelta(hours=1),
                    )

            results.extend(a for a in batch_articles if a.extract)
        except (httpx.HTTPError, httpx.TimeoutException, Exception) as e:
            logger.warning("fetch_summaries_bulk batch failed: %s", e)

    return results


_CACHE_FILE = Path(".cache/candidates.json")


def save_candidate_cache() -> None:
    try:
        _CACHE_FILE.parent.mkdir(exist_ok=True)
        data = {
            k: {"titles": v[0], "cached_at": v[1].isoformat()}
            for k, v in _candidate_cache.items()
        }
        _CACHE_FILE.write_text(json.dumps(data))
        logger.info("Candidate cache saved: %d entries", len(data))
    except Exception as e:
        logger.warning("save_candidate_cache failed: %s", e)


def load_candidate_cache() -> None:
    if not _CACHE_FILE.exists():
        return
    try:
        data = json.loads(_CACHE_FILE.read_text())
        now = datetime.now()
        loaded = 0
        for k, v in data.items():
            cached_at = datetime.fromisoformat(v["cached_at"])
            if now - cached_at < _CANDIDATE_TTL:
                _candidate_cache[k] = (v["titles"], cached_at)
                loaded += 1
        logger.info("Candidate cache loaded: %d entries from disk", loaded)
    except Exception as e:
        logger.warning("load_candidate_cache failed: %s", e)
