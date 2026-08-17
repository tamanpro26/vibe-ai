"""
teams/router_team.py
Router Team — fast classification, dispatch, and fallback handling.

Models:
  1. gpt_oss_120b_dispatch   — Primary dispatcher (30B MoE, ultra-fast)
  2. lfm_router    — Logic routing + structured output parsing
  3. gemma_4         — Multimodal gatekeeper
  4. llama31_8b_router       — Cost-overflow handler
  5. qwen25_3b_ollama — Edge micro-router (local, 3B, free)

The router is responsible for:
  - Quick intent classification before the full Prompt Refiner pipeline
  - Deciding if a request is simple enough to skip some teams
  - Fallback routing when primary models fail
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

from loguru import logger

from config.scaffold_loader import get_prompt
from core.imcp import TaskJSON, TaskType
from teams.base_team import BaseTeam


_ROUTER_SYSTEM = get_prompt("ROUTER_SYSTEM", """You are a fast task router in VibeAI.
Given a user request, classify it and return ONLY valid JSON:
{
  "task_type": "debugging|vibe_coding|ui_design|animation|video_analysis|research|mixed",
  "complexity": "simple|moderate|complex",
  "needs_vision": true/false,
  "needs_code": true/false,
  "needs_design": true/false,
  "media_path": "<path if found, else null>",
  "quick_summary": "one sentence describing the task"
}

RESEARCH DETECTION — set task_type: "research" when answering well requires
CURRENT external facts rather than reasoning from what a model already knows:
- The request asks what is happening/changed/released/announced now, recently,
  or "latest", or names a version, price, date, or ongoing event
- It asks to compare real products, papers, libraries, or vendors on fact
- It asks for sources, citations, evidence, or "look it up"
Do NOT use it for questions answerable from general knowledge ("explain
recursion"), or for coding/debugging work, which have their own types. This
routes to a team that searches first and answers only from what it retrieves,
so misrouting a general question here just makes it slower, and misrouting a
current-facts question AWAY from here makes it confidently out of date.

VISION DETECTION — set needs_vision: true and task_type: "video_analysis" when:
- The request mentions ANY filename ending in .mp4 .mov .avi .mkv .webm .frames
  .png .jpg .jpeg .gif .webp .bmp
- The user says "watch", "look at", "analyse", "analyze", "describe",
  "what's in", "what is in", "show me", "visualize" with a file reference
- A path or filename is present in the request (relative or absolute)
When needs_vision is true, also extract the path into "media_path".

IMAGE/DESIGN DETECTION — set needs_design: true and task_type: "ui_design" when
the request asks to GENERATE, CREATE, DRAW, MAKE, PAINT, or DESIGN any picture,
image, photo, illustration, icon, logo, banner, artwork, wallpaper, or graphic
(e.g. "create an image of a cat", "draw me a sunset", "generate a logo",
"make an icon for..."). This is a real, load-bearing signal, found live
(2026-07-13): before this rule existed, "create an image of a cat wearing
sunglasses" had no detection guidance at all (unlike vision, which does),
classified as complexity:simple with needs_design left false, and the fast
path below answered it with a plain text model that has no image capability
-- it replied with a hallucinated "I can't generate images" and suggested
the user go use a DIFFERENT tool, when this system genuinely can. NEVER mark
a bare image-generation request as complexity:simple even if it reads as a
short, clear request -- it must reach the design team, not the fast path.

complexity guide:
- simple:   greetings, chat, questions, explanations, or ONE small function/snippet
            with clear requirements (e.g. "write a function that checks primes")
- moderate: one feature, file, or component with several requirements
- complex:  multi-file projects, debugging with stack traces, full apps,
            design systems, video analysis, or anything ambiguous""")


class RouterTeam(BaseTeam):
    team_name = "router"

    async def _execute(
        self,
        task_json: TaskJSON,
        instruction: str,
        iteration: int,
        extra: dict[str, Any],
    ) -> str:
        # Router's output is a routing decision JSON
        connector = self._get_model("gpt_oss_120b_dispatch")
        result = await connector.generate(
            prompt=f"Classify this task: {task_json.original_prompt}",
            system=_ROUTER_SYSTEM,
            max_tokens=200,
            temperature=0.1,
        )
        return result

    async def classify_quick(self, prompt: str) -> dict:
        """
        Fast pre-classification before the full Prompt Refiner pipeline.
        Used to decide if we can skip heavy models for simple tasks.

        Tries the local Ollama edge router (qwen25_3b_ollama — free, zero
        quota cost, no network round-trip past localhost) first when it's
        actually reachable; falls back to the cloud dispatcher otherwise.
        This was registered in models_config.py and documented in this
        file's own docstring but never actually called — classify_quick
        always hardcoded the cloud model, so the "edge micro-router" tier
        existed on paper only.

        Originally wired to qwen3.5:0.8b, which turned out to be reachable
        but not capable: verified live (5 trials, 3 system-prompt variants,
        max_tokens up to 2000) that it returned 0 chars every time against
        this function's real multi-field schema — a "thinking" model that
        never converged to visible output for a schema this size, and a
        bigger budget didn't help. Swapped to qwen2.5:3b-instruct (no
        thinking mode, documented JSON-schema-compliance edge): verified
        live, correct schema-matching output in 1.1s once warm (one-time
        ~45s cold start to load into VRAM the first time it's called).

        The result-checking fallback below is kept regardless of which local
        model is configured — an empty/unparseable classification retries
        once against the cloud model rather than silently handing the caller
        `{}` and losing the fast-path optimization. A short timeout bounds
        the cost of a stuck/incapable local model — this path exists to be
        FAST (~1s normally), so a bad local response must not be allowed to
        cost more than a few seconds before the cloud retry kicks in.
        """
        from models.connectors.ollama import is_reachable
        use_ollama = await is_reachable()
        model_id = "qwen25_3b_ollama" if use_ollama else "gpt_oss_120b_dispatch"
        try:
            parsed = await asyncio.wait_for(self._classify_once(model_id, prompt), timeout=4.0)
        except asyncio.TimeoutError:
            parsed = {}
        if not parsed and use_ollama:
            logger.info("[router] Ollama classification empty/timed out — retrying on cloud")
            parsed = await self._classify_once("gpt_oss_120b_dispatch", prompt)
        return parsed

    async def _classify_once(self, model_id: str, prompt: str) -> dict:
        connector = self._get_model(model_id)
        raw = await connector.generate(
            prompt=f"Classify: {prompt}",
            system=_ROUTER_SYSTEM,
            max_tokens=150,
            temperature=0.05,
        )
        try:
            import re
            match = re.search(r"\{.*\}", raw, re.DOTALL)
            return json.loads(match.group(0)) if match else {}
        except Exception:
            return {}
