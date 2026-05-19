from pydantic import BaseModel


class ReadingHistoryEntry(BaseModel):
    title: str
    rating: int | None = None
    scroll_depth: int = 0
    source: str | None = None  # canonical title of the article that linked here; None = came from feed


class UserProfile(BaseModel):
    timezone: str = "UTC"
    last_generated: str | None = None
    selected_categories: list[str] = []
    current_articles: list[str] = []
    seen_articles: list[str] = []
    reading_history: list[ReadingHistoryEntry] = []
    shown_articles: list[str] = []   # recently displayed; excluded temporarily from recommendations
    custom_interests: list[str] = []  # free-text interests embedded directly into the taste vector

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


class FeedResponse(BaseModel):
    articles: list[ArticleSummary]
    is_new_day: bool
