import json
import uuid
from contextlib import asynccontextmanager

import httpx
import redis.asyncio as redis
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse

from app.schemas import ChatRequest
from app.services.ollama_agent import run_agent_turn, AgentError, OLLAMA_BASE_URL
from app.services.session_store import (
    get_history,
    append_messages,
    clear_session,
    REDIS_URL,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # One shared httpx.AsyncClient and one shared Redis client for the
    # app's whole lifetime — same reasoning as the Multi-Modal API: avoid
    # opening a fresh connection on every single request.
    app.state.http_client = httpx.AsyncClient()
    app.state.redis_client = redis.from_url(REDIS_URL, decode_responses=True)
    yield
    await app.state.http_client.aclose()
    await app.state.redis_client.aclose()


app = FastAPI(
    title="AI Agent API",
    description="An LLM-powered agent with calculator, web search, and code execution tools",
    lifespan=lifespan,
)


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event)}\n\n"


async def _stream_agent_response(request: Request, session_id: str, user_message: str):
    history = await get_history(session_id, redis_client=request.app.state.redis_client)
    messages = history + [{"role": "user", "content": user_message}]

    yield _sse({"event": "session", "session_id": session_id})

    final_content = ""
    try:
        async for event in run_agent_turn(
            messages, client=request.app.state.http_client
        ):
            if event["event"] == "done":
                final_content = event["content"]
            yield _sse(event)
    except AgentError as exc:
        yield _sse({"event": "error", "message": str(exc)})
        return

    # Only persist once the turn actually completed successfully — a
    # failed/interrupted turn shouldn't pollute session history.
    await append_messages(
        session_id,
        [
            {"role": "user", "content": user_message},
            {"role": "assistant", "content": final_content},
        ],
        redis_client=request.app.state.redis_client,
    )


@app.post("/agent/chat")
async def agent_chat(request: Request, body: ChatRequest):
    session_id = body.session_id or str(uuid.uuid4())

    return StreamingResponse(
        _stream_agent_response(request, session_id, body.message),
        media_type="text/event-stream",
    )


@app.get("/agent/session/{session_id}")
async def get_session(request: Request, session_id: str):
    history = await get_history(session_id, redis_client=request.app.state.redis_client)
    return {"session_id": session_id, "messages": history}


@app.delete("/agent/session/{session_id}")
async def delete_session(request: Request, session_id: str):
    await clear_session(session_id, redis_client=request.app.state.redis_client)
    return {"session_id": session_id, "cleared": True}


@app.get("/health")
async def health_check(request: Request):
    ollama_reachable = True
    try:
        response = await request.app.state.http_client.get(
            f"{OLLAMA_BASE_URL}/", timeout=3.0
        )
        response.raise_for_status()
    except httpx.HTTPError:
        ollama_reachable = False

    redis_reachable = True
    try:
        await request.app.state.redis_client.ping()
    except Exception:
        redis_reachable = False

    return {
        "status": "healthy",
        "ollama_reachable": ollama_reachable,
        "redis_reachable": redis_reachable,
    }
