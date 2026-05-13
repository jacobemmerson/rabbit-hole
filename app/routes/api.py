from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Form, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from app.config import settings
from app.dependencies import get_user_profile
from app.models import ArticleSummary, FeedResponse, ReadingHistoryEntry, UserProfile
from app.services.cookie import dump_profile
from app.services.recommendations import generate_feed
from app.services.reset import get_local_date
from app.services.wikipedia import fetch_summaries_bulk, get_popularity

router = APIRouter(prefix="/api")


class FeedbackRequest(BaseModel):
    title: str
    rating: int = Field(..., ge=1, le=5)


class ScrollRequest(BaseModel):
    title: str
    depth: int = Field(..., ge=0, le=100)


def _set_cookie(response: Response, profile: UserProfile) -> None:
    response.set_cookie(
        key=settings.cookie_name,
        value=dump_profile(profile),
        max_age=settings.cookie_max_age,
        httponly=True,
        samesite="lax",
    )


@router.post("/refresh", response_model=FeedResponse)
async def refresh(
    response: Response,
    profile: UserProfile = Depends(get_user_profile),
):
    # Archive outgoing articles into shown_articles before replacing them
    new_shown = list(dict.fromkeys(profile.shown_articles + profile.current_articles))
    profile.shown_articles = new_shown[-42:]
    profile.current_articles = []

    new_titles = await generate_feed(profile)
    profile.current_articles = new_titles
    profile.last_generated = get_local_date(profile.timezone).isoformat()
    # Record newly generated articles as shown
    new_shown = list(dict.fromkeys(profile.shown_articles + new_titles))
    profile.shown_articles = new_shown[-42:]

    _set_cookie(response, profile)
    articles = await fetch_summaries_bulk(new_titles)
    return FeedResponse(articles=articles, is_new_day=False)


@router.post("/onboard")
async def onboard(
    selected_categories: list[str] = Form(...),
    timezone: str = Form(default="UTC"),
):
    profile = UserProfile(
        selected_categories=selected_categories,
        timezone=timezone,
    )
    redirect = RedirectResponse(url="/", status_code=303)
    redirect.set_cookie(
        key=settings.cookie_name,
        value=dump_profile(profile),
        max_age=settings.cookie_max_age,
        httponly=True,
        samesite="lax",
    )
    return redirect


@router.post("/feedback")
async def feedback(
    body: FeedbackRequest,
    response: Response,
    profile: UserProfile = Depends(get_user_profile),
):
    entry = next((e for e in profile.reading_history if e.title == body.title), None)
    if entry:
        entry.rating = body.rating
    else:
        profile.reading_history.append(
            ReadingHistoryEntry(title=body.title, rating=body.rating, scroll_depth=0)
        )
    _set_cookie(response, profile)
    return {"ok": True}


@router.post("/scroll")
async def scroll(
    body: ScrollRequest,
    response: Response,
    profile: UserProfile = Depends(get_user_profile),
):
    entry = next((e for e in profile.reading_history if e.title == body.title), None)
    if entry:
        entry.scroll_depth = max(entry.scroll_depth, body.depth)
    else:
        profile.reading_history.append(
            ReadingHistoryEntry(title=body.title, rating=None, scroll_depth=body.depth)
        )
    _set_cookie(response, profile)
    return {"ok": True}


_TOP_DAILY_META = ("Outline of ", "List of ", "Index of ", "Lists of ", "Glossary of ")
_TOP_DAILY_TTL = timedelta(hours=6)  # daily data; refresh well within each day

_top_daily_result: list | None = None
_top_daily_computed_at: datetime | None = None


def _norm(title: str) -> str:
    """Normalise a Wikipedia title for fuzzy matching.

    The Wikimedia pageviews API and the Wikipedia action API occasionally
    return slightly different forms of the same title — different dash
    characters (en-dash vs hyphen), curly vs straight quotes, etc.
    Normalising before comparison prevents these mismatches from causing
    popular articles to be ranked as unknown and excluded from Top Daily.
    """
    return (
        title.lower()
        .replace("–", "-").replace("—", "-").replace("−", "-")
        .replace("’", "'").replace("‘", "'")
        .strip()
    )


@router.get("/top-daily")
async def top_daily():
    """Return the top Wikipedia articles by monthly pageviews.
    Iterates candidates in strict popularity order and matches them to
    returned summaries using normalised titles, so minor formatting
    differences between the Wikimedia and Wikipedia title forms
    never push a genuinely popular article out of the top 6.
    """
    global _top_daily_result, _top_daily_computed_at

    if (
        _top_daily_result is not None
        and _top_daily_computed_at is not None
        and datetime.now() - _top_daily_computed_at < _TOP_DAILY_TTL
    ):
        return {"articles": _top_daily_result}

    popularity = get_popularity()
    if not popularity:
        return {"articles": []}

    candidates = [
        t for t in sorted(popularity, key=popularity.__getitem__)
        if not any(t.startswith(p) for p in _TOP_DAILY_META)
    ][:30]

    summaries = await fetch_summaries_bulk(candidates)

    # Key summaries by their normalised title so we can match them to
    # candidates even when there are minor formatting differences.
    norm_map: dict[str, ArticleSummary] = {_norm(s.title): s for s in summaries}

    # Walk candidates in popularity order (most popular first) and collect
    # the first 6 that have a matching summary.
    result: list[ArticleSummary] = []
    for candidate in candidates:
        article = norm_map.get(_norm(candidate))
        if article:
            result.append(article)
            if len(result) >= settings.feed_size:
                break

    articles = [s.model_dump() for s in result]
    _top_daily_result = articles
    _top_daily_computed_at = datetime.now()
    return {"articles": articles}
