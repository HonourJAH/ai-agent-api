import json
import os
from typing import AsyncGenerator

import httpx

from app.services.tools.calculator import calculate, CalculatorError
from app.services.tools.web_search import search, WebSearchConfigError
from app.services.tools.code_executor import execute_code, CodeExecutionError

OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_AGENT_MODEL = os.getenv("OLLAMA_AGENT_MODEL", "qwen3:4b")

REQUEST_TIMEOUT_SECONDS = 300.0
OLLAMA_KEEP_ALIVE = "10m"

MAX_TOOL_ITERATIONS = 5

SYSTEM_PROMPT = (
    "You are a helpful assistant with access to tools: a calculator, a web "
    "search tool, and a Python code executor. Use a tool whenever it would "
    "give a more accurate or up-to-date answer than reasoning alone — for "
    "example, use the calculator for arithmetic, web_search for current "
    "events or facts you're unsure of, and code_executor for anything "
    "requiring actual computation or data manipulation. If you don't need "
    "a tool, just answer directly."
)

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "calculator",
            "description": (
                "Evaluate a basic arithmetic expression. Supports "
                "+, -, *, /, //, %, ** and parentheses on plain numbers."
            ),
            "parameters": {
                "type": "object",
                "required": ["expression"],
                "properties": {
                    "expression": {
                        "type": "string",
                        "description": "A math expression, e.g. '47*89' or '(2+3)*4'",
                    }
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": (
                "Search the web for current information, facts, or events "
                "that may not be part of your training data."
            ),
            "parameters": {
                "type": "object",
                "required": ["query"],
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search query",
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "Number of results to return (default 5)",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "code_executor",
            "description": (
                "Execute a snippet of Python code and return its stdout/stderr. "
                "Useful for calculations, data manipulation, or anything easier "
                "to solve by writing code than reasoning about it directly. "
                "Only a small set of stdlib modules are available: math, json, "
                "random, re, datetime, statistics, itertools, collections, "
                "string, decimal, fractions. No file, network, or OS access."
            ),
            "parameters": {
                "type": "object",
                "required": ["code"],
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "The Python code to execute",
                    }
                },
            },
        },
    },
]


class AgentError(RuntimeError):
    """Raised for unrecoverable agent-loop failures — Ollama unreachable,
    an HTTP error from Ollama itself, or exceeding the tool-call iteration
    cap. Distinct from a tool itself failing, which is reported back to
    the model as a tool result instead of raised here.
    """


async def _call_tool(name: str, arguments: dict) -> str:
    """Execute a single tool call and return a string result.

    Tool-level failures (bad expression, unreachable search API, rejected
    code) are caught here and turned into a descriptive string rather than
    propagated — the model sees the failure as a tool result and can
    decide how to respond, rather than the whole request blowing up.
    """
    try:
        if name == "calculator":
            result = calculate(arguments["expression"])
            return json.dumps({"result": result})

        if name == "web_search":
            results = await search(
                arguments["query"], max_results=arguments.get("max_results", 5)
            )
            return json.dumps({"results": results})

        if name == "code_executor":
            result = await execute_code(arguments["code"])
            return json.dumps(result)

        return json.dumps({"error": f"Unknown tool: {name}"})

    except CalculatorError as exc:
        return json.dumps({"error": f"Calculator error: {exc}"})
    except WebSearchConfigError as exc:
        return json.dumps({"error": f"Web search unavailable: {exc}"})
    except httpx.HTTPError as exc:
        return json.dumps({"error": f"Web search request failed: {exc}"})
    except CodeExecutionError as exc:
        return json.dumps({"error": f"Code rejected: {exc}"})


