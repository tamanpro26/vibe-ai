"""
teams/prompt_refiner.py
The 5-model sequential Prompt Refiner pipeline.

Stage 1 — Qwen3-30B-Instruct   → Intent parser
Stage 2 — GLM-4.5-Air           → Context enricher
Stage 3 — Mistral Small 3.5     → Ambiguity resolver
Stage 4 — Phi-4                 → Technical translator
Stage 5 — Cogito v1 Preview     → Instruction formatter → outputs TaskJSON

Each stage receives the previous stage's output as additional context.
"""
from __future__ import annotations

import asyncio
import json
import re

from loguru import logger

from config.models_config import get_team_models
from core.imcp import (
    TaskJSON, TeamActivation, Classification, TaskContext,
    TaskType, Complexity, Priority,
)
from models.registry import generate_resilient

# ── Stage system prompts ──────────────────────────────────────────────────────

_STAGE_SYSTEMS = {
    "gpt_oss_120b_free_intent": """You are an intent parser in a multi-agent AI pipeline.
Your job: extract the true user intent from their message.
A request to GENERATE, CREATE, DRAW, MAKE, PAINT, or DESIGN a picture, image,
photo, illustration, icon, logo, banner, artwork, wallpaper, or graphic --
with no app/website/code-building component -- is task_type "ui_design", not
"mixed", even though it isn't literally a UI. Found live (2026-07-13): "create
an image of a cat wearing sunglasses" was mistyped "mixed" here, which has no
defined team-activation rule downstream and silently drops the design team
entirely, routing a pure image request to brain+code+vision instead.
Return a JSON object with:
{
  "core_intent": "...",
  "task_type": "debugging|vibe_coding|ui_design|animation|video_analysis|mixed",
  "success_criteria": ["...", "..."],
  "implicit_requirements": ["...", "..."],
  "tech_signals": ["...", "..."]
}
Return ONLY valid JSON, no prose.""",

    "gemini_flash_prompt": """You are a context enricher in a multi-agent AI pipeline.
Given a parsed intent, enrich it with:
- Inferred tech stack details
- Common constraints and best practices
- Background knowledge the user didn't state
Return a JSON object with additional "context" and "constraints" fields.
Return ONLY valid JSON.""",

    "gpt_oss_20b_free": """You are an ambiguity resolver in a multi-agent AI pipeline.
Identify ANY vague, missing, or conflicting parts in the prompt.
Resolve them using context or flag them.
Return a JSON object with:
{
  "resolved": true/false,
  "clarifications": {"vague_part": "resolved_as"},
  "assumptions_made": ["..."],
  "refined_prompt": "..."
}
Return ONLY valid JSON.""",

    "nemotron_super_spec": """You are a technical specification writer.
Convert the refined user intent into precise, unambiguous technical specifications.
Be exact: name specific APIs, patterns, file types, browser targets, frameworks.
Return a JSON object with:
{
  "tech_spec": "...",
  "required_tech_stack": ["..."],
  "complexity": "simple|moderate|complex",
  "requires_execution": true/false,
  "requires_visual_check": true/false
}
Return ONLY valid JSON.""",

    "nemotron_nano_format": """You are an instruction formatter for a 31-model AI system.
You receive a fully-refined technical specification and must output a complete TaskJSON.
The task types are: debugging, vibe_coding, ui_design, animation, video_analysis, mixed.

Active team rules:
- debugging:      brain=true, code=true, vision=conditional(screenshot), design=false
- vibe_coding:    brain=true, code=true, vision=true, design=conditional(ui_changes)
- ui_design:      brain=true, code=conditional(impl), vision=true, design=true
- animation:      brain=true, code=conditional(css/js), vision=true, design=true
- video_analysis: brain=true, code=false, vision=true, design=false

A bare request to generate/create/draw/make/paint/design a picture, image,
photo, illustration, icon, logo, banner, artwork, or graphic -- with NO
app/website/code-building component -- is primary_type "ui_design" with
design.active=true and code.active=false (there is nothing to implement).
NEVER classify a pure image-generation request as "mixed" -- "mixed" has no
defined rule above and the model must guess, which has been observed live
(2026-07-13) to default to brain+code+vision and silently drop the design
team from a request that is entirely a design/image request.

Write specific, actionable instructions for each active team.
Return ONLY a valid JSON matching this schema exactly:
{
  "refined_prompt": "...",
  "primary_type": "debugging|vibe_coding|ui_design|animation|video_analysis|mixed",
  "sub_types": ["..."],
  "complexity": "simple|moderate|complex",
  "active_teams": {
    "brain":  {"active": true/false, "models": [...], "instruction": "...", "reason": ""},
    "code":   {"active": true/false, "models": [...], "instruction": "...", "reason": ""},
    "vision": {"active": true/false, "models": [...], "instruction": "...", "reason": ""},
    "design": {"active": true/false, "models": [...], "instruction": "...", "reason": ""},
    "router": {"active": true,       "models": ["gpt_oss_120b_dispatch"], "instruction": "Route messages between active teams.", "reason": ""}
  },
  "team_instructions": {
    "brain": "...",
    "code": "...",
    "router": "..."
  },
  "success_criteria": ["...", "..."],
  "tech_stack": ["..."],
  "requires_execution": true/false,
  "requires_visual": true/false
}""",
}

