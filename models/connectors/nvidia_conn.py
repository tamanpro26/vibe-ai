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
        # timeout: found in code review (2026-07-13) that no connector set one,
        # relying on SDK defaults (~600s) and blocking the fallback chain on a hang.
        self._client = AsyncOpenAI(
            api_key=settings.nvidia_api_key or "not-configured", base_url=self.BASE_URL,
            timeout=settings.default_timeout_ms / 1000,
        )

    async def _call(self, prompt, system, images, max_tokens, temperature, **kwargs) -> str:
        if not settings.nvidia_api_key:
            raise RuntimeError("NVIDIA_API_KEY not set — free at https://build.nvidia.com")
        messages = []
        if system: messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        # extra_body carries chat_template_kwargs for NIM DeepSeek thinking;
        # request_timeout gives a silently-reasoning model room past the 30s
        # client default. Both are model-configured (see ModelDef / Feature 1).
        create_kw: dict[str, Any] = dict(
            model=self.api_model, messages=messages,
            max_tokens=min(max_tokens, 8_192), temperature=temperature,
        )
        eb = self._merged_extra_body()
        if eb is not None:            create_kw["extra_body"] = eb
        if self._request_timeout():  create_kw["timeout"] = self._request_timeout()
        resp = await self._client.chat.completions.create(**create_kw)
        self._note_completion(resp)
        return resp.choices[0].message.content or ""

    async def _call_with_tools(self, messages, tools, max_tokens, temperature, **kwargs) -> dict:
        if not settings.nvidia_api_key:
            raise RuntimeError("NVIDIA_API_KEY not set")
        create_kw: dict[str, Any] = dict(
            model=self.api_model, messages=messages, tools=tools,
            tool_choice="auto", max_tokens=min(max_tokens, 8_192), temperature=temperature,
        )
        eb = self._merged_extra_body()
        if eb is not None:            create_kw["extra_body"] = eb
        if self._request_timeout():  create_kw["timeout"] = self._request_timeout()
        resp = await self._client.chat.completions.create(**create_kw)
        self._note_completion(resp)
        msg = resp.choices[0].message
        if msg.tool_calls:
            calls = [
                {"id": tc.id, "name": tc.function.name,
                 "args": json.loads(tc.function.arguments or "{}")}
                for tc in msg.tool_calls
            ]
            return {"type": "tool_calls", "tool_calls": calls}
        return {"type": "text", "content": msg.content or ""}
