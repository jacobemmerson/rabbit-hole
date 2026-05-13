from unittest.mock import patch

import pytest
import respx
from fastapi.testclient import TestClient

from app.main import create_app
from app.models import ReadingHistoryEntry, UserProfile
from app.services.cookie import dump_profile, load_profile


@pytest.fixture(autouse=True, scope="session")
def patch_load_model():
    import asyncio
    import app.services.wikipedia as wiki
    wiki._rest_sem = asyncio.Semaphore(2)
    wiki._action_sem = asyncio.Semaphore(1)
    with patch("app.services.embeddings.load_model"), \
         patch("app.services.wikipedia.fetch_top_articles"):
        yield


@pytest.fixture
def client():
    return TestClient(create_app())


@pytest.fixture
def empty_profile() -> UserProfile:
    return UserProfile(
        timezone="UTC",
        selected_categories=["Science"],
    )


@pytest.fixture
def returning_profile() -> UserProfile:
    return UserProfile(
        timezone="UTC",
        last_generated="2026-05-11",
        selected_categories=["Science", "History"],
        current_articles=[
            "Quantum_mechanics", "Roman_Empire", "Mathematics", "Science",
            "History", "Philosophy", "Biology", "Chemistry"
        ],
        seen_articles=["Photosynthesis"],
        reading_history=[
            ReadingHistoryEntry(title="Photosynthesis", rating=None, scroll_depth=30),
        ],
    )


@pytest.fixture
def warm_profile() -> UserProfile:
    return UserProfile(
        timezone="UTC",
        last_generated="2026-05-11",
        selected_categories=["Science", "History"],
        current_articles=["Article_A", "Article_B", "Article_C", "Article_D", "Article_E"],
        seen_articles=["Old_Seen"],
        reading_history=[
            ReadingHistoryEntry(title="Quantum_mechanics", rating=5, scroll_depth=90),
            ReadingHistoryEntry(title="Roman_Empire", rating=3, scroll_depth=50),
            ReadingHistoryEntry(title="Photosynthesis", rating=2, scroll_depth=20),
        ],
    )


@pytest.fixture
def cold_profile() -> UserProfile:
    return UserProfile(
        timezone="UTC",
        last_generated="2026-05-11",
        selected_categories=["Science"],
        current_articles=["Article_A", "Article_B", "Article_C", "Article_D", "Article_E"],
        seen_articles=[],
        reading_history=[
            ReadingHistoryEntry(title="Quantum_mechanics", rating=4, scroll_depth=80),
        ],
    )


@pytest.fixture
def signed_cookie_for():
    def _make(profile: UserProfile) -> str:
        return dump_profile(profile)
    return _make


def extract_profile(response) -> UserProfile:
    set_cookie = response.headers.get("set-cookie", "")
    part = next((p for p in set_cookie.split(";") if "session=" in p), None)
    assert part, "No session cookie in response"
    return load_profile(part.split("session=", 1)[1])


@pytest.fixture
def wiki_mock():
    with respx.mock(base_url="https://en.wikipedia.org", assert_all_called=False) as mock:
        yield mock