# Default models for each active team (Cogito uses these)
_TEAM_DEFAULT_MODELS = {
    "brain":  ["gemini_flash", "gpt_oss_120b_planner"],
    "code":   ["gpt_oss_120b_coder", "gpt_oss_120b_debug"],
    "vision": ["gemini_flash_vision", "nemotron_vl"],
    "design": ["flux_asset", "flux_realism_gen"],
    "router": ["gpt_oss_120b_dispatch"],
}


# Per-stage wall-clock ceiling. Each stage is ONE generate_resilient call, but
# that call walks a whole failover chain internally (~30s per candidate), so a
# stage whose primary is sick can run for minutes with nothing timing it out.
# Measured live (2026-08-08, v4-code-05, full pipeline): stages 1-4 returned in
# 21s / 37s / 76s while gpt_oss_20b_free (OpenRouter ":free") was still going at
# 376s when the 400s trace cutoff fired. gather() waits for the slowest member,
# so that one straggler WAS the >290s pipeline latency — the request never even
# reached the code team.
# 60s keeps the observed-healthy stages and drops a hung one. Losing a stage is
# already an accepted outcome here (see the skip branch below): stages 1-4 are
# additive context, not required inputs.
# ponytail: fixed ceiling, not adaptive — revisit if healthy stages start
# exceeding 60s rather than raising this blindly.
_STAGE_TIMEOUT_S = 60


