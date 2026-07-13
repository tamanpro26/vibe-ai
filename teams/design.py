"""
teams/design.py
Design Team — UI assets, animations, typography, motion demos.

Models:
  1. flux_asset              — Static asset gen (best open-source image quality)
  2. flux_typography     — Typography renderer (text-in-image)
  3. flux_world     — World-aware gen (80B MoE)
  4. flux_realism_gen             — Animation / motion (Wan-Bench #1)
  5. turbo_fast   — UI motion demos (image-to-video)

Optimisations active:
  • Intent classifier — detect icon/banner/hero/animation/mockup, apply type-specific style
  • Design prompt expander — brief prompts get quality+style tokens before generation
  • All variations generated in PARALLEL (was sequential)
  • Animation: flux_realism_gen + turbo_fast both run in parallel, first success wins
  • Fallback chain — flux_asset as last resort if all primary models fail
"""
from __future__ import annotations

import asyncio
import re
from typing import Any

from loguru import logger

from core.imcp import TaskJSON, TaskType
from models.registry import generate_resilient
from teams.base_team import BaseTeam


# ── Intent classification ──────────────────────────────────────────────────────

_INTENT_KEYWORDS: dict[str, list[str]] = {
    "icon":         ["icon", "logo", "symbol", "glyph", "favicon", "badge"],
    "banner":       ["banner", "header", "hero", "cover", "masthead", "splash", "ad"],
    "card":         ["card", "tile", "panel", "widget", "chip"],
    "mockup":       ["mockup", "wireframe", "prototype", "screen", "ui mock", "figma"],
    "illustration": ["illustration", "artwork", "drawing", "graphic", "character"],
    "animation":    ["animation", "motion", "transition", "animated", "video", "gif", "loop", "hover"],
    "background":   ["background", "wallpaper", "pattern", "texture", "gradient"],
}

_QUALITY_SUFFIXES: dict[str, str] = {
    "icon":         "flat vector style, clean lines, transparent background, 512×512, professional, sharp edges",
    "banner":       "16:9, professional design, high contrast, bold typography, web-optimised",
    "card":         "clean UI card, subtle shadow, rounded corners, modern flat style",
    "mockup":       "realistic UI mockup, high fidelity, professional presentation, clean interface",
    "illustration": "digital illustration, vibrant colors, detailed, high resolution, professional",
    "animation":    "smooth 60fps, easing curves, clean transitions, modern UI animation style",
    "background":   "seamless tile, 4K resolution, professional quality, high detail",
    "general":      "high quality, professional design, clean, modern, sharp, detailed",
}

_STYLE_SYSTEM = """You are a design prompt engineer for AI image/video generation models.
Expand the user's brief design request into a rich, specific visual generation prompt.
Add: visual style, color palette hints, composition, quality markers.
Preserve the user's original intent exactly — only enrich the visual specification.
Output the expanded prompt as plain text only. Maximum 2 sentences."""


def _detect_intent(instruction: str) -> str:
    lower = instruction.lower()
    for intent, keywords in _INTENT_KEYWORDS.items():
        if any(kw in lower for kw in keywords):
            return intent
    return "general"


