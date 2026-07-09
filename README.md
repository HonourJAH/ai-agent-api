# AI Agent API

An LLM-powered agent with real tool use — a calculator, live web search, and sandboxed Python code execution — exposed as a streaming API with persistent, multi-turn session memory. Built with FastAPI, Ollama (local inference, no external LLM API), Redis, and Tavily.

---

## How It Works

```
POST /agent/chat              →  send a message, get back an SSE stream:
                                    session   → the session_id for this conversation
                                    thinking  → the model's live reasoning trace
                                    tool_call → a tool the model decided to call
                                    tool_result → what that tool actually returned
                                    token     → the final answer, streamed live
                                    done      → the complete final answer
                                    error     → an unrecoverable failure

GET    /agent/session/{id}    →  inspect a session's persisted history
DELETE /agent/session/{id}    →  clear a session
GET    /health                →  liveness + real Ollama/Redis reachability
```

---

## Table of Contents

- [Why Stream Reasoning, Not Just the Answer?](#why-stream-reasoning-not-just-the-answer)
- [Tools](#tools)
- [Security & Sandboxing](#security--sandboxing)
- [Project Structure](#project-structure)
- [Requirements](#requirements)
- [Getting Started](#getting-started)
- [Environment Variables](#environment-variables)
- [Running Tests](#running-tests)
- [API Endpoints](#api-endpoints)
- [Request & Response Schemas](#request--response-schemas)
- [Example Usage](#example-usage)
- [Docker](#docker)

---

## Why Stream Reasoning, Not Just the Answer?

Local, CPU-only tool-calling models are slow — a single tool-calling round trip on this project's reference hardware took over two minutes. A naive implementation would leave the client staring at a blank screen for that entire time, then dump a wall of text at the end.

Instead, every stage of the agent's decision-making streams live:

```
thinking    → the model's raw reasoning, as it's generated
tool_call   → the exact tool + arguments the model decided on
tool_result → what actually came back from running it for real
token       → the final answer, streamed word by word
```

This required confirming, directly against the running model, that tool_calls arrives as one complete object even when the rest of the response streams token-by-token — verified before writing any of the parsing logic, not assumed.

A single agent turn can involve several tool round-trips before producing a final answer — each one re-enters this same loop, capped at a hard iteration limit so a confused model can't loop forever.

---

## Tools

| Tool | Description | External dependency |
|---|---|---|
| `calculator` | Arithmetic expressions (`+ - * / // % **`, parentheses) | None — pure Python, no `eval()` |
| `web_search` | Live web search for current events/facts | Tavily API |
| `code_executor` | Runs a Python snippet and returns stdout/stderr | None — sandboxed subprocess |

The model decides on its own, per-message, whether a tool is needed at all — tool descriptions are the entire interface it reasons against; nothing is hardcoded about when to call what.

---

## Security & Sandboxing

`code_executor` runs model-influenced code, which is a genuine attack surface if handled naively (e.g. a plain `eval()`). This project layers five independent defenses, each closing a gap the others don't:

| Layer | Defends against |
|---|---|
| AST pre-scan | Dunder attribute access (`().__class__.__base__.__subclasses__()`) — rejected before any code runs, since attribute access isn't gated by `__builtins__` at all in Python |
| Restricted `__builtins__` | `open`, `eval`, `exec`, `compile`, `input`, `getattr`, etc. simply don't exist in the executed environment |
| Restricted `__import__` | Only 11 pure-computation stdlib modules are importable (`math`, `json`, `statistics`, ...) — no filesystem, network, process, or OS access |
| OS resource limits | CPU-time and memory (`resource.setrlimit`) caps, sized to avoid swap-thrashing on constrained hardware |
| Subprocess isolation + timeout | A genuinely separate OS process, killed outright on a hard wall-clock timeout |

> **Note:** this is deliberately scoped for safely demonstrating a tool-use pattern, not for running fully untrusted code at production security posture — there's no container or VM-level isolation beyond a plain subprocess + rlimits. A real production system executing arbitrary untrusted code would want gVisor, Firecracker, or similar.

The `calculator` tool uses the same AST-based philosophy — it never calls `eval()` at all, only interpreting a small whitelist of arithmetic AST node types.

---

## Project Structure

```
ai-agent-api/
├── .github/
│   └── workflows/
│       └── ci.yml                    — GitHub Actions CI pipeline
├── app/
│   ├── main.py                        — FastAPI app, /agent/chat SSE route
│   ├── schemas.py                     — Request schema
│   └── services/
│       ├── ollama_agent.py            — the tool-calling loop itself
│       ├── session_store.py           — Redis-backed conversation history
│       └── tools/
│           ├── calculator.py          — AST-based safe arithmetic
│           ├── web_search.py          — Tavily wrapper
│           ├── code_executor.py       — sandboxed code execution
│           └── _runner_template.py    — the restricted-builtins runner
├── tests/
│   ├── conftest.py                    — Shared fixtures (fake NDJSON streams, fakeredis)
│   ├── test_calculator.py
│   ├── test_web_search.py
│   ├── test_code_executor.py
│   ├── test_ollama_agent.py
│   ├── test_session_store.py
│   └── test_main.py
├── .dockerignore
├── .env.example
├── .gitignore
├── docker-compose.yml
├── Dockerfile
├── pytest.ini
├── README.md
└── requirements.txt
```

---

## Requirements

- Python 3.12+
- [Ollama](https://ollama.com) installed and running **on your host machine** (not containerized — see [Docker](#docker) for why)
- A free [Tavily](https://tavily.com) API key
- Docker and Docker Compose (Redis is containerized; the API itself can run either way)

---

## Getting Started

### 1. Install Ollama

**Linux / macOS:**

```bash
curl -fsSL https://ollama.com/install.sh | sh
```

**Windows:** download the installer from [ollama.com/download](https://ollama.com/download).

Confirm it's running:

```bash
curl http://localhost:11434
```

### 2. Pull the agent model

```bash
ollama pull qwen3:4b
```

`qwen3:4b` was chosen specifically for reliable tool-calling on modest, CPU-only hardware — see [Docker](#docker) for the memory reasoning behind this choice.

### 3. Get a Tavily API key

Sign up free at [tavily.com](https://tavily.com) — no card required.

### 4. Clone the repository

```bash
git clone https://github.com/HonourJAH/ai-agent-api.git
cd ai-agent-api
```

### 5. Create and activate a virtual environment

```bash
python3 -m venv venv
```

**Linux / macOS:**

```bash
source venv/bin/activate
```

**Windows (PowerShell):**

```powershell
venv\Scripts\Activate.ps1
```

> If PowerShell blocks the script: `Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser`

### 6. Install dependencies

```bash
pip install -r requirements.txt
```

### 7. Set up environment variables

```bash
cp .env.example .env
```

Edit `.env` with your real Tavily key.

### 8. Start Redis

```bash
docker run -d --name redis -p 6379:6379 redis:7-alpine
```

### 9. Start the API server

```bash
export TAVILY_API_KEY=your-key-here   # or `source .env` if you prefer
uvicorn app.main:app --reload
```

API available at `http://localhost:8000`

---

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Where the API reaches Ollama |
| `OLLAMA_AGENT_MODEL` | `qwen3:4b` | Model used for the agent loop |
| `TAVILY_API_KEY` | — | **Required.** Get one free at tavily.com |
| `REDIS_URL` | `redis://localhost:6379/0` | Session store connection |
| `SESSION_TTL_SECONDS` | `86400` (24h) | How long an inactive session's history persists |

> **Important:** In Docker, `OLLAMA_BASE_URL` must be `http://host.docker.internal:11434` (Ollama runs on the host, not in a container) and `REDIS_URL` must be `redis://redis:6379/0` (the Compose service name, not `localhost`). `docker-compose.yml` already sets both correctly.

---

## Running Tests

Nothing here requires a live Ollama, Tavily, or Redis — every external call is dependency-injected and mocked, including a simulated NDJSON stream matching Ollama's real streaming shape (captured from an actual `qwen3:4b` session).

```bash
pip install pytest pytest-asyncio fakeredis
pytest -v
```

67 tests across 6 files — arithmetic safety, sandbox escape prevention, the full multi-round tool-calling loop, session persistence, and every SSE event type.

---

## API Endpoints

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/agent/chat` | Send a message, receive an SSE stream of agent activity + final answer |
| `GET` | `/agent/session/{session_id}` | Inspect a session's persisted history |
| `DELETE` | `/agent/session/{session_id}` | Clear a session |
| `GET` | `/health` | Liveness + real Ollama/Redis reachability |

---

## Request & Response Schemas

### `POST /agent/chat`

**Request body:**

```json
{
  "message": "What is 47 times 89?",
  "session_id": "optional — omit to start a new session"
}
```

**Response** — `text/event-stream`, one JSON object per `data:` line:

```
data: {"event": "session", "session_id": "687b0a1b-..."}

data: {"event": "thinking", "content": "Okay, the user is asking..."}

data: {"event": "tool_call", "tool": "calculator", "arguments": {"expression": "47*89"}}

data: {"event": "tool_result", "tool": "calculator", "result": "{\"result\": 4183}"}

data: {"event": "token", "content": "The"}

data: {"event": "token", "content": " result is 4183."}

data: {"event": "done", "content": "The result is 4183."}
```

On failure, a single `error` event replaces the rest of the stream, and the turn is **not** persisted to session history:

```
data: {"event": "error", "message": "Could not reach Ollama (ConnectError: ...). Is it running?"}
```

---

### `GET /agent/session/{session_id}`

```json
{
  "session_id": "687b0a1b-...",
  "messages": [
    {"role": "user", "content": "What is 47 times 89?"},
    {"role": "assistant", "content": "The result is 4183."}
  ]
}
```

Only user messages and final assistant answers are persisted — intermediate tool-call scaffolding from within a turn is never stored, since its effect is already reflected in that turn's final answer.

---

### `GET /health`

```json
{
  "status": "healthy",
  "ollama_reachable": true,
  "redis_reachable": true
}
```

---

## Example Usage

### A direct question (no tools needed)

```bash
curl -N -X POST http://localhost:8000/agent/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "What is the capital of France?"}'
```

### A question requiring the calculator

```bash
curl -N -X POST http://localhost:8000/agent/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "What is 47 times 89?"}'
```

### Continuing a conversation

```bash
curl -N -X POST http://localhost:8000/agent/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "What about 12 times 12?", "session_id": "687b0a1b-..."}'
```

### Asking it to write and run code

```bash
curl -N -X POST http://localhost:8000/agent/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "Compute the standard deviation of [4, 8, 15, 16, 23, 42] using Python"}'
```

(`-N` disables curl's output buffering so SSE events appear live instead of all at once at the end.)

---

## Docker

### Why Ollama isn't in `docker-compose.yml`, but Redis is

Ollama runs on the host machine, with models already pulled and configured — same reasoning as this author's Multi-Modal API project. `qwen3:4b` was chosen specifically over larger tool-calling models after this machine hit real memory-swap thrashing during earlier local testing; the model size is a deliberate hardware-fit decision, not an arbitrary default.

Redis, on the other hand, has no such constraint — it's genuinely lightweight and gains nothing from living on the host, so it's a normal containerized service here, matching this author's MLOps Pipeline API pattern.

Reaching host-based Ollama from the API container requires:

1. Ollama bound to `0.0.0.0`, not `127.0.0.1` (Linux, via systemd):

   ```bash
   sudo systemctl edit ollama
   ```
   ```ini
   [Service]
   Environment="OLLAMA_HOST=0.0.0.0:11434"
   ```
   ```bash
   sudo systemctl daemon-reload && sudo systemctl restart ollama
   ```

2. `extra_hosts: - "host.docker.internal:host-gateway"` in `docker-compose.yml` (already included) — required on Linux; automatic on Docker Desktop (Mac/Windows).

### Run with Docker Compose

```bash
cp .env.example .env   # then edit in your real TAVILY_API_KEY
docker compose up --build
```

### Stop

```bash
docker compose down
```

Use `docker compose down -v` only when you intentionally want to wipe Redis's persisted sessions — it deletes the named volume.

### A note on CI

CI runs the full mocked test suite, then boots the real two-container stack and genuinely verifies Redis connectivity via `/health`. It does **not** verify Ollama connectivity — Ollama runs on this project's host machine and isn't available on a GitHub-hosted runner. `ollama_reachable: false` in that specific check is expected, not a failure.

---

## License

MIT
