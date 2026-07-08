from app.services.session_store import get_history, append_messages, clear_session


class TestSessionStore:
    async def test_empty_session_returns_empty_list(self, fake_redis):
        history = await get_history("nonexistent", redis_client=fake_redis)
        assert history == []

    async def test_append_then_get_round_trips(self, fake_redis):
        await append_messages(
            "session-a",
            [
                {"role": "user", "content": "hello"},
                {"role": "assistant", "content": "hi there"},
            ],
            redis_client=fake_redis,
        )
        history = await get_history("session-a", redis_client=fake_redis)
        assert history == [
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi there"},
        ]

    async def test_multiple_turns_accumulate_in_order(self, fake_redis):
        await append_messages(
            "session-b",
            [{"role": "user", "content": "turn 1"}, {"role": "assistant", "content": "reply 1"}],
            redis_client=fake_redis,
        )
        await append_messages(
            "session-b",
            [{"role": "user", "content": "turn 2"}, {"role": "assistant", "content": "reply 2"}],
            redis_client=fake_redis,
        )
        history = await get_history("session-b", redis_client=fake_redis)
        assert [m["content"] for m in history] == [
            "turn 1", "reply 1", "turn 2", "reply 2",
        ]

    async def test_sessions_are_isolated(self, fake_redis):
        await append_messages(
            "session-c", [{"role": "user", "content": "only in c"}], redis_client=fake_redis
        )
        history_c = await get_history("session-c", redis_client=fake_redis)
        history_d = await get_history("session-d", redis_client=fake_redis)
        assert len(history_c) == 1
        assert history_d == []

    async def test_ttl_is_set_on_append(self, fake_redis):
        await append_messages(
            "session-e", [{"role": "user", "content": "hi"}], redis_client=fake_redis
        )
        ttl = await fake_redis.ttl("session:session-e:messages")
        assert ttl > 0

    async def test_clear_session_removes_history(self, fake_redis):
        await append_messages(
            "session-f", [{"role": "user", "content": "hi"}], redis_client=fake_redis
        )
        await clear_session("session-f", redis_client=fake_redis)
        history = await get_history("session-f", redis_client=fake_redis)
        assert history == []

    async def test_append_with_empty_list_is_a_noop(self, fake_redis):
        await append_messages("session-g", [], redis_client=fake_redis)
        history = await get_history("session-g", redis_client=fake_redis)
        assert history == []
