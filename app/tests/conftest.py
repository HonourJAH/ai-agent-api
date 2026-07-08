"""
Shared fixtures for the AI Agent API test suite.

No test here ever contacts real Ollama, Tavily, or Redis. The trickiest
piece is simulating Ollama's streaming NDJSON /api/chat responses — the
helpers below build a fake httpx.AsyncClient whose .stream() returns
pre-scripted lines, matching the exact shape confirmed against real
qwen3:4b earlier in this project (thinking/content stream incrementally,
tool_calls arrives as one complete chunk).
"""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from fakeredis.aioredis import FakeRedis


def make_ndjson_lines(chunks: list[dict]) -> list[str]:
    """Turn a list of chunk dicts into NDJSON strings, same shape Ollama
    actually sends over the wire.
    """
    return [json.dumps(chunk) for chunk in chunks]


class _FakeStreamResponse:
    def __init__(self, lines: list[str]):
        self._lines = lines
        self.raise_for_status = MagicMock(return_value=None)

    async def aiter_lines(self):
        for line in self._lines:
            yield line


class _FakeStreamContextManager:
    def __init__(self, lines: list[str]):
        self._lines = lines

    async def __aenter__(self):
        return _FakeStreamResponse(self._lines)

    async def __aexit__(self, *args):
        return False


@pytest.fixture
def make_fake_ollama_client():
    """Factory fixture: pass a list of "rounds", where each round is a
    list of chunk dicts. Returns a mock httpx.AsyncClient whose .stream()
    yields each round in sequence on successive calls — mirrors the
    multi-round tool-calling loop (round 1: tool call, round 2: final
    answer, etc).
    """

    def _make(rounds: list[list[dict]]):
        call_count = {"n": 0}

        def fake_stream(method, url, **kwargs):
            idx = call_count["n"]
            call_count["n"] += 1
            lines = make_ndjson_lines(rounds[idx])
            return _FakeStreamContextManager(lines)

        client = MagicMock()
        client.stream = fake_stream
        client.get = AsyncMock()
        return client

    return _make


@pytest.fixture
async def fake_redis():
    """A real (in-memory) async Redis-compatible client via fakeredis —
    no actual Redis server needed."""
    client = FakeRedis(decode_responses=True)
    yield client
    await client.aclose()
