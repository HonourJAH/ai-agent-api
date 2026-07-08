from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

import app.services.tools.web_search as web_search_module
from app.services.tools.web_search import search, WebSearchConfigError


@pytest.fixture(autouse=True)
def fake_api_key():
    """Ensure every test has a key set, except the one that deliberately
    tests the missing-key case (which clears it itself).
    """
    original = web_search_module.TAVILY_API_KEY
    web_search_module.TAVILY_API_KEY = "tvly-fake-key"
    yield
    web_search_module.TAVILY_API_KEY = original


class TestWebSearch:
    async def test_returns_trimmed_results(self):
        fake_response = MagicMock()
        fake_response.raise_for_status.return_value = None
        fake_response.json.return_value = {
            "results": [
                {
                    "title": "Weather in Abuja",
                    "url": "https://example.com",
                    "content": "Partly cloudy, 24C",
                    "score": 0.9,
                    "raw_content": None,
                }
            ]
        }
        mock_client = AsyncMock()
        mock_client.post.return_value = fake_response

        results = await search("current weather in Abuja", client=mock_client)

        assert results == [
            {
                "title": "Weather in Abuja",
                "url": "https://example.com",
                "content": "Partly cloudy, 24C",
            }
        ]
        # score/raw_content should NOT be present
        assert "score" not in results[0]

    async def test_sends_correct_auth_header_and_payload(self):
        fake_response = MagicMock()
        fake_response.raise_for_status.return_value = None
        fake_response.json.return_value = {"results": []}
        mock_client = AsyncMock()
        mock_client.post.return_value = fake_response

        await search("test query", max_results=3, client=mock_client)

        _, kwargs = mock_client.post.call_args
        assert kwargs["headers"] == {"Authorization": "Bearer tvly-fake-key"}
        assert kwargs["json"] == {"query": "test query", "max_results": 3}

    async def test_missing_api_key_raises_config_error(self):
        web_search_module.TAVILY_API_KEY = None
        mock_client = AsyncMock()

        with pytest.raises(WebSearchConfigError):
            await search("test", client=mock_client)

    async def test_empty_results_returns_empty_list(self):
        fake_response = MagicMock()
        fake_response.raise_for_status.return_value = None
        fake_response.json.return_value = {"results": []}
        mock_client = AsyncMock()
        mock_client.post.return_value = fake_response

        results = await search("obscure query with no hits", client=mock_client)
        assert results == []

    async def test_http_error_propagates(self):
        mock_client = AsyncMock()
        mock_client.post.side_effect = httpx.ConnectError(
            "Connection refused", request=httpx.Request("POST", "http://fake")
        )

        with pytest.raises(httpx.RequestError):
            await search("test", client=mock_client)

    async def test_creates_default_client_when_none_provided(self):
        with patch("app.services.tools.web_search.httpx.AsyncClient") as client_cls:
            entered = client_cls.return_value.__aenter__.return_value
            fake_response = MagicMock()
            fake_response.raise_for_status.return_value = None
            fake_response.json.return_value = {"results": []}
            entered.post = AsyncMock(return_value=fake_response)
            client_cls.return_value.__aexit__.return_value = None

            await search("test", client=None)

            client_cls.assert_called_once()
