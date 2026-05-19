import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.config import settings
from app.dependencies import get_user_profile
from app.models import ReadingHistoryEntry, UserProfile
from app.services.cookie import dump_profile
from app.services.recommendations import CATEGORY_SEED_POOL
from app.services.wikipedia import fetch_summary, fetch_wiki_page, inject_article_chrome

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


def _group_history(reading_history: list[ReadingHistoryEntry]) -> list[dict]:
    """Group reading_history into [{entry, children}] dicts for hierarchical display.

    Root entries have source=None (arrived from the feed).
    Sub-articles (any depth) are flattened under their root by following the source chain.
    Most-recently-active root appears first.
    """
    by_title = {e.title: e for e in reading_history}

    def find_root_title(entry: ReadingHistoryEntry, depth: int = 0) -> str:
        if entry.source is None or depth > 20:
            return entry.title
        parent = by_title.get(entry.source)
        if parent is None:
            return entry.title
        return find_root_title(parent, depth + 1)

    roots: list[ReadingHistoryEntry] = []
    roots_set: set[str] = set()
    children_map: dict[str, list[ReadingHistoryEntry]] = {}

    for entry in reading_history:
        if entry.source is None:
            if entry.title not in roots_set:
                roots.append(entry)
                roots_set.add(entry.title)
        else:
            root_title = find_root_title(entry)
            if root_title not in roots_set:
                root_entry = by_title.get(root_title, entry)
                roots.append(root_entry)
                roots_set.add(root_title)
            children_map.setdefault(root_title, []).append(entry)

    return [
        {"entry": r, "children": children_map.get(r.title, [])}
        for r in reversed(roots)
    ]


@router.get("/onboarding")
async def onboarding(request: Request):
    return templates.TemplateResponse(
        request, "onboarding.html", {"categories": list(CATEGORY_SEED_POOL.keys())}
    )


@router.get("/")
async def home(
    request: Request,
    profile: UserProfile = Depends(get_user_profile),
):
    if not profile.selected_categories and not profile.custom_interests:
        return RedirectResponse(url="/onboarding", status_code=302)

    # Inject profile data so JS can initialise IndexedDB on first load.
    # The httpOnly cookie is unreadable by JS; this one-time inline script
    # bridges the gap without a separate non-httpOnly cookie.
    init_data = json.dumps({
        "timezone": profile.timezone,
        "selected_categories": profile.selected_categories,
        "custom_interests": profile.custom_interests,
        "reading_history": [e.model_dump() for e in profile.reading_history],
    })

    resp = templates.TemplateResponse(
        request,
        "feed.html",
        {
            "history_groups": _group_history(profile.reading_history),
            "init_data": init_data,
        },
    )
    # Re-issue cookie on every home visit to keep its TTL fresh.
    resp.set_cookie(
        key=settings.cookie_name,
        value=dump_profile(profile),
        max_age=settings.cookie_max_age,
        httponly=True,
        samesite="lax",
    )
    return resp


@router.get("/article/{title}")
async def article_detail(
    title: str,
    request: Request,
    from_title: str | None = Query(default=None, alias="from"),
    profile: UserProfile = Depends(get_user_profile),
):
    wiki_html, article = await asyncio.gather(
        fetch_wiki_page(title),
        fetch_summary(title),
    )

    if article is None and wiki_html is None:
        raise HTTPException(status_code=404, detail="Article not found")

    # Use canonical title from Wikipedia (spaces not underscores) so history
    # entries are consistent regardless of how the user arrived at this page.
    canonical = article.title if article else title

    # Update reading_history in the signed cookie so /api/score has fresh data.
    # seen_articles tracking has moved to client-side IndexedDB.
    if not any(e.title == canonical for e in profile.reading_history):
        canonical_source = from_title.replace("_", " ") if from_title else None
        profile.reading_history.append(
            ReadingHistoryEntry(title=canonical, rating=None, scroll_depth=0, source=canonical_source)
        )

    existing = next((e for e in profile.reading_history if e.title == canonical), None)
    current_rating = existing.rating if existing else None

    if wiki_html:
        resp = HTMLResponse(content=inject_article_chrome(wiki_html, canonical, current_rating))
    else:
        resp = templates.TemplateResponse(
            request,
            "article.html",
            {
                "article": article,
                "wiki_css": "",
                "wiki_html": "",
                "current_rating": current_rating,
            },
        )

    resp.set_cookie(
        key=settings.cookie_name,
        value=dump_profile(profile),
        max_age=settings.cookie_max_age,
        httponly=True,
        samesite="lax",
    )
    return resp
