"""
models/connectors/mistral_conn.py
Mistral La Plateforme — OpenAI-compatible. Sign up: https://console.mistral.ai

HONEST LIMITS: the free "Experiment" tier gives ~1B tokens/month, but
Mistral's own ToS scopes this to evaluation/prototyping, NOT production
traffic, and they no longer publish exact rate limits (check your own
Admin Console). Because of that ToS restriction, this connector/model is
registered for MANUAL /model selection only — it is deliberately NOT wired
into the automatic agent fallback chain in core/agent_loop.py.
"""
from __future__ import annotations
import json
from typing import Any
from openai import AsyncOpenAI
from config.models_config import ModelDef
from config.settings import settings
from models.base import BaseModelConnector


class MistralConnector(BaseModelConnector):
    BASE_URL = "https://api.mistral.ai/v1"

    def __init__(self, model_def: ModelDef) -> None:
        super().__init__(model_def)
        # timeout: found in code review (2026-07-13) that no connector set one,
        # relying on SDK defaults (~600s) and blocking the fallback chain on a hang.
        self._client = AsyncOpenAI(
            api_key=settings.mistral_api_key or "not-configured", base_url=self.BASE_URL,
            timeout=settings.default_timeout_ms / 1000,
        )

    async def _call(self, prompt, system, images, max_tokens, temperature, **kwargs) -> str:
        if not settings.mistral_api_key:
            raise RuntimeError("MISTRAL_API_KEY not set — free at https://console.mistral.ai")
        messages = []
        if system: messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        resp = await self._client.chat.completions.create(
            model=self.api_model, messages=messages,
            max_tokens=min(max_tokens, 16_384), temperature=temperature,
        )
        return resp.choices[0].message.content or ""

    async def _call_with_tools(self, messages, tools, max_tokens, temperature, **kwargs) -> dict:
        if not settings.mistral_api_key:
            raise RuntimeError("MISTRAL_API_KEY not set")
        resp = await self._client.chat.completions.create(
            model=self.api_model, messages=messages, tools=tools,
            tool_choice="auto", max_tokens=min(max_tokens, 16_384), temperature=temperature,
        )
        msg = resp.choices[0].message
        if msg.tool_calls:
            calls = [
                {"id": tc.id, "name": tc.function.name,
                 "args": json.loads(tc.function.arguments or "{}")}
                for tc in msg.tool_calls
            ]
            return {"type": "tool_calls", "tool_calls": calls}
        return {"type": "text", "content": msg.content or ""}
