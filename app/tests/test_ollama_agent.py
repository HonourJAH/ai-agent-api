import httpx
import pytest

from app.services.ollama_agent import run_agent_turn, AgentError, MAX_TOOL_ITERATIONS


async def _collect(generator):
    events = []
    async for event in generator:
        events.append(event)
    return events


class TestSingleRoundNoTools:
    async def test_direct_answer_streams_content_and_done(self, make_fake_ollama_client):
        client = make_fake_ollama_client(
            rounds=[
                [
                    {"message": {"role": "assistant", "content": "", "thinking": "Easy one."}, "done": False},
                    {"message": {"role": "assistant", "content": "Hello"}, "done": False},
                    {"message": {"role": "assistant", "content": " there!"}, "done": False},
                    {"message": {"role": "assistant", "content": ""}, "done": True},
                ]
            ]
        )
        events = await _collect(
            run_agent_turn([{"role": "user", "content": "hi"}], client=client)
        )

        assert {"event": "thinking", "content": "Easy one."} in events
        assert {"event": "token", "content": "Hello"} in events
        assert {"event": "token", "content": " there!"} in events
        assert events[-1] == {"event": "done", "content": "Hello there!"}
        # no tool activity at all for a direct-answer turn
        assert not any(e["event"] in ("tool_call", "tool_result") for e in events)


class TestToolCallingLoop:
    async def test_single_tool_call_then_final_answer(self, make_fake_ollama_client):
        client = make_fake_ollama_client(
            rounds=[
                [
                    {"message": {"role": "assistant", "content": "", "tool_calls": [
                        {"id": "call_1", "function": {"name": "calculator", "arguments": {"expression": "47*89"}}}
                    ]}, "done": False},
                    {"message": {"role": "assistant", "content": ""}, "done": True},
                ],
                [
                    {"message": {"role": "assistant", "content": "It is 4183."}, "done": False},
                    {"message": {"role": "assistant", "content": ""}, "done": True},
                ],
            ]
        )
        events = await _collect(
            run_agent_turn([{"role": "user", "content": "what is 47*89"}], client=client)
        )

        tool_call_events = [e for e in events if e["event"] == "tool_call"]
        tool_result_events = [e for e in events if e["event"] == "tool_result"]
        assert len(tool_call_events) == 1
        assert tool_call_events[0]["tool"] == "calculator"
        assert tool_call_events[0]["arguments"] == {"expression": "47*89"}

        # the real calculator actually ran — result is genuinely 4183
        assert '"result": 4183' in tool_result_events[0]["result"]

        assert events[-1] == {"event": "done", "content": "It is 4183."}

    async def test_multiple_tool_calls_in_one_round(self, make_fake_ollama_client):
        client = make_fake_ollama_client(
            rounds=[
                [
                    {"message": {"role": "assistant", "content": "", "tool_calls": [
                        {"id": "call_1", "function": {"name": "calculator", "arguments": {"expression": "2+2"}}},
                        {"id": "call_2", "function": {"name": "calculator", "arguments": {"expression": "3+3"}}},
                    ]}, "done": False},
                    {"message": {"role": "assistant", "content": ""}, "done": True},
                ],
                [
                    {"message": {"role": "assistant", "content": "4 and 6."}, "done": False},
                    {"message": {"role": "assistant", "content": ""}, "done": True},
                ],
            ]
        )
        events = await _collect(
            run_agent_turn([{"role": "user", "content": "compute two things"}], client=client)
        )

        tool_calls = [e for e in events if e["event"] == "tool_call"]
        assert len(tool_calls) == 2

    async def test_unknown_tool_name_reported_not_crashed(self, make_fake_ollama_client):
        client = make_fake_ollama_client(
            rounds=[
                [
                    {"message": {"role": "assistant", "content": "", "tool_calls": [
                        {"id": "call_1", "function": {"name": "nonexistent_tool", "arguments": {}}}
                    ]}, "done": False},
                    {"message": {"role": "assistant", "content": ""}, "done": True},
                ],
                [
                    {"message": {"role": "assistant", "content": "Sorry, I can't do that."}, "done": False},
                    {"message": {"role": "assistant", "content": ""}, "done": True},
                ],
            ]
        )
        events = await _collect(
            run_agent_turn([{"role": "user", "content": "do the impossible"}], client=client)
        )

        tool_result = next(e for e in events if e["event"] == "tool_result")
        assert "Unknown tool" in tool_result["result"]

    async def test_failing_tool_call_reported_as_result_not_raised(self, make_fake_ollama_client):
        """A bad calculator expression should surface as a tool result
        the model can react to, not crash the whole agent turn.
        """
        client = make_fake_ollama_client(
            rounds=[
                [
                    {"message": {"role": "assistant", "content": "", "tool_calls": [
                        {"id": "call_1", "function": {"name": "calculator", "arguments": {"expression": "1/0"}}}
                    ]}, "done": False},
                    {"message": {"role": "assistant", "content": ""}, "done": True},
                ],
                [
                    {"message": {"role": "assistant", "content": "Division by zero isn't possible."}, "done": False},
                    {"message": {"role": "assistant", "content": ""}, "done": True},
                ],
            ]
        )
        events = await _collect(
            run_agent_turn([{"role": "user", "content": "what is 1/0"}], client=client)
        )

        tool_result = next(e for e in events if e["event"] == "tool_result")
        assert "error" in tool_result["result"].lower()
        assert events[-1]["event"] == "done"


class TestIterationCap:
    async def test_exceeding_max_iterations_raises_agent_error(self, make_fake_ollama_client):
        # Every round calls a tool, never producing a final answer —
        # should hit MAX_TOOL_ITERATIONS and raise, not loop forever.
        tool_call_round = [
            {"message": {"role": "assistant", "content": "", "tool_calls": [
                {"id": "call_1", "function": {"name": "calculator", "arguments": {"expression": "1+1"}}}
            ]}, "done": False},
            {"message": {"role": "assistant", "content": ""}, "done": True},
        ]
        client = make_fake_ollama_client(
            rounds=[tool_call_round for _ in range(MAX_TOOL_ITERATIONS + 1)]
        )

        with pytest.raises(AgentError, match="maximum tool-call iterations"):
            await _collect(
                run_agent_turn([{"role": "user", "content": "loop forever"}], client=client)
            )


class TestErrorHandling:
    async def test_ollama_unreachable_raises_agent_error(self):
        from unittest.mock import MagicMock

        class FailingContextManager:
            async def __aenter__(self):
                raise httpx.ConnectError("Connection refused", request=httpx.Request("POST", "http://fake"))
            async def __aexit__(self, *args):
                return False

        client = MagicMock()
        client.stream = lambda *a, **kw: FailingContextManager()

        with pytest.raises(AgentError, match="Could not reach Ollama"):
            await _collect(
                run_agent_turn([{"role": "user", "content": "hi"}], client=client)
            )
