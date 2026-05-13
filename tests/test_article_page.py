import pytest

from app.models import ArticleSummary, ReadingHistoryEntry, UserProfile
from app.services.cookie import dump_profile
from tests.conftest import extract_profile

MOCK_ARTICLE = ArticleSummary(
    title="Quantum_mechanics",
    extract="Quantum mechanics is a fundamental theory in physics.",
    page_url="https://en.wikipedia.org/wiki/Quantum_mechanics",
)


@pytest.fixture(autouse=True)
def mock_wiki_html(mocker):
    mocker.patch("app.routes.pages.fetch_wiki_page", return_value=None)


def test_article_page_renders(client, mocker):
    mocker.patch("app.routes.pages.fetch_summary", return_value=MOCK_ARTICLE)
    response = client.get("/article/Quantum_mechanics")
    assert response.status_code == 200
    assert "Quantum mechanics is a fundamental theory" in response.text


def test_article_page_404(client, mocker):
    mocker.patch("app.routes.pages.fetch_summary", return_value=None)
    mocker.patch("app.routes.pages.fetch_wiki_page", return_value=None)
    response = client.get("/article/Nonexistent_Article_XYZ")
    assert response.status_code == 404


def test_article_page_shows_thumbs_rating(client, mocker, signed_cookie_for):
    mocker.patch("app.routes.pages.fetch_summary", return_value=MOCK_ARTICLE)
    profile = UserProfile(
        selected_categories=["Science & Mathematics"],
        reading_history=[
            ReadingHistoryEntry(title="Quantum_mechanics", rating=5, scroll_depth=80)
        ],
        seen_articles=["Quantum_mechanics"],
    )
    client.cookies.set("session", signed_cookie_for(profile))
    response = client.get("/article/Quantum_mechanics")
    assert response.status_code == 200
    # Thumbs up (rating=5) should be fully opaque, others dimmed
    assert 'data-rating="5"' in response.text
    assert 'opacity:1' in response.text


def test_article_page_writes_cookie(client, mocker):
    mocker.patch("app.routes.pages.fetch_summary", return_value=MOCK_ARTICLE)
    response = client.get("/article/Quantum_mechanics")
    assert response.status_code == 200
    assert "set-cookie" in response.headers


def test_article_visit_marks_seen(client, mocker):
    mocker.patch("app.routes.pages.fetch_summary", return_value=MOCK_ARTICLE)
    response = client.get("/article/Quantum_mechanics")
    updated = extract_profile(response)
    # Canonical title from article.title ("Quantum_mechanics") is used, not the URL slug
    assert "Quantum_mechanics" in updated.seen_articles


def test_article_visit_adds_history_entry(client, mocker):
    mocker.patch("app.routes.pages.fetch_summary", return_value=MOCK_ARTICLE)
    response = client.get("/article/Quantum_mechanics")
    updated = extract_profile(response)
    titles = [e.title for e in updated.reading_history]
    assert "Quantum_mechanics" in titles
    entry = next(e for e in updated.reading_history if e.title == "Quantum_mechanics")
    assert entry.rating is None
    assert entry.scroll_depth == 0


def test_article_visit_does_not_duplicate_history(client, mocker, signed_cookie_for):
    mocker.patch("app.routes.pages.fetch_summary", return_value=MOCK_ARTICLE)
    profile = UserProfile(
        selected_categories=["Science & Mathematics"],
        reading_history=[
            ReadingHistoryEntry(title="Quantum_mechanics", rating=3, scroll_depth=50)
        ],
        seen_articles=["Quantum_mechanics"],
    )
    client.cookies.set("session", signed_cookie_for(profile))
    response = client.get("/article/Quantum_mechanics")
    updated = extract_profile(response)
    entries = [e for e in updated.reading_history if e.title == "Quantum_mechanics"]
    assert len(entries) == 1
    assert entries[0].rating == 3  # existing rating preserved


def test_article_visit_uses_canonical_title(client, mocker):
    # URL slug uses underscore; Wikipedia returns title with space — canonical wins
    article_with_space = ArticleSummary(
        title="Music industry",
        extract="The music industry...",
        page_url="https://en.wikipedia.org/wiki/Music_industry",
    )
    mocker.patch("app.routes.pages.fetch_summary", return_value=article_with_space)
    response = client.get("/article/Music_industry")
    updated = extract_profile(response)
    assert "Music industry" in updated.seen_articles
    assert "Music_industry" not in updated.seen_articles


def test_article_page_shows_wikipedia_link(client, mocker):
    mocker.patch("app.routes.pages.fetch_summary", return_value=MOCK_ARTICLE)
    response = client.get("/article/Quantum_mechanics")
    assert "en.wikipedia.org/wiki/Quantum_mechanics" in response.text


def test_article_visit_from_another_article_stores_source(client, mocker):
    julius = ArticleSummary(title="Julius Caesar", extract="Roman general.", page_url="")
    mocker.patch("app.routes.pages.fetch_summary", return_value=julius)
    # URL slug uses underscore; stored source should be normalized to spaces
    response = client.get("/article/Julius_Caesar?from=Roman_Empire")
    updated = extract_profile(response)
    entry = next(e for e in updated.reading_history if e.title == "Julius Caesar")
    assert entry.source == "Roman Empire"  # normalized from "Roman_Empire"


def test_article_visit_from_feed_has_no_source(client, mocker):
    mocker.patch("app.routes.pages.fetch_summary", return_value=MOCK_ARTICLE)
    response = client.get("/article/Quantum_mechanics")
    updated = extract_profile(response)
    entry = next(e for e in updated.reading_history if e.title == "Quantum_mechanics")
    assert entry.source is None
