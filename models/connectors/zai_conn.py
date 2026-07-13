"""
models/connectors/zai_conn.py
Z.AI (Zhipu) — OpenAI-compatible. GLM-4.7-Flash and GLM-4.5-Flash are free
outright on this platform. Sign up: https://z.ai/model-api

HONEST LIMITS (2026 trackers): reported ~1000 req/day free tier, but the
provider has revised free-tier limits twice in the past year per multiple
2026 sources — treat the number as approximate, not contractual.

Chosen as a fallback tier specifically because it's the SAME MODEL FAMILY
(GLM-4.7) as our Cerebras primary but on entirely independent infrastructure
— a Cerebras outage doesn't take this down too.
"""
from __future__ import annotations
import json
from typing import Any
from openai import AsyncOpenAI
from config.models_config import ModelDef
from config.settings import settings
from models.base import BaseModelConnector


class ZaiConnector(BaseModelConnector):
    BASE_URL = "https://api.z.ai/api/paas/v4/"

    def __init__(self, model_def: ModelDef) -> None:
        super().__init__(model_def)
        self._client = AsyncOpenAI(api_key=settings.zai_api_key or "not-configured", base_url=self.BASE_URL)

    # GLM-4.7(-Flash) "thinks compulsorily" on Z.AI's hosted API (per Z.AI's
    # own docs) and, per multiple live 2026 bug reports, the documented
    # `thinking: {"type": "disabled"}` toggle is currently a no-op on their
    # API -- it does not actually turn thinking off. Confirmed live
    # (2026-07-13): a trivial "Say hello in one word" call with
    # max_tokens=50 burned its ENTIRE budget on a hidden `reasoning_content`
    # field (185 tokens of chain-of-thought) and returned `content=""` --
    # not an error, just silently empty, because the visible answer never
    # got a token left to be written. The same call with max_tokens=2000
    # returned "Hello" correctly (reasoning_tokens=185, content came after).
    # Since disabling thinking isn't reliably possible right now, the fix is
    # to never send this model a budget too small to survive its own
    # reasoning phase -- undercutting a caller's smaller max_tokens would
    # silently produce the same empty-response failure regardless of what
    # they asked for.
    _MIN_TOKENS_FOR_THINKING = 2_000

    async def _call(self, prompt, system, images, max_tokens, temperature, **kwargs) -> str:
        if not settings.zai_api_key:
            raise RuntimeError("ZAI_API_KEY not set — free at https://z.ai/model-api")
        messages = []
        if system: messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        resp = await self._client.chat.completions.create(
            model=self.api_model, messages=messages,
            max_tokens=min(max(max_tokens, self._MIN_TOKENS_FOR_THINKING), 16_384),
            temperature=temperature,
        )
        return resp.choices[0].message.content or ""

    async def _call_with_tools(self, messages, tools, max_tokens, temperature, **kwargs) -> dict:
        if not settings.zai_api_key:
            raise RuntimeError("ZAI_API_KEY not set")
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
