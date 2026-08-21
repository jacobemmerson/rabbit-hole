from pydantic import BaseModel


class ReadingHistoryEntry(BaseModel):
    title: str
    rating: int | None = None
    scroll_depth: int = 0
    source: str | None = None  # canonical title of the article that linked here; None = came from feed


class UserProfile(BaseModel):
    # Only what the server needs to trust, kept in the signed cookie.
    # Everything else lives in the client's IndexedDB.
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
    group: str  # category name, "__custom__N", or "__history__"


class BanditStateModel(BaseModel):
    """Pydantic wrapper for API serialization of bandits.BanditState."""
    theta:     list[float]
    A_flat:    list[float]
    b:         list[float]
    n_updates: int = 0


class ScoreRequest(BaseModel):
    candidates:    list[CandidateItem]
    history_extracts: dict[str, str] = {}
    bandit_state:  BanditStateModel | None = None
    n: int = 8


class BanditUpdateRequest(BaseModel):
    phi:          list[float]
    reward:       float
    bandit_state: BanditStateModel


class FeedResponse(BaseModel):
    articles: list[ArticleSummary]
    is_new_day: bool