class PromptRefinerPipeline:
    """
    Runs the 5-stage sequential prompt refinement pipeline.
    Returns a TaskJSON ready for the manager to dispatch.
    """

    STAGES = [
        "gpt_oss_120b_free_intent",
        "gemini_flash_prompt",
        "gpt_oss_20b_free",
        "nemotron_super_spec",
        "nemotron_nano_format",
    ]

    async def run(self, original_prompt: str, session_id: str = "") -> TaskJSON:
        """
        Stages 1-4 are independent analyses of the same prompt, so they run
        in PARALLEL. Only stage 5 (nemotron_nano_format) needs the others' outputs —
        it formats everything into the final TaskJSON.
        """
        logger.info(f"[prompt_refiner] starting pipeline | session={session_id}")
        logger.info(f"[prompt_refiner] input: {original_prompt[:80]}...")

        parallel_stages = [s for s in self.STAGES if s != "nemotron_nano_format"]
        logger.info(f"[prompt_refiner] stages 1-4 in parallel → {parallel_stages}")

        results = await asyncio.gather(
            *(
                asyncio.wait_for(
                    generate_resilient(
                        model_id,
                        prompt=f"Original user prompt: {original_prompt}",
                        system=_STAGE_SYSTEMS[model_id],
                        max_tokens=2000,
                        temperature=0.3,  # low temp for structured outputs
                    ),
                    timeout=_STAGE_TIMEOUT_S,
                )
                for model_id in parallel_stages
            ),
            return_exceptions=True,
        )

        accumulated: dict[str, str] = {}
        for model_id, result in zip(parallel_stages, results):
            if isinstance(result, Exception):
                # A stage's output is additive context — losing one stage is
                # far better than failing the whole request.
                logger.warning(
                    f"[prompt_refiner] stage {model_id} failed "
                    f"({str(result)[:60]}) — skipping stage"
                )
            else:
                accumulated[model_id] = result

        # Stage 5: cogito formats all analyses into the TaskJSON
        context_block = ""
        if accumulated:
            context_block = "\n\n--- Analyses from the refiner team ---\n"
            for prev_id, prev_out in accumulated.items():
                context_block += f"\n[{prev_id}]:\n{prev_out}\n"

        logger.info("[prompt_refiner] stage 5/5 → nemotron_nano_format")
        final_output = ""
        try:
            final_output = await asyncio.wait_for(
                generate_resilient(
                    "nemotron_nano_format",
                    prompt=f"Original user prompt: {original_prompt}{context_block}",
                    system=_STAGE_SYSTEMS["nemotron_nano_format"],
                    max_tokens=2000,
                    temperature=0.3,
                ),
                timeout=_STAGE_TIMEOUT_S,
            )
        except Exception as exc:
            logger.warning(
                f"[prompt_refiner] final stage failed ({str(exc)[:60]}) — "
                "using fallback TaskJSON"
            )
        task_json = self._parse_task_json(original_prompt, session_id, final_output)

        logger.info(
            f"[prompt_refiner] done | "
            f"task_id={task_json.task_id} | "
            f"type={task_json.classification.primary_type.value} | "
            f"active_teams={[t for t, a in task_json.active_teams.items() if a.active]}"
        )
        return task_json

    # ── Private helpers ───────────────────────────────────────────────

    def _parse_task_json(
        self, original_prompt: str, session_id: str, raw: str
    ) -> TaskJSON:
        """Parse Cogito's JSON output into a validated TaskJSON."""
        try:
            data = _extract_json(raw)
            return self._build_task_json(original_prompt, session_id, data)
        except Exception as exc:
            logger.warning(
                f"[prompt_refiner] failed to parse Cogito JSON ({exc}). "
                "Falling back to safe defaults."
            )
            return self._fallback_task_json(original_prompt, session_id)

    def _build_task_json(
        self, original_prompt: str, session_id: str, data: dict
    ) -> TaskJSON:
        """Construct a TaskJSON from parsed Cogito output."""
        # Map string → enum
        type_map = {t.value: t for t in TaskType}
        comp_map = {c.value: c for c in Complexity}

        primary_type = type_map.get(data.get("primary_type", "vibe_coding"), TaskType.VIBE_CODING)
        complexity = comp_map.get(data.get("complexity", "moderate"), Complexity.MODERATE)

        # Build active_teams
        active_teams: dict[str, TeamActivation] = {}
        for team_key, team_data in data.get("active_teams", {}).items():
            is_active = team_data.get("active", False)
            active_teams[team_key] = TeamActivation(
                active=is_active,
                models=team_data.get("models", _TEAM_DEFAULT_MODELS.get(team_key, [])),
                instruction=team_data.get("instruction", ""),
                reason=team_data.get("reason", ""),
                priority=Priority.NORMAL,
            )

        return TaskJSON(
            session_id=session_id,
            original_prompt=original_prompt,
            refined_prompt=data.get("refined_prompt", original_prompt),
            classification=Classification(
                primary_type=primary_type,
                sub_types=data.get("sub_types", []),
                complexity=complexity,
                confidence=0.92,
            ),
            active_teams=active_teams,
            team_instructions=data.get("team_instructions", {}),
            success_criteria=data.get("success_criteria", []),
            context=TaskContext(
                tech_stack=data.get("tech_stack", []),
                requires_execution=data.get("requires_execution", False),
                requires_visual=data.get("requires_visual", False),
            ),
        )

    def _fallback_task_json(self, original_prompt: str, session_id: str) -> TaskJSON:
        """Safe fallback if Cogito output can't be parsed."""
        return TaskJSON(
            session_id=session_id,
            original_prompt=original_prompt,
            refined_prompt=original_prompt,
            active_teams={
                "brain": TeamActivation(active=True, models=["gemini_flash"], instruction="Plan the task."),
                "code":  TeamActivation(active=True, models=["gpt_oss_120b_coder"], instruction="Implement the task."),
                "router": TeamActivation(active=True, models=["gpt_oss_120b_dispatch"], instruction="Route messages."),
            },
            success_criteria=["Task completed as requested"],
        )


def _extract_json(text: str) -> dict:
    """Extract JSON from a string that may contain markdown fences."""
    # Try direct parse first
    try:
        return json.loads(text.strip())
    except json.JSONDecodeError:
        pass
    # Strip markdown ```json ... ```
    match = re.search(r"```(?:json)?\s*([\s\S]+?)```", text)
    if match:
        return json.loads(match.group(1).strip())
    # Last-resort: find first { ... }
    match = re.search(r"\{[\s\S]+\}", text)
    if match:
        return json.loads(match.group(0))
    raise ValueError("No JSON found in model output")
