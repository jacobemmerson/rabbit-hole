import pytest

from app.models import ArticleSummary, UserProfile
from app.services.cookie import dump_profile

MOCK_ARTICLES = [
    ArticleSummary(title=f"Article{i}", extract=f"Extract {i}", page_url=f"https://en.wikipedia.org/wiki/Article{i}")
    for i in range(8)
]
MOCK_TITLES = [a.title for a in MOCK_ARTICLES]


@pytest.fixture(autouse=True)
def mock_feed_services(mocker):
    mocker.patch("app.routes.pages.generate_feed", return_value=MOCK_TITLES)
    mocker.patch("app.routes.pages.fetch_summaries_bulk", return_value=MOCK_ARTICLES)


def test_home_new_user(client, signed_cookie_for, empty_profile):
    client.cookies.set("session", signed_cookie_for(empty_profile))
    response = client.get("/")
    assert response.status_code == 200
    assert response.text.count('class="article-card"') == 8


def test_home_returning_user(client, signed_cookie_for, returning_profile):
    client.cookies.set("session", signed_cookie_for(returning_profile))
    response = client.get("/")
    assert response.status_code == 200
    assert response.text.count('class="article-card"') == 8


def test_home_no_categories_redirects(client):
    client.cookies.set("session", dump_profile(UserProfile(timezone="UTC", selected_categories=[])))
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == "/onboarding"


def test_home_no_cookie_redirects(client):
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == "/onboarding"
