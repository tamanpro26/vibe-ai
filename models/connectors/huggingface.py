"""
models/connectors/huggingface.py
Hugging Face Inference Providers — free tier with generous credits.

How to get your free HF token (takes 2 minutes):
  1. Go to https://huggingface.co/join  (free account)
  2. Go to https://huggingface.co/settings/tokens
  3. Click "New token" → name it "VibeAI" → select "Fine-grained"
  4. Under permissions → enable "Make calls to Inference Providers"
  5. Copy the token (starts with hf_...)
  6. Add to .env:  HF_TOKEN=hf_...

Supports:
  - Text generation (thousands of models)
  - Image generation (FLUX.1-dev, FLUX.1-schnell, Stable Diffusion 3.5)
  - Vision models
  - Embeddings

Free tier: generous credits included. FLUX generation costs ~$0.001 per image.
"""
from __future__ import annotations

import base64
import io
import os
from typing import Any

from loguru import logger

from config.models_config import ModelDef
from config.settings import settings
from models.base import BaseModelConnector


class HuggingFaceConnector(BaseModelConnector):
    """
    Connects to Hugging Face Inference Providers API.
    Free tier, generous credits — much easier than Together.ai.
    """

    HF_BASE_URL = "https://router.huggingface.co/v1"

    def __init__(self, model_def: ModelDef) -> None:
        super().__init__(model_def)
        self._hf_token = settings.hf_token or os.getenv("HF_TOKEN", "")

    async def _call(
        self,
        prompt:      str,
        system:      str,
        images:      list[str],
        max_tokens:  int,
        temperature: float,
        **kwargs: Any,
    ) -> str:
        if not self._hf_token:
            raise RuntimeError(
                "HF_TOKEN not set. Get yours free at "
                "https://huggingface.co/settings/tokens"
            )

        # Route to appropriate endpoint based on capability
        if "image_generation" in self.model_def.capabilities:
            return await self._generate_image(prompt, **kwargs)
        else:
            return await self._generate_text(prompt, system, images, max_tokens, temperature)

    async def _generate_image(self, prompt: str, **kwargs: Any) -> str:
        """Generate image via HF Inference Providers. Returns base64 or URL."""
        try:
            from huggingface_hub import InferenceClient
            client = InferenceClient(api_key=self._hf_token)

            image = client.text_to_image(
                prompt=prompt,
                model=self.api_model,
                width=kwargs.get("width", 1024),
                height=kwargs.get("height", 1024),
            )

            # Convert PIL Image to base64
            buf = io.BytesIO()
            image.save(buf, format="PNG")
            b64 = base64.b64encode(buf.getvalue()).decode()
            logger.info(f"[huggingface] image generated | model={self.api_model}")
            return f"data:image/png;base64,{b64}"

        except ImportError:
            raise RuntimeError(
                "huggingface_hub not installed. Run: pip install huggingface_hub"
            )

    async def _generate_text(
        self,
        prompt:      str,
        system:      str,
        images:      list[str],
        max_tokens:  int,
        temperature: float,
    ) -> str:
        """Generate text via HF Inference Providers (OpenAI-compatible)."""
        from openai import AsyncOpenAI

        client = AsyncOpenAI(
            base_url=self.HF_BASE_URL,
            api_key=self._hf_token,
        )

        messages = []
        if system:
            messages.append({"role": "system", "content": system})

        # Vision support
        if images:
            content = [{"type": "text", "text": prompt}]
            for img in images:
                url = img if img.startswith("http") else f"data:image/png;base64,{img}"
                content.append({"type": "image_url", "image_url": {"url": url}})
            messages.append({"role": "user", "content": content})
        else:
            messages.append({"role": "user", "content": prompt})

        resp = await client.chat.completions.create(
            model=self.api_model,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        return resp.choices[0].message.content or ""
