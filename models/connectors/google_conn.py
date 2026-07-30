"""
models/connectors/google_conn.py
Google AI Studio — Gemini 2.5 Flash, free, 1,500 req/day, tool calling + extended thinking.
Sign up: https://aistudio.google.com/apikey
"""
from __future__ import annotations
import json
from typing import Any
from openai import AsyncOpenAI
from loguru import logger
from config.models_config import ModelDef
from config.settings import settings
from models.base import BaseModelConnector


class GoogleConnector(BaseModelConnector):
    BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"

    def __init__(self, model_def: ModelDef) -> None:
        super().__init__(model_def)
        # timeout: found in code review (2026-07-13) that no connector set one,
        # relying on SDK defaults (~600s) and blocking the fallback chain on a hang.
        self._client = AsyncOpenAI(
            api_key=settings.gemini_api_key or "not-configured", base_url=self.BASE_URL,
            timeout=settings.default_timeout_ms / 1000,
        )

    def _build_messages(self, prompt, system, images):
        msgs = []
        if system: msgs.append({"role": "system", "content": system})
        if images and self.supports("vision"):
            content = [{"type": "text", "text": prompt}]
            for img in images:
                url = img if img.startswith("http") else f"data:image/jpeg;base64,{img}"
                content.append({"type": "image_url", "image_url": {"url": url}})
            msgs.append({"role": "user", "content": content})
        else:
            msgs.append({"role": "user", "content": prompt})
        return msgs

    async def _call(self, prompt, system, images, max_tokens, temperature, **kwargs) -> str:
        if not settings.gemini_api_key:
            raise RuntimeError("GEMINI_API_KEY not set — free at https://aistudio.google.com/apikey")
        messages = self._build_messages(prompt, system, images)
        # Gemini 2.5 recommends temperature=1.0 for best results
        eff_temp = temperature if "2.5" not in self.api_model else max(temperature, 1.0)
        resp = await self._client.chat.completions.create(
            model=self.api_model, messages=messages,
            max_tokens=min(max_tokens, 65_536), temperature=eff_temp,
        )
        return resp.choices[0].message.content or ""

    async def _call_with_tools(self, messages, tools, max_tokens, temperature, **kwargs) -> dict:
        """Gemini native tool calling via OpenAI-compatible endpoint."""
        if not settings.gemini_api_key:
            raise RuntimeError("GEMINI_API_KEY not set")
        eff_temp = max(temperature, 1.0) if "2.5" in self.api_model else temperature
        resp = await self._client.chat.completions.create(
            model=self.api_model, messages=messages, tools=tools,
            tool_choice="auto", max_tokens=min(max_tokens, 65_536), temperature=eff_temp,
        )
        msg = resp.choices[0].message
        if msg.tool_calls:
            calls = [
                {"id": tc.id, "name": tc.function.name,
                 "args": json.loads(tc.function.arguments or "{}")}
                for tc in msg.tool_calls
            ]
            result = {"type": "tool_calls", "tool_calls": calls}
            if msg.content:
                result["content"] = msg.content
            return result
        return {"type": "text", "content": msg.content or ""}