class DesignTeam(BaseTeam):
    team_name = "design"

    async def _execute(
        self,
        task_json: TaskJSON,
        instruction: str,
        iteration: int,
        extra: dict[str, Any],
    ) -> str:
        instruction  = instruction or self._instruction(task_json)
        task_type    = task_json.classification.primary_type
        has_text     = extra.get("needs_text_rendering", False)
        needs_motion = task_type == TaskType.ANIMATION or extra.get("needs_animation")

        # ── 1. Classify intent + expand prompt ────────────────────────────────
        intent   = _detect_intent(instruction)
        expanded = await self._expand_prompt(instruction, intent)
        logger.info(
            f"[design] intent={intent} | prompt expanded: "
            f"{len(instruction)} → {len(expanded)} chars"
        )

        results: list[str] = []

        if needs_motion:
            # Parallel: flux_realism_gen (animation) + flux_asset (static keyframe)
            # flux_realism_gen and turbo_fast race; first success is the animation result
            anim_task, static_task = await asyncio.gather(
                self._generate_animation(expanded),
                self._generate_static(expanded),
                return_exceptions=True,
            )
            if not isinstance(anim_task, Exception) and anim_task:
                results.append(f"Animation reference: {anim_task}")
            else:
                logger.warning(f"[design] animation generation failed: {anim_task}")
            if not isinstance(static_task, Exception) and static_task:
                results.append(f"Static keyframe: {static_task}")

        else:
            # Parallel: primary model + flux_world alternative simultaneously
            primary_model = "flux_typography" if has_text else "flux_asset"
            primary_task, alt_task = await asyncio.gather(
                self._get_model(primary_model).generate(
                    prompt=expanded, max_tokens=100
                ),
                self._get_model("flux_world").generate(
                    prompt=expanded + ", high quality UI design", max_tokens=100
                ),
                return_exceptions=True,
            )
            if not isinstance(primary_task, Exception) and primary_task and primary_task.strip():
                results.append(f"Design asset (primary): {primary_task.strip()}")
            else:
                logger.warning(f"[design] primary model failed: {primary_task}")

            if not isinstance(alt_task, Exception) and alt_task and alt_task.strip():
                results.append(f"Design asset (alternative): {alt_task.strip()}")

        # ── 2. Fallback if all models failed ──────────────────────────────────
        if not results:
            logger.warning("[design] all primary models failed — flux_asset fallback")
            try:
                fallback = await self._get_model("flux_asset").generate(
                    prompt=instruction, max_tokens=100
                )
                if fallback and fallback.strip():
                    results.append(f"Design asset (fallback): {fallback.strip()}")
                else:
                    return "DESIGN FAILED: All generation models returned empty output."
            except Exception as exc:
                return f"DESIGN FAILED: All generation models unavailable. ({exc})"

        quality_note = _QUALITY_SUFFIXES.get(intent, _QUALITY_SUFFIXES["general"])
        return (
            f"DESIGN OUTPUTS\n{'='*40}\n"
            + "\n".join(results)
            + f"\n\nIntent: {intent} | Style: {quality_note}\n"
            + f"Instruction: {instruction}"
        )

    # ── Prompt expander ────────────────────────────────────────────────────────

    async def _expand_prompt(self, prompt: str, intent: str) -> str:
        """
        Short prompts (≤6 words): just append quality suffix — zero latency.
        Longer prompts: use a fast model to integrate quality tokens naturally.
        """
        suffix = _QUALITY_SUFFIXES.get(intent, _QUALITY_SUFFIXES["general"])
        if len(prompt.split()) <= 6:
            return f"{prompt}, {suffix}"
        try:
            expanded = await generate_resilient(
                "glm_47_cerebras",
                prompt=f"Design request: {prompt}\nVisual quality target: {suffix}",
                system=_STYLE_SYSTEM,
                max_tokens=200,
                temperature=0.4,
            )
            expanded = expanded.strip()
            return expanded if expanded and len(expanded) > len(prompt) else f"{prompt}, {suffix}"
        except Exception:
            return f"{prompt}, {suffix}"

    # ── Generation helpers ─────────────────────────────────────────────────────

    async def _generate_static(self, prompt: str) -> str:
        return await self._get_model("flux_asset").generate(prompt=prompt, max_tokens=100)

    async def _generate_animation(self, prompt: str) -> str:
        """Race flux_realism_gen and turbo_fast — first success wins."""
        results = await asyncio.gather(
            self._get_model("flux_realism_gen").generate(prompt=prompt, max_tokens=100),
            self._get_model("turbo_fast").generate(prompt=prompt, max_tokens=100),
            return_exceptions=True,
        )
        for r in results:
            if not isinstance(r, Exception) and r and r.strip():
                return r.strip()
        raise RuntimeError("all animation models returned empty or failed")
