from pydantic import BaseModel


class ReadingHistoryEntry(BaseModel):
    title: str
    rating: int | None = None
    scroll_depth: int = 0
    source: str | None = None  # canonical title of the article that linked here; None = came from feed


class UserProfile(BaseModel):
    # Fields kept in the signed cookie — only what the server needs to trust.
    # All other state (current_articles, seen_articles, shown_articles,
    # last_generated) lives in the client's IndexedDB.
    timezone: str = "UTC"
    selected_categories: list[str] = []
    custom_interests: list[str] = []
    reading_history: list[ReadingHistoryEntry] = []

    def article_weight(self, title: str) -> float:
        rating_map = {1: 0.2, 2: 0.5, 3: 1.0, 4: 1.5, 5: 2.0}
        for entry in self.reading_history:
            if entry.title == title:
                base = rating_map.get(entry.rating, 1.0) if entry.rating else 1.0
                if entry.scroll_depth > 75:
                    base += 0.3
                elif entry.scroll_depth < 25:
                    base -= 0.3
                return max(base, 0.0)
        return 1.0


class ArticleSummary(BaseModel):
    title: str
    extract: str
    page_url: str = ""
    thumbnail_url: str | None = None
    page_type: str = "standard"  # "standard", "disambiguation", "no-extract"


class CandidateItem(BaseModel):
    title: str
    extract: str
    group: str  # category name or "__custom__N"


class ScoreRequest(BaseModel):
    candidates: list[CandidateItem]
    history_extracts: dict[str, str] = {}  # title → extract, provided by client from IndexedDB
    n: int = 8


# FeedResponse kept for any callers that still reference it during migration
class FeedResponse(BaseModel):
    articles: list[ArticleSummary]
    is_new_day: bool
