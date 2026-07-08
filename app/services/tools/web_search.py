import os

import httpx

TAVILY_BASE_URL = os.getenv("TAVILY_BASE_URL", "https://api.tavily.com")
TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")

REQUEST_TIMEOUT_SECONDS = 15.0


class WebSearchConfigError(RuntimeError):
    """Raised when TAVILY_API_KEY isn't set — a configuration problem,
    not a runtime failure, so it's worth a distinct exception type the
    caller can recognize immediately rather than a generic auth error
    surfacing from deep inside an HTTP call.
    """


async def search(
    query: str,
    max_results: int = 5,
    client: httpx.AsyncClient | None = None,
) -> list[dict]:
    """Search the web via Tavily and return a compact list of results."""

    if not TAVILY_API_KEY:
        raise WebSearchConfigError(
            "TAVILY_API_KEY is not set. Get a free key at tavily.com and "
            "set it as an environment variable."
        )

    payload = {
        "query": query,
        "max_results": max_results,
    }
    headers = {"Authorization": f"Bearer {TAVILY_API_KEY}"}

    if client is not None:
        response = await client.post(
            f"{TAVILY_BASE_URL}/search",
            json=payload,
            headers=headers,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        data = response.json()
    else:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as default_client:
            response = await default_client.post(
                f"{TAVILY_BASE_URL}/search",
                json=payload,
                headers=headers,
            )
            response.raise_for_status()
            data = response.json()

    return [
        {
            "title": result.get("title", ""),
            "url": result.get("url", ""),
            "content": result.get("content", ""),
        }
        for result in data.get("results", [])
    ]