async def _stream_chat_round(conversation: list[dict], client: httpx.AsyncClient):
    """Stream one round of /api/chat and yield (event_dict) as chunks arrive.

    Also returns, once the round is complete, the accumulated final content
    and any tool_calls that appeared — via the loop in run_agent_turn,
    since an async generator can't both yield events and return a value
    cleanly. See run_agent_turn for how this is actually consumed.
    """
    accumulated_content = ""
    pending_tool_calls = None

    async with client.stream(
        "POST",
        f"{OLLAMA_BASE_URL}/api/chat",
        json={
            "model": OLLAMA_AGENT_MODEL,
            "messages": conversation,
            "tools": TOOL_SCHEMAS,
            "stream": True,
            "keep_alive": OLLAMA_KEEP_ALIVE,
        },
        timeout=REQUEST_TIMEOUT_SECONDS,
    ) as response:
        response.raise_for_status()
        async for line in response.aiter_lines():
            if not line:
                continue
            chunk = json.loads(line)
            message = chunk.get("message", {})

            thinking_delta = message.get("thinking")
            if thinking_delta:
                yield {"event": "thinking", "content": thinking_delta}

            content_delta = message.get("content")
            if content_delta:
                accumulated_content += content_delta
                yield {"event": "token", "content": content_delta}

            tool_calls = message.get("tool_calls")
            if tool_calls:
                pending_tool_calls = tool_calls

            if chunk.get("done"):
                break

    yield {
        "event": "_round_complete",
        "content": accumulated_content,
        "tool_calls": pending_tool_calls,
    }


async def run_agent_turn(
    messages: list[dict],
    client: httpx.AsyncClient,
) -> AsyncGenerator[dict, None]:
    """Run one full agent turn: the model may call tools zero or more
    times before producing a final answer. Every round streams in real
    time — reasoning ("thinking"), the final answer ("token"), and tool
    activity are all surfaced as distinct event types as they happen.

    `messages` is the full conversation history including the new user
    message, but NOT the system prompt — that's added here. Session
    persistence (loading/saving this history) is a separate concern,
    handled by session_store.py, not this function.

    Yields events as dicts:
      {"event": "thinking", "content": "..."}      (reasoning, as it streams)
      {"event": "token", "content": "..."}          (final answer, as it streams)
      {"event": "tool_call", "tool": name, "arguments": {...}}
      {"event": "tool_result", "tool": name, "result": "..."}
      {"event": "done", "content": "full final text"}
    Raises AgentError for unrecoverable failures (Ollama unreachable,
    HTTP error, or exceeding MAX_TOOL_ITERATIONS).
    """
    conversation = [{"role": "system", "content": SYSTEM_PROMPT}] + messages

    for _ in range(MAX_TOOL_ITERATIONS):
        accumulated_content = ""
        pending_tool_calls = None

        try:
            async for event in _stream_chat_round(conversation, client):
                if event["event"] == "_round_complete":
                    accumulated_content = event["content"]
                    pending_tool_calls = event["tool_calls"]
                else:
                    yield event
        except httpx.HTTPStatusError as exc:
            raise AgentError(f"Ollama returned an error: {exc.response.status_code}")
        except httpx.RequestError as exc:
            raise AgentError(
                f"Could not reach Ollama ({type(exc).__name__}: {exc}). Is it running?"
            )

        if not pending_tool_calls:
            yield {"event": "done", "content": accumulated_content}
            return

        conversation.append(
            {
                "role": "assistant",
                "content": accumulated_content,
                "tool_calls": pending_tool_calls,
            }
        )

        for call in pending_tool_calls:
            tool_name = call["function"]["name"]
            tool_args = call["function"]["arguments"]

            yield {"event": "tool_call", "tool": tool_name, "arguments": tool_args}

            result_str = await _call_tool(tool_name, tool_args)

            yield {"event": "tool_result", "tool": tool_name, "result": result_str}

            conversation.append({"role": "tool", "content": result_str})

    raise AgentError(
        f"Exceeded maximum tool-call iterations ({MAX_TOOL_ITERATIONS}) "
        "without producing a final answer."
    )
