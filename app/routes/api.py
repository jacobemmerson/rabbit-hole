from fastapi import APIRouter, Depends, Form, Query, Response
from fastapi.responses import RedirectResponse
from pydantic import Field

from app.config import settings
from app.dependencies import get_user_profile
from app.models import ArticleSummary, BanditStateModel, BanditUpdateRequest, ReadingHistoryEntry, ScoreRequest, UserProfile
from app.services import bandits, embeddings
from app.services.cookie import dump_profile
from app.services.query_gen import extract_seed_bigrams
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
    history_len: int = Query(default=0),
):
    """Return today's Wikipedia search queries per interest group.

    Stateless — no cookie needed. The client uses these to make Wikipedia
    search calls from its own network, bypassing server-side rate limits.

    Queries taper as reading history grows so learned TF-IDF queries take over:
      - Category queries:  2 per category (0–3 articles) → 1 (4–7) → 0 (8+)
      - Custom queries:    1 per interest (0–9 articles) → 0 (10+)
    General category interests taper first; custom interests persist longer.
    """
    if history_len >= 8:
        cat_limit = 0
    elif history_len >= 4:
        cat_limit = 1
    else:
        cat_limit = 2

    include_custom = history_len < 10

    groups = []
    for cat in categories:
        if cat in CATEGORY_SEED_POOL and cat_limit > 0:
            groups.append({"key": cat, "queries": get_daily_seeds(cat)[:cat_limit]})
    if include_custom:
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
    """Rank client-provided candidates using the embedding model + bandit.

    Returns:
      titles       — ranked article titles
      phi_map      — feature vector per candidate (store in IndexedDB for bandit updates)
      bandit_state — current state (default warm-start if none was supplied)
      seed_queries — TF-IDF bigrams from reading history, reranked by taste similarity,
                     for use as learned seed queries on the next feed generation
    """
    # Compute taste vectors once — used by both the bandit scorer and query gen.
    history_summaries = [
        ArticleSummary(title=e.title, extract=body.history_extracts[e.title], page_url="")
        for e in profile.reading_history
        if body.history_extracts.get(e.title)
    ]
    taste_vectors = embeddings.compute_taste_vectors(profile, history_summaries)

    ranked, phi_map = score_candidates_for_profile(
        profile=profile,
        candidates=body.candidates,
        history_extracts=body.history_extracts,
        bandit_state=body.bandit_state,
        n=body.n,
        taste_vectors=taste_vectors,
    )

    candidate_extracts = [c.extract for c in body.candidates]
    seed_queries = extract_seed_bigrams(
        profile=profile,
        history_extracts=body.history_extracts,
        candidate_extracts=candidate_extracts,
        taste_vectors=taste_vectors,
    )

    # Echo back the supplied state (or the default) so the client always has a
    # valid bandit_state to store, even on first call.
    bs = (
        bandits.BanditState.from_dict(body.bandit_state.model_dump())
        if body.bandit_state is not None
        else bandits.default_state()
    )
    return {
        "titles":       ranked,
        "phi_map":      phi_map,
        "bandit_state": bs.to_dict(),
        "seed_queries": seed_queries,
    }


@router.post("/bandit/update")
async def bandit_update(body: BanditUpdateRequest):
    """Incorporate one (φ, r) feedback observation into the bandit posterior.

    Called by the client after a rating or meaningful scroll event.  Pure linear
    algebra — no Wikipedia I/O, no cookie required.
    """
    current = bandits.BanditState.from_dict(body.bandit_state.model_dump())
    updated = bandits.update(current, body.phi, body.reward)
    return {"bandit_state": updated.to_dict()}
