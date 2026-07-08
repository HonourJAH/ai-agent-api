import json
import os

import redis.asyncio as redis

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

# Sessions expire after inactivity rather than accumulating forever.
# Refreshed on every append, so an active conversation never expires
# mid-use — only genuinely abandoned sessions age out.
SESSION_TTL_SECONDS = int(os.getenv("SESSION_TTL_SECONDS", 60 * 60 * 24))


def _session_key(session_id: str) -> str:
    return f"session:{session_id}:messages"


def _get_client(redis_client: redis.Redis | None = None) -> redis.Redis:
    return redis_client or redis.from_url(REDIS_URL, decode_responses=True)


async def get_history(
    session_id: str, redis_client: redis.Redis | None = None
) -> list[dict]:
    """Return the persisted conversation history for a session, oldest
    first. Empty list if the session doesn't exist or has expired.

    Only contains {"role": "user"/"assistant", "content": "..."} pairs —
    never tool_call/tool messages from within a turn. Those are
    reconstructed fresh by the agent loop each turn and are never
    persisted, since replaying them in a later turn isn't necessary once
    that turn's final answer already reflects their effect.
    """
    client = _get_client(redis_client)
    raw_messages = await client.lrange(_session_key(session_id), 0, -1)
    return [json.loads(m) for m in raw_messages]


async def append_messages(
    session_id: str,
    messages: list[dict],
    redis_client: redis.Redis | None = None,
) -> None:
    """Append one or more messages to a session's persisted history and
    refresh its TTL. Typically called once per turn with exactly two
    messages: the user's message and the assistant's final answer.
    """
    if not messages:
        return

    client = _get_client(redis_client)
    key = _session_key(session_id)

    serialized = [json.dumps(m) for m in messages]
    await client.rpush(key, *serialized)
    await client.expire(key, SESSION_TTL_SECONDS)


async def clear_session(
    session_id: str, redis_client: redis.Redis | None = None
) -> None:
    """Delete a session's history entirely — e.g. for a 'start over' /
    DELETE endpoint.
    """
    client = _get_client(redis_client)
    await client.delete(_session_key(session_id))
