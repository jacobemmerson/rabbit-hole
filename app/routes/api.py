from fastapi import APIRouter, Depends, Form, Query, Response
from fastapi.responses import RedirectResponse
from pydantic import Field

from app.config import settings
from app.dependencies import get_user_profile
from app.models import ReadingHistoryEntry, ScoreRequest, UserProfile
from app.services.cookie import dump_profile
from app.services.recommendations import (
    CATEGORY_SEED_POOL,
    get_daily_seeds,
    score_candidates_for_profile,
)

router = APIRouter(prefix="/api")


def _set_cookie(response: Response, profile: UserProfile) -> None:
    response.set_cookie(
        key=settings.cookie_name,
        value=dump_profile(profile),
        max_age=settings.cookie_max_age,
        httponly=True,
        samesite="lax",
    )


# ── Onboarding ────────────────────────────────────────────────────────────────

@router.post("/onboard")
async def onboard(
    selected_categories: list[str] = Form(default=[]),
    timezone: str = Form(default="UTC"),
    custom_interests: list[str] = Form(default=[]),
):
    profile = UserProfile(
        timezone=timezone,
        selected_categories=selected_categories,
        custom_interests=[t.strip() for t in custom_interests if t.strip()][:5],
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


# ── Feedback + scroll (update signed cookie; JS mirrors to IndexedDB) ─────────

from pydantic import BaseModel

class FeedbackRequest(BaseModel):
    title: str
    rating: int = Field(..., ge=1, le=5)


class ScrollRequest(BaseModel):
    title: str
    depth: int = Field(..., ge=0, le=100)


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


# ── Data reset ───────────────────────────────────────────────────────────────

@router.post("/reset")
async def reset(response: Response):
    """Clear the signed session cookie. Client is responsible for deleting IndexedDB."""
    response.delete_cookie(key=settings.cookie_name, httponly=True, samesite="lax")
    return {"ok": True}


# ── Client-side feed support ──────────────────────────────────────────────────

@router.get("/seed-queries")
async def seed_queries(
    categories: list[str] = Query(default=[], alias="categories[]"),
    custom: list[str] = Query(default=[], alias="custom[]"),
):
    """Return today's Wikipedia search queries per interest group.

    Stateless — no cookie needed. The client uses these to make Wikipedia
    search calls from its own network, bypassing server-side rate limits.
    Each category gets two rotating seed queries; each custom interest is
    returned as its own single-query group (the text itself is the query).
    """
    groups = []
    for cat in categories:
        if cat in CATEGORY_SEED_POOL:
            groups.append({"key": cat, "queries": get_daily_seeds(cat)})
    for i, text in enumerate(custom):
        text = text.strip()
        if text:
            groups.append({"key": f"__custom__{i}", "queries": [text]})
    return {"groups": groups}


@router.post("/score")
async def score(
    body: ScoreRequest,
    profile: UserProfile = Depends(get_user_profile),
):
    """Rank client-provided candidates using the server-side embedding model.

    Reads reading_history and custom_interests from the signed cookie for trust.
    Candidate extracts and reading-history extracts arrive from the client's
    IndexedDB — no Wikipedia calls happen here.
    """
    ranked = score_candidates_for_profile(
        profile=profile,
        candidates=body.candidates,
        history_extracts=body.history_extracts,
        n=body.n,
    )
    return {"titles": ranked}
