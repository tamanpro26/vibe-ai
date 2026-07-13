"""
models/connectors/pollinations.py
Pollinations.ai — 100% free image generation. No API key. No signup. No credit card.

API: https://image.pollinations.ai/prompt/{encoded_prompt}?model=flux&width=1024&height=1024
Returns: image URL directly (the URL itself IS the image)

Supported models:
  flux        — FLUX.1 (best quality, default)
  flux-realism — FLUX with realism LoRA
  turbo       — Fastest generation
  seedream    — Alternative high-quality model
"""
from __future__ import annotations

import urllib.parse
from typing import Any

import aiohttp
from loguru import logger

from config.models_config import ModelDef
from models.base import BaseModelConnector


class PollinationsConnector(BaseModelConnector):
    """
    Zero-setup image generation via Pollinations.ai.
    No API key, no account, no rate limits for reasonable usage.
    Simply builds a URL and returns it — the URL IS the generated image.
    """

    BASE_URL  = "https://image.pollinations.ai/prompt/"
    TEXT_URL  = "https://text.pollinations.ai/"

    def __init__(self, model_def: ModelDef) -> None:
        super().__init__(model_def)
        self._model_name = model_def.api_model   # e.g. "flux", "turbo", "seedream"

    async def _call(
        self,
        prompt:      str,
        system:      str,
        images:      list[str],
        max_tokens:  int,
        temperature: float,
        **kwargs: Any,
    ) -> str:
        """
        Build Pollinations URL and verify it responds.
        Returns the image URL string (can be used directly in <img> or saved).
        """
        width    = kwargs.get("width",  1024)
        height   = kwargs.get("height", 1024)
        seed     = kwargs.get("seed",   None)

        # Clean and encode the prompt
        clean_prompt = prompt[:500].strip()   # Pollinations has prompt length limits
        encoded      = urllib.parse.quote(clean_prompt, safe="")

        # Build URL
        params  = f"model={self._model_name}&width={width}&height={height}&nologo=true&enhance=true"
        if seed is not None:
            params += f"&seed={seed}"

        url = f"{self.BASE_URL}{encoded}?{params}"

        # Verify the URL is reachable (lightweight HEAD request)
        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=30)
            ) as session:
                async with session.head(url) as resp:
                    if resp.status in (200, 302, 303):
                        logger.info(f"[pollinations] image ready: {url[:80]}...")
                        return url
                    else:
                        # Fall back to returning URL anyway — Pollinations
                        # sometimes returns 200 only on GET, not HEAD
                        logger.warning(
                            f"[pollinations] HEAD returned {resp.status}, "
                            f"returning URL anyway"
                        )
                        return url

        except Exception as exc:
            logger.warning(f"[pollinations] verification failed ({exc}), returning URL")
            return url   # Return URL even if verification fails — may still work

    # ── Text generation fallback ──────────────────────────────────────────────

    async def generate_text(self, prompt: str, system: str = "") -> str:
        """
        Pollinations also provides a free text generation endpoint.
        Used as a fallback if OpenRouter is unavailable.
        API: GET https://text.pollinations.ai/{encoded_prompt}?model=openai
        """
        full    = (f"{system}\n\n{prompt}" if system else prompt)[:2000]
        encoded = urllib.parse.quote(full, safe="")
        url     = f"{self.TEXT_URL}{encoded}?model=openai&seed=42"

        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=20)
            ) as session:
                async with session.get(url) as resp:
                    if resp.status == 200:
                        return await resp.text()
                    return ""
        except Exception as exc:
            logger.warning(f"[pollinations/text] {exc}")
            return ""
