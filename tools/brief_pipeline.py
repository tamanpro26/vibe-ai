"""
tools/brief_pipeline.py
Upgrade 2 — Brief-first Creative Pipeline + Inspiration Injection

Problem: committee creativity is incoherent — every model pulls a different direction.
Solution:
  1. Fetch 5 real-world examples (GitHub / CodePen) as inspiration
  2. Qwen3.7 Max writes a CreativeBrief (concept, style, palette, constraints)
  3. All other design models execute WITHIN the brief, never overriding it
  4. Coherence of single-model vision + execution power of ensemble
"""
from __future__ import annotations

import asyncio
import json
import re
from typing import Any

import aiohttp
from loguru import logger
from pydantic import BaseModel, Field


# ── Creative Brief schema ─────────────────────────────────────────────────────

class CreativeBrief(BaseModel):
    """
    The single source of truth for any creative task.
    Written by the Creative Director (Qwen3.7 Max).
    All other models must execute within this brief.
    """
    concept:          str              # one-sentence creative direction
    style:            str              # visual/motion style (e.g. "glassmorphism", "brutalist")
    palette:          list[str]        # hex codes or named colors
    motion_language:  str              # animation style (e.g. "spring physics", "CSS ease-in-out")
    constraints:      list[str]        # hard rules (e.g. "no gradients", "WCAG AA")
    inspiration_refs: list[str]        # URLs of inspiration examples
    tech_target:      str = ""         # target stack (e.g. "React + Tailwind")
    audience:         str = ""         # target user/context


# ── Inspiration fetcher ───────────────────────────────────────────────────────

async def _fetch_github_examples(query: str, max_results: int = 3) -> list[str]:
    """Search GitHub for relevant code examples. Returns list of URLs."""
    urls: list[str] = []
    api_url = "https://api.github.com/search/repositories"
    params  = {"q": f"{query} in:readme", "sort": "stars", "per_page": max_results}
    headers = {"Accept": "application/vnd.github.v3+json"}

    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=6)) as session:
            async with session.get(api_url, params=params, headers=headers) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    urls = [
                        item.get("html_url", "")
                        for item in data.get("items", [])
                        if item.get("html_url")
                    ]
    except Exception as exc:
        logger.warning(f"[brief/github] {exc}")
    return urls


async def _fetch_codepen_examples(query: str) -> list[str]:
    """Return CodePen search URLs (no API key needed for links)."""
    encoded = query.replace(" ", "+")
    return [
        f"https://codepen.io/search/pens?q={encoded}&order=popularity&depth=everything",
        f"https://codepen.io/collection/?q={encoded}",
    ]


async def fetch_inspiration(task_description: str) -> list[str]:
    """Collect inspiration URLs from GitHub + CodePen concurrently."""
    # Build a focused search query from the task
    gh_query   = f"UI animation {task_description[:40]}"
    gh_task    = _fetch_github_examples(gh_query)
    cp_task    = _fetch_codepen_examples(task_description[:50])

    gh_urls, cp_urls = await asyncio.gather(gh_task, cp_task, return_exceptions=True)

    all_urls: list[str] = []
    if isinstance(gh_urls, list):
        all_urls.extend(gh_urls)
    if isinstance(cp_urls, list):
        all_urls.extend(cp_urls)

    logger.info(f"[brief] {len(all_urls)} inspiration references collected")
    return all_urls[:5]


# ── Creative Director ─────────────────────────────────────────────────────────

_DIRECTOR_SYSTEM = """You are the Creative Director in VibeAI.
Your role: write the definitive CreativeBrief that all other design models will follow.
Be specific and opinionated. Vague briefs produce inconsistent results.
Return ONLY valid JSON matching the schema exactly:
{
  "concept": "one clear creative direction sentence",
  "style": "specific visual/motion style name",
  "palette": ["#hex1", "#hex2", "#hex3"],
  "motion_language": "specific animation approach",
  "constraints": ["hard rule 1", "hard rule 2"],
  "inspiration_refs": ["url1", "url2"],
  "tech_target": "framework/stack",
  "audience": "target user context"
}"""

_BRIEF_ENFORCEMENT = """You are executing a design task within a mandatory Creative Brief.
You MUST follow the brief exactly. Do not deviate from the concept, style, palette, or constraints.
The brief is not a suggestion — it is the creative law for this task.

CREATIVE BRIEF:
{brief_json}

Your task: {instruction}"""


class BriefFirstPipeline:
    """
    Manages the creative direction and brief enforcement lifecycle.
    Used by DesignTeam and CodeTeam for creative tasks.
    """

    async def create_brief(
        self,
        task_description: str,
        tech_stack:       list[str] | None = None,
    ) -> CreativeBrief:
        """
        Step 1: Fetch inspiration.
        Step 2: Qwen3.7 Max writes the Creative Brief.
        """
        from models.registry import registry

        # Fetch inspiration concurrently with setting up the connector
        refs = await fetch_inspiration(task_description)

        connector = registry.get("qwen36_27b_verifier")
        prompt = (
            f"Task: {task_description}\n\n"
            f"Tech target: {', '.join(tech_stack or [])}\n\n"
            f"Inspiration references found:\n" +
            "\n".join(f"- {u}" for u in refs) +
            "\n\nWrite the Creative Brief for this task."
        )

        raw = await connector.generate(
            prompt=prompt,
            system=_DIRECTOR_SYSTEM,
            max_tokens=800,
            temperature=0.6,  # creative but structured
        )

        try:
            data = _extract_json(raw)
            data.setdefault("inspiration_refs", refs)
            return CreativeBrief(**data)
        except Exception as exc:
            logger.warning(f"[brief] parse failed ({exc}), using defaults")
            return CreativeBrief(
                concept=task_description,
                style="modern minimal",
                palette=["#0066FF", "#FFFFFF", "#111111"],
                motion_language="ease-in-out transitions",
                constraints=["WCAG AA contrast", "mobile responsive"],
                inspiration_refs=refs,
            )

    def enforce_prompt(self, instruction: str, brief: CreativeBrief) -> str:
        """
        Wrap an instruction with brief enforcement context.
        Pass the returned string to any design model as the prompt.
        """
        return _BRIEF_ENFORCEMENT.format(
            brief_json=brief.model_dump_json(indent=2),
            instruction=instruction,
        )

    async def create_and_enforce(
        self,
        instruction:  str,
        tech_stack:   list[str] | None = None,
    ) -> tuple[CreativeBrief, str]:
        """
        One-call convenience: create brief → return (brief, enforced_prompt).
        """
        brief   = await self.create_brief(instruction, tech_stack)
        prompt  = self.enforce_prompt(instruction, brief)
        logger.info(
            f"[brief] concept='{brief.concept[:50]}' | "
            f"style='{brief.style}' | palette={brief.palette[:2]}"
        )
        return brief, prompt


def _extract_json(text: str) -> dict:
    try:
        return json.loads(text.strip())
    except Exception:
        pass
    m = re.search(r"\{[\s\S]+\}", text)
    if m:
        return json.loads(m.group(0))
    raise ValueError("No JSON in creative brief response")


# Singleton
brief_pipeline = BriefFirstPipeline()
