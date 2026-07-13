"""
models/connectors/ollama.py
Connector for locally-running Ollama models.
Uses the OpenAI-compatible endpoint Ollama exposes at /v1.
Ideal for edge routing (Qwen3.5-0.8B) and privacy-sensitive tasks.
"""
from __future__ import annotations

import time
from typing import Any

from openai import AsyncOpenAI
from loguru import logger

from config.models_config import ModelDef
from config.settings import settings
from models.base import BaseModelConnector

_last_check_at: float = 0.0
_last_reachable: bool = False
_CHECK_INTERVAL_S = 60.0


async def is_reachable() -> bool:
    """
    Cheap, cached reachability probe for the local Ollama server. Most
    installs don't have Ollama running at all — settings.ollama_base_url has
    a default value regardless, so its mere presence can't be used as a
    signal — this actually hits the server with a short timeout.

    Cached for _CHECK_INTERVAL_S so a caller that checks this on every
    request (e.g. a router classifying every incoming task) doesn't pay a
    network round-trip each time, while a user starting Ollama mid-session
    is picked up within a minute rather than needing a restart.
    """
    global _last_check_at, _last_reachable
    now = time.monotonic()
    if now - _last_check_at < _CHECK_INTERVAL_S:
        return _last_reachable
    _last_check_at = now
    try:
        import httpx
        tags_url = settings.ollama_base_url.rsplit("/v1", 1)[0] + "/api/tags"
        async with httpx.AsyncClient(timeout=1.5) as client:
            resp = await client.get(tags_url)
            _last_reachable = resp.status_code == 200
    except Exception:
        _last_reachable = False
    return _last_reachable


class OllamaConnector(BaseModelConnector):
    """
    Connects to a local Ollama server.
    No API key required — Ollama uses a dummy key.
    """

    def __init__(self, model_def: ModelDef) -> None:
        super().__init__(model_def)
        self._client = AsyncOpenAI(
            api_key="ollama",   # Ollama ignores the key value
            base_url=settings.ollama_base_url,
        )

    async def _call(
        self,
        prompt: str,
        system: str,
        images: list[str],
        max_tokens: int,
        temperature: float,
        **kwargs: Any,
    ) -> str:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        # qwen3.5 (the only model registered on this connector) is a
        # "thinking" model — verified live: a max_tokens=150 classification
        # call returned 0 chars because the hidden reasoning phase consumed
        # the whole budget through Ollama's OpenAI-compat endpoint before any
        # visible content was emitted (raising to 500 produced real output).
        # Same failure mode already fixed for Qwen3 on Groq
        # (groq_conn.py's reasoning_format=hidden + token floor) — same fix
        # here since Ollama's /v1 shim doesn't expose that param.
        if "qwen3" in self.api_model.lower():
            max_tokens = max(max_tokens, 600)

        try:
            response = await self._client.chat.completions.create(
                model=self.api_model,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
                # Ollama's default keep_alive unloads an idle model after 5
                # minutes, which brings back the ~10s cold-start reload cost
                # (verified live, 2026-07-11 -- see vibemind/fastpath.py's
                # tier-2 intent classifier, the main caller this matters
                # for) on the next call after any normal pause in
                # conversation. Not an OpenAI API field, so it has to go
                # through extra_body -- Ollama's OpenAI-compat shim reads it
                # from there.
                extra_body={"keep_alive": "30m"},
            )
            return response.choices[0].message.content or ""

        except Exception as exc:
            logger.warning(
                f"[ollama] {self.model_id} unreachable ({exc}). "
                "Is Ollama running? Start with: ollama serve"
            )
            raise

    async def _call_with_tools(
        self,
        messages:    list[dict],
        tools:       list[dict],
        max_tokens:  int,
        temperature: float,
        **kwargs: Any,
    ) -> dict:
        """
        Native tool calling over Ollama's OpenAI-compatible endpoint. Added
        specifically so local Ollama models can act as a genuine last-resort
        tier for core/model_escalation.py -- without this override, the base
        class's default _call_with_tools() silently drops every tool and
        returns plain text, which means an escalated agent-loop run would
        never actually write a file. Mirrors groq_conn.py's parsing (minus
        Groq's text-format-fallback recovery, which is a Groq-specific quirk).

        Not every locally-pulled model supports function calling -- if this
        raises, the caller (agent_loop.py's escalation loop) just moves on
        to the next candidate, same as any other tier failure.
        """
        if "qwen3" in self.api_model.lower():
            max_tokens = max(max_tokens, 600)
        response = await self._client.chat.completions.create(
            model=self.api_model,
            messages=messages,
            tools=tools,
            tool_choice="auto",
            max_tokens=max_tokens,
            temperature=temperature,
        )
        msg = response.choices[0].message
        if msg.tool_calls:
            import json
            calls = [
                {
                    "id":   tc.id,
                    "name": tc.function.name,
                    "args": json.loads(tc.function.arguments or "{}"),
                }
                for tc in msg.tool_calls
            ]
            result: dict[str, Any] = {"type": "tool_calls", "tool_calls": calls}
            if msg.content:
                result["content"] = msg.content
            return result
        return {"type": "text", "content": msg.content or ""}
