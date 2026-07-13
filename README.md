# VibeAI

A multi-provider AI orchestration system: an autonomous coding agent plus a
multi-team reasoning pipeline, built entirely on **free-tier models** with
automatic cross-provider failover.

## What it actually is (honest architecture)

- **~12 distinct free models** across 6 providers (Google Gemini Flash,
  GPT-OSS-120B on Groq/Cerebras/OpenRouter, Llama 3.3 70B, Llama 3.1 8B,
  GLM-4.7, Qwen3.6-27B, NVIDIA Nemotron, Gemma 4, LFM, Whisper, FLUX).
  Registry entries are (model × provider × role) slots — the same strong model
  intentionally serves several roles for cross-provider redundancy.
- **Agent loop** (`core/agent_loop.py`): scaffold detection with golden
  templates, build-verification gate, deterministic verifier battery
  (unstyled CSS classes, broken imports, placeholder stubs, dead images),
  reflexion critic, repetition guard with hard stop, provider-aware
  compact-context mode for tight-TPM models.
- **Mixture-of-Agents reasoning core** (`core/reasoning_core.py`) — after
  Wang et al., 2024: parallel proposers → aggregator → verifier.
- **Free Manager Council** (`manager/free_manager.py`): 5 free models
  collaborating (Plan → Draft → Critique → Refine → Polish). This is the
  **primary** manager unless a valid Anthropic key is configured.
- **Resilience layer**: per-endpoint circuit breaker that parses provider
  retry-delays, cross-provider failover, per-model TPM budgeting.

## Quick start

```bash
pip install -r requirements.txt
cp .env.example .env      # fill in your own free API keys
python main.py            # terminal agent
```

API server (localhost only by default — see SECURITY below):

```bash
uvicorn api.server:app --host 127.0.0.1 --port 8000
```

## Security notes (read before exposing anything)

- The agent **creates files and runs shell commands**. The API is therefore an
  RCE surface by design: it binds to `127.0.0.1` by default, and all mutating
  routes require `Authorization: Bearer $VIBE_API_TOKEN` when the token is set
  (and are refused on non-localhost binds without it).
- `bash`/code execution are **contained, not sandboxed** (workspace cwd,
  credential-shaped env vars stripped, destructive-pattern tripwire, timeouts).
  For untrusted input, run the whole system in a container.
- Never commit `.env`. `.env.example` contains placeholders only.

## Layout

```
core/       agent loop, MoA reasoning, verifiers, circuit breaker
models/     provider connectors + registry with cross-provider failover
teams/      prompt / brain / code / vision / design / router teams
manager/    manager pipeline + Free Council
tools/      agent tools (files, bash, git, SSH, web), code executor
api/        FastAPI server (REST + WebSocket)
cli.py      Rich terminal UI (entry: python main.py)
```

Operational history (model deprecations, discovered rate limits, why things
are the way they are) lives in [DECISIONS.md](DECISIONS.md).
