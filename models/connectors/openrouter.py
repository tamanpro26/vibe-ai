"""
models/connectors/openrouter.py
Connector for OpenRouter — single API that routes to 25+ free models:
Kimi K2.6, GLM-5.1, DeepSeek, Qwen3, Mistral, Phi-4, Gemma, etc.
Uses the openai SDK with a custom base_url.
"""
from __future__ import annotations

import base64
from typing import Any

from openai import AsyncOpenAI
from loguru import logger

from config.models_config import ModelDef
from config.settings import settings
from models.base import BaseModelConnector


class OpenRouterConnector(BaseModelConnector):
    """
    Routes calls through OpenRouter's OpenAI-compatible API.
    Supports text + vision models transparently.
    """

    def __init__(self, model_def: ModelDef) -> None:
        super().__init__(model_def)
        self._client = AsyncOpenAI(
            api_key=settings.openrouter_api_key,
            base_url=settings.openrouter_base_url,
            default_headers={
                "HTTP-Referer": "https://vibe-ai.local",
                "X-Title": "VibeAI System",
            },
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

        # System message
        if system:
            messages.append({"role": "system", "content": system})

        # User message — text only or multimodal
        if images and self.supports("vision"):
            content: list[dict] = []
            for img in images:
                if img.startswith("http"):
                    content.append({
                        "type": "image_url",
                        "image_url": {"url": img},
                    })
                else:
                    content.append({
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{img}"},
                    })
            content.append({"type": "text", "text": prompt})
            messages.append({"role": "user", "content": content})
        else:
            messages.append({"role": "user", "content": prompt})

        response = await self._client.chat.completions.create(
            model=self.api_model,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            **{k: v for k, v in kwargs.items() if k not in ("stream", "task_type")},
        )

        return response.choices[0].message.content or ""
    async def _call_with_tools(self, messages, tools, max_tokens, temperature, **kwargs) -> dict:
        """OpenRouter tool calling for models that support it."""
        import json
        system = next((m["content"] for m in messages if m["role"] == "system"), "")
        try:
            resp = await self._client.chat.completions.create(
                model=self.api_model, messages=messages, tools=tools,
                tool_choice="auto", max_tokens=min(max_tokens, 32_768), temperature=temperature,
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
        except Exception:
            # Model doesn't support tools — fall back to text-only, preserving system prompt
            last = next((m["content"] for m in reversed(messages) if m["role"] == "user"), "")
            return {"type": "text", "content": await self._call(str(last), system, [], max_tokens, temperature)}

