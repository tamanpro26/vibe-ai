"""
models/connectors/nvidia_conn.py
NVIDIA NIM catalog — OpenAI-compatible, free tier, no credit card.
Sign up: https://build.nvidia.com (generates an nvapi-... key)

HONEST LIMITS (verified via provider docs + forum posts, 2026):
  - ~40 requests/minute, SHARED ACROSS THE WHOLE KEY (not per-model) — this is
    NOT a published SLA, NVIDIA staff describe it as traffic-dependent and can
    tighten under load. Treat this as a thin, deep-fallback tier, never primary.
  - Catalog model slugs shift over time — if a model_id here starts 404ing,
    check the live catalog at https://build.nvidia.com/models.
"""
from __future__ import annotations
import json
from typing import Any
from openai import AsyncOpenAI
from config.models_config import ModelDef
from config.settings import settings
from models.base import BaseModelConnector


class NvidiaConnector(BaseModelConnector):
    BASE_URL = "https://integrate.api.nvidia.com/v1"

    def __init__(self, model_def: ModelDef) -> None:
        super().__init__(model_def)
        self._client = AsyncOpenAI(api_key=settings.nvidia_api_key or "not-configured", base_url=self.BASE_URL)

    async def _call(self, prompt, system, images, max_tokens, temperature, **kwargs) -> str:
        if not settings.nvidia_api_key:
            raise RuntimeError("NVIDIA_API_KEY not set — free at https://build.nvidia.com")
        messages = []
        if system: messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        resp = await self._client.chat.completions.create(
            model=self.api_model, messages=messages,
            max_tokens=min(max_tokens, 8_192), temperature=temperature,
        )
        return resp.choices[0].message.content or ""

    async def _call_with_tools(self, messages, tools, max_tokens, temperature, **kwargs) -> dict:
        if not settings.nvidia_api_key:
            raise RuntimeError("NVIDIA_API_KEY not set")
        resp = await self._client.chat.completions.create(
            model=self.api_model, messages=messages, tools=tools,
            tool_choice="auto", max_tokens=min(max_tokens, 8_192), temperature=temperature,
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
