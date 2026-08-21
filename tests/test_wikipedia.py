from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from app.models import ArticleSummary
from app.services import wikipedia as wiki_service

SUMMARY_PAYLOAD = {
    "title": "Quantum mechanics",
    "extract": "Quantum mechanics is a fundamental theory.",
    "content_urls": {"desktop": {"page": "https://en.wikipedia.org/wiki/Quantum_mechanics"}},
}

CATEGORY_MEMBERS_PAYLOAD = {
    "query": {
        "categorymembers": [
            {"title": "Wave function"},
            {"title": "Schrödinger equation"},
        ]
    }
}

SEARCH_PAYLOAD = {
    "query": {
        "search": [
            {"title": "Wave function"},
            {"title": "Schrödinger equation"},
        ]
    }
}


def _mock_response(status: int, json_data: dict | None = None) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = json_data or {}
    if status >= 400:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            message=str(status), request=MagicMock(), response=resp
        )
    else:
        resp.raise_for_status.return_value = None
    return resp


@pytest.fixture(autouse=True)
def mock_client(mocker):
    import asyncio
    client = AsyncMock()
    wiki_service._client = client
    # Create semaphores in the current test's event loop
    wiki_service._rest_sem = asyncio.Semaphore(2)
    wiki_service._action_sem = asyncio.Semaphore(1)
    wiki_service._summary_cache.clear()
    wiki_service._candidate_cache.clear()
    yield client
    wiki_service._client = None


@pytest.mark.asyncio
async def test_fetch_summary_success(mock_client):
    mock_client.get.return_value = _mock_response(200, SUMMARY_PAYLOAD)
    result = await wiki_service.fetch_summary("Quantum_mechanics")
    assert isinstance(result, ArticleSummary)
    assert result.title == "Quantum mechanics"
    assert result.extract != ""


@pytest.mark.asyncio
async def test_fetch_summary_404(mock_client):
    mock_client.get.return_value = _mock_response(404, {"type": "not found"})
    result = await wiki_service.fetch_summary("Nonexistent_Article_XYZ")
    assert result is None


@pytest.mark.asyncio
async def test_fetch_summary_network_error(mock_client):
    mock_client.get.side_effect = httpx.ConnectError("connection refused")
    result = await wiki_service.fetch_summary("Broken_Article")
    assert result is None


@pytest.mark.asyncio
async def test_fetch_category_members_success(mock_client):
    mock_client.get.return_value = _mock_response(200, CATEGORY_MEMBERS_PAYLOAD)
    result = await wiki_service.fetch_category_members("Science")
    assert "Wave function" in result
    assert "Schrödinger equation" in result


@pytest.mark.asyncio
async def test_fetch_category_members_error(mock_client):
    mock_client.get.return_value = _mock_response(503)
    result = await wiki_service.fetch_category_members("Science")
    assert result == []


@pytest.mark.asyncio
async def test_fetch_search_success(mock_client):
    mock_client.get.return_value = _mock_response(200, SEARCH_PAYLOAD)
    result = await wiki_service.fetch_search("Quantum_mechanics")
    assert "Wave function" in result
    assert "Schrödinger equation" in result


@pytest.mark.asyncio
async def test_fetch_search_error(mock_client):
    mock_client.get.return_value = _mock_response(503)
    result = await wiki_service.fetch_search("Quantum_mechanics")
    assert result == []


@pytest.mark.asyncio
async def test_fetch_summary_retries_on_429(mock_client, mocker):
    mocker.patch("asyncio.sleep")
    mock_client.get.side_effect = [
        _mock_response(429),
        _mock_response(200, SUMMARY_PAYLOAD),
    ]
    result = await wiki_service.fetch_summary("Quantum_mechanics")
    assert isinstance(result, ArticleSummary)
    assert mock_client.get.call_count == 2


@pytest.mark.asyncio
async def test_fetch_search_retries_on_429(mock_client, mocker):
    mocker.patch("asyncio.sleep")
    mock_client.get.side_effect = [
        _mock_response(429),
        _mock_response(200, SEARCH_PAYLOAD),
    ]
    result = await wiki_service.fetch_search("quantum mechanics")
    assert "Wave function" in result
    assert mock_client.get.call_count == 2


@pytest.mark.asyncio
async def test_fetch_summary_exhausts_retries_on_429(mock_client, mocker):
    mocker.patch("asyncio.sleep")
    mock_client.get.return_value = _mock_response(429)
    result = await wiki_service.fetch_summary("Quantum_mechanics")
    assert result is None
    assert mock_client.get.call_count == 5


@pytest.mark.asyncio
async def test_fetch_summaries_bulk_excludes_empty_extract(mock_client):
    # Both extract passes (exintro then exsentences fallback) return no text.
    # The article must NOT appear in results, an empty card would be shown otherwise.
    empty_batch = {
        "query": {
            "pages": {
                "1": {
                    "pageid": 1,
                    "title": "Tom Kane",
                    "extract": "",
                    "fullurl": "https://en.wikipedia.org/wiki/Tom_Kane",
                }
            }
        }
    }
    mock_client.get.return_value = _mock_response(200, empty_batch)
    results = await wiki_service.fetch_summaries_bulk(["Tom Kane"])
    assert results == []


@pytest.mark.asyncio
async def test_fetch_summaries_bulk_partial(mock_client):
    # fetch_summaries_bulk now uses a single batch action API call.
    # Missing articles appear with "missing" key in the pages dict.
    batch_response = {
        "query": {
            "pages": {
                "1":  {"pageid": 1,  "title": "ArticleA", "extract": "Extract for ArticleA.",
                       "fullurl": "https://en.wikipedia.org/wiki/ArticleA"},
                "-1": {"title": "ArticleB", "missing": ""},
                "3":  {"pageid": 3,  "title": "ArticleC", "extract": "Extract for ArticleC.",
                       "fullurl": "https://en.wikipedia.org/wiki/ArticleC"},
            }
        }
    }
    mock_client.get.return_value = _mock_response(200, batch_response)
    results = await wiki_service.fetch_summaries_bulk(["ArticleA", "ArticleB", "ArticleC"])

    assert len(results) == 2
    titles = {r.title for r in results}
    assert "ArticleA" in titles
    assert "ArticleC" in titles
