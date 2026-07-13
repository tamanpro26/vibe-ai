"""
core/model_escalation.py
Broader-pool escalation: "if the task ... cannot be done with good quality
with the assigned ais, then the manager ... can use any other models by
itself ... APIs, ollama local models etc." (user request, 2026-07-10).

This is deliberately NOT a third, disconnected fallback mechanism -- it reuses
what already exists:
  - models/registry.py's _LLM_PROVIDERS as the auto-eligible provider set.
    "mistral" stays excluded: its free tier's ToS scopes it to evaluation/
    prototyping, not production traffic, and an automatic escalation call
    IS production traffic -- that decision does not change here just because
    the caller changed.
  - config/models_config.py's MODEL_REGISTRY as the candidate pool -- no new
    provider is invented, no API is guessed at. The two documented failure
    modes this project already paid for once (fabricating an API contract,
    silently overriding a ToS-driven exclusion) are both designed out here.

Two distinct pools, because they need genuinely different capabilities:
  - agentic_candidates(): for the TOOL-CALLING agent loop (core/agent_loop.py).
    Candidates must be able to actually execute create_file/edit_file/bash --
    filtered to "code_generation"/"agentic_coding" capability. Ollama only
    qualifies now that OllamaConnector implements _call_with_tools (added
    alongside this module) -- before that it could only return prose, never
    touch a file.
  - single_shot_candidates(): for a plain generate() call (the manager's
    _run_team ESCALATE path in manager/claude_manager.py, which reviews team
    OUTPUT quality, not tool execution). Broader, since no tool-calling
    capability is required.

Ollama models beyond the one static router entry (qwen25_3b_ollama) are
discovered LIVE from the local server's /api/tags rather than guessed --
a wrong guess at a model name just fails loudly for no reason when the
server can be asked directly what is actually pulled. Discovered tags are
registered as ordinary MODEL_REGISTRY entries so every downstream mechanism
(registry.get, activity_log, circuit breaker) treats them like any other
model_id -- no parallel code path.
"""
from __future__ import annotations

import re

import httpx
from loguru import logger

from config.models_config import MODEL_REGISTRY, ModelDef
from config.settings import settings
from models.registry import _LLM_PROVIDERS

_AGENTIC_CAPS = {"code_generation", "agentic_coding"}

# Prefix for auto-discovered Ollama entries, so they're recognisable in logs
# and never collide with a hand-written model_id in models_config.py.
_DYNAMIC_OLLAMA_PREFIX = "ollama_escalation_"


def _sanitize(tag: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", tag.lower()).strip("_")


async def discover_ollama_tags() -> list[str]:
    """Model tags actually pulled on the local Ollama server, or [] if it's
    unreachable / has nothing pulled. A live query, not an assumption --
    see module docstring."""
    try:
        tags_url = settings.ollama_base_url.rsplit("/v1", 1)[0] + "/api/tags"
        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.get(tags_url)
            if resp.status_code != 200:
                return []
            data = resp.json()
            return [m["name"] for m in data.get("models", []) if m.get("name")]
    except Exception as exc:
        logger.debug(f"[escalation] ollama discovery skipped: {str(exc)[:60]}")
        return []


def register_dynamic_ollama(tag: str) -> str:
    """Idempotently ensure a MODEL_REGISTRY entry exists for a locally-pulled
    Ollama tag, returning the model_id to use. Reuses an existing static
    entry if one already points at this exact tag."""
    for mid, d in MODEL_REGISTRY.items():
        if d.provider == "ollama" and d.api_model == tag:
            return mid
    model_id = _DYNAMIC_OLLAMA_PREFIX + _sanitize(tag)
    if model_id not in MODEL_REGISTRY:
        MODEL_REGISTRY[model_id] = ModelDef(
            model_id=model_id,
            provider="ollama",
            api_model=tag,
            team="code",
            role="Escalation (local Ollama, auto-discovered)",
            context_window=8_192,
            capabilities=["code_generation", "agentic_coding"],
        )
        logger.info(f"[escalation] registered local Ollama model '{tag}' as {model_id}")
    return model_id


async def agentic_candidates(exclude: set[str]) -> list[str]:
    """Broader pool for the TOOL-CALLING agent loop: every auto-eligible
    registry model with coding capability not already tried, ordered by
    context window (a crude "more headroom" proxy -- routing_memory.py is
    what actually learns quality over time, this is just the day-one
    default order), then any OTHER locally-pulled Ollama model last --
    local models have no verified agentic tool-calling track record in this
    project yet, so they're a final resort, not a first guess."""
    pool = [
        mid for mid, d in MODEL_REGISTRY.items()
        if mid not in exclude
        and d.provider in _LLM_PROVIDERS
        and _AGENTIC_CAPS & set(d.capabilities)
    ]
    pool.sort(key=lambda mid: MODEL_REGISTRY[mid].context_window, reverse=True)

    for tag in await discover_ollama_tags():
        mid = register_dynamic_ollama(tag)
        if mid not in exclude and mid not in pool:
            pool.append(mid)
    return pool


async def single_shot_candidates(exclude: set[str]) -> list[str]:
    """Broader pool for a plain generate() call (manager quality-review
    escalation). No tool-calling requirement, so this is intentionally wider
    than agentic_candidates() -- any auto-eligible text model qualifies."""
    pool = [
        mid for mid, d in MODEL_REGISTRY.items()
        if mid not in exclude
        and d.provider in _LLM_PROVIDERS
        and "audio" not in d.capabilities
    ]
    for tag in await discover_ollama_tags():
        mid = register_dynamic_ollama(tag)
        if mid not in exclude and mid not in pool:
            pool.append(mid)
    return pool
