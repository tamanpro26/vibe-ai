"""
models/connectors/together_conn.py
Connector for Together.ai — used for image/video generation models:
FLUX.2, HunyuanImage-3.0, Wan 2.7, HunyuanVideo 1.5
"""
from __future__ import annotations

from typing import Any

from openai import AsyncOpenAI
from loguru import logger

from config.models_config import ModelDef
from config.settings import settings
from models.base import BaseModelConnector


class TogetherConnector(BaseModelConnector):
    """
    Routes to Together.ai.
    For image/video models, returns a URL rather than text.
    """

    def __init__(self, model_def: ModelDef) -> None:
        super().__init__(model_def)
        self._client = AsyncOpenAI(
            api_key=settings.together_api_key,
            base_url=settings.together_base_url,
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
        # Design/image/video models — use images.generate endpoint
        if "image_generation" in self.model_def.capabilities or \
           "video_generation" in self.model_def.capabilities:
            return await self._generate_image(prompt, **kwargs)

        # Text models via chat completions
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        response = await self._client.chat.completions.create(
            model=self.api_model,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        return response.choices[0].message.content or ""

    async def _generate_image(self, prompt: str, **kwargs: Any) -> str:
        """Call Together's image generation endpoint and return URL."""
        try:
            response = await self._client.images.generate(
                model=self.api_model,
                prompt=prompt,
                n=1,
                size=kwargs.get("size", "1024x1024"),
            )
            url = response.data[0].url if response.data else ""
            logger.info(f"[{self.model_id}] image generated: {url[:60]}...")
            return url
        except Exception as exc:
            logger.error(f"[{self.model_id}] image generation failed: {exc}")
            raise
