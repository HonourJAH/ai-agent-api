import json
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.ollama_agent import AgentError


def parse_sse(response_text: str) -> list[dict]:
    events = []
    for block in response_text.strip().split("\n\n"):
        if block.startswith("data: "):
            events.append(json.loads(block[len("data: ") :]))
    return events


async def simple_agent_turn(messages, client):
    yield {"event": "token", "content": "Hello"}
    yield {"event": "token", "content": " world"}
    yield {"event": "done", "content": "Hello world"}


@pytest.fixture
def client():
    with patch("app.main.run_agent_turn", simple_agent_turn):
        with TestClient(app) as test_client:
            from fakeredis.aioredis import FakeRedis

            test_client.app.state.redis_client = FakeRedis(decode_responses=True)
            yield test_client


class TestAgentChatEndpoint:
    def test_new_session_generates_session_id(self, client):
        response = client.post("/agent/chat", json={"message": "hi"})
        assert response.status_code == 200

        events = parse_sse(response.text)
        assert events[0]["event"] == "session"
        assert len(events[0]["session_id"]) > 0

    def test_streams_all_expected_events(self, client):
        response = client.post("/agent/chat", json={"message": "hi"})
        events = parse_sse(response.text)

        event_types = [e["event"] for e in events]
        assert event_types == ["session", "token", "token", "done"]
        assert events[-1]["content"] == "Hello world"

    def test_provided_session_id_is_reused(self, client):
        response = client.post(
            "/agent/chat", json={"message": "hi", "session_id": "my-session"}
        )
        events = parse_sse(response.text)
        assert events[0]["session_id"] == "my-session"

    def test_successful_turn_is_persisted(self, client):
        client.post("/agent/chat", json={"message": "hi", "session_id": "persist-test"})

        history_response = client.get("/agent/session/persist-test")
        history = history_response.json()["messages"]

        assert history == [
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "Hello world"},
        ]

    def test_existing_session_history_is_loaded_into_agent(self, client):
        received_messages = {}

        async def capturing_agent_turn(messages, client):
            received_messages["value"] = messages
            yield {"event": "done", "content": "second reply"}

        client.post(
            "/agent/chat", json={"message": "first", "session_id": "continuing"}
        )

        with patch("app.main.run_agent_turn", capturing_agent_turn):
            client.post(
                "/agent/chat", json={"message": "second", "session_id": "continuing"}
            )

        messages = received_messages["value"]
        assert messages == [
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "Hello world"},
            {"role": "user", "content": "second"},
        ]

    def test_rejects_empty_message(self, client):
        response = client.post("/agent/chat", json={"message": ""})
        assert response.status_code == 422

    def test_agent_error_streams_error_event_and_does_not_persist(self, client):
        async def failing_agent_turn(messages, client):
            yield {"event": "thinking", "content": "trying"}
            raise AgentError(
                "Could not reach Ollama (ConnectError: refused). Is it running?"
            )

        with patch("app.main.run_agent_turn", failing_agent_turn):
            response = client.post(
                "/agent/chat", json={"message": "hi", "session_id": "fail-session"}
            )

        events = parse_sse(response.text)
        assert events[-1]["event"] == "error"
        assert "Could not reach Ollama" in events[-1]["message"]

        history = client.get("/agent/session/fail-session").json()["messages"]
        assert history == []


class TestSessionEndpoints:
    def test_get_session_returns_history(self, client):
        client.post("/agent/chat", json={"message": "hi", "session_id": "get-test"})
        response = client.get("/agent/session/get-test")
        assert response.status_code == 200
        assert response.json()["session_id"] == "get-test"

    def test_get_nonexistent_session_returns_empty_history(self, client):
        response = client.get("/agent/session/never-existed")
        assert response.status_code == 200
        assert response.json()["messages"] == []

    def test_delete_session_clears_history(self, client):
        client.post("/agent/chat", json={"message": "hi", "session_id": "delete-test"})
        delete_response = client.delete("/agent/session/delete-test")
        assert delete_response.json()["cleared"] is True

        history = client.get("/agent/session/delete-test").json()["messages"]
        assert history == []


class TestHealthEndpoint:
    def test_health_reports_both_dependencies(self, client):
        response = client.get("/health")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "healthy"
        assert "ollama_reachable" in body
        assert "redis_reachable" in body

    def test_health_reports_redis_reachable_when_working(self, client):
        response = client.get("/health")
        assert response.json()["redis_reachable"] is True

    def test_health_reports_ollama_unreachable_when_connection_fails(self, client):
        import httpx

        client.app.state.http_client.get = AsyncMock(
            side_effect=httpx.ConnectError(
                "Connection refused", request=httpx.Request("GET", "http://fake")
            )
        )
        response = client.get("/health")
        assert response.json()["ollama_reachable"] is False

    def test_health_reports_ollama_reachable_when_connection_succeeds(self, client):
        fake_response = AsyncMock()
        fake_response.raise_for_status = lambda: None
        client.app.state.http_client.get = AsyncMock(return_value=fake_response)

        response = client.get("/health")
        assert response.json()["ollama_reachable"] is True
