"""
teams/brain.py
Brain Team — orchestration, long-context planning, reasoning verification.

Models:
  1. glm_5.1          — Master orchestrator
  2. gpt_oss_120b_planner  — Long-context planner (1M token codebase analysis)
  3. qwen36_27b_verifier      — Reasoning verifier / quality gate
  4. llama33_70b_memory    — Memory manager / state keeper
  5. gpt_oss_120b_coord     — Multimodal coordinator
"""
from __future__ import annotations

import asyncio
from typing import Any

from loguru import logger

from core.imcp import TaskJSON, Complexity
from teams.base_team import BaseTeam


_BRAIN_SYSTEM = """You are part of the Brain team in VibeAI — a 31-model AI system.
Your role is strategic: break down complex tasks, plan execution order,
identify dependencies, and verify that all success criteria will be met.
Think step by step. Be specific about what each specialist team should do."""


class BrainTeam(BaseTeam):
    team_name = "brain"

    # Primary model handles planning; verifier checks it
    _PLANNER_MODEL  = "gemini_flash"
    _VERIFIER_MODEL = "qwen36_27b_verifier"
    _LONG_CTX_MODEL = "gpt_oss_120b_planner"

    async def _execute(
        self,
        task_json: TaskJSON,
        instruction: str,
        iteration: int,
        extra: dict[str, Any],
    ) -> str:
        instruction = instruction or self._instruction(task_json)
        context_size = extra.get("context_tokens", 0)

        # Complex tasks reason through VibeMind — the full Mixture-of-Agents
        # network — instead of a single planner model. This is where the fleet
        # behaves like one deep reasoning brain.
        # Iteration 1: full VibeMind (quality matters most).
        # Iteration 2+: Flash VibeMind (speed matters — refinement passes
        #               don't need maximum depth).
        if task_json.classification.complexity == Complexity.COMPLEX:
            if iteration == 1:
                return await self._deep_reason(task_json, instruction)
            else:
                return await self._flash_reason(task_json, instruction)

        # Route to long-context model if the task has a large codebase
        primary_model = (
            self._LONG_CTX_MODEL
            if context_size > 100_000
            else self._PLANNER_MODEL
        )

        logger.info(f"[brain] planning with {primary_model}")

        # Step 1: Plan
        plan = await self._get_model(primary_model).generate(
            prompt=(
                f"Task: {instruction}\n\n"
                f"Refined spec: {task_json.refined_prompt}\n\n"
                f"Success criteria:\n" +
                "\n".join(f"- {c}" for c in task_json.success_criteria) +
                "\n\nProvide a detailed execution plan."
            ),
            system=_BRAIN_SYSTEM,
            max_tokens=3000,
            temperature=0.4,
        )

        # Step 2: Verify the plan (only on first iteration — skip on refine)
        if iteration == 1:
            verified = await self._get_model(self._VERIFIER_MODEL).generate(
                prompt=(
                    f"Review this execution plan for completeness and correctness.\n\n"
                    f"PLAN:\n{plan}\n\n"
                    f"SUCCESS CRITERIA:\n" +
                    "\n".join(f"- {c}" for c in task_json.success_criteria) +
                    "\n\nConfirm the plan meets all criteria, or add missing steps."
                ),
                system=_BRAIN_SYSTEM,
                max_tokens=1500,
                temperature=0.2,
            )
            return f"EXECUTION PLAN:\n{plan}\n\nVERIFICATION:\n{verified}"

        return plan

    async def _deep_reason(self, task_json: TaskJSON, instruction: str) -> str:
        """Run the task through VibeMind (Mixture-of-Agents) for a deep,
        multi-model-verified plan. Falls back to single-model planning if
        VibeMind fails (all proposers down)."""
        from core.reasoning_core import reasoning_core

        criteria = "\n".join(f"- {c}" for c in task_json.success_criteria)
        problem = (
            f"{instruction}\n\n"
            f"Refined spec: {task_json.refined_prompt}\n\n"
            f"Success criteria:\n{criteria}\n\n"
            f"Produce a detailed, correct execution plan that meets every criterion."
        )
        logger.info("[brain] deep reasoning via VibeMind (Mixture-of-Agents)")
        try:
            bb = await reasoning_core.reason(
                problem=problem, depth=1, max_tokens=3000, user_facing=False
            )
        except Exception as exc:
            logger.warning(
                f"[brain] VibeMind failed ({str(exc)[:70]}) — falling back to single planner"
            )
            return await self._get_model(self._PLANNER_MODEL).generate(
                prompt=(
                    f"Task: {instruction}\n\n"
                    f"Refined spec: {task_json.refined_prompt}\n\n"
                    f"Success criteria:\n{criteria}\n\n"
                    f"Provide a detailed execution plan."
                ),
                system=_BRAIN_SYSTEM,
                max_tokens=3000,
                temperature=0.4,
            )

        # Surface the execution verdict so the manager (Claude or Free Council)
        # reviews against verified ground truth, not assertion.
        v = bb.verdict
        if v is not None and v.method in ("execution", "constraints") and v.ground_truth:
            tag = "✓ verified by execution" if v.verified else "⚠ corrected by execution"
            return (
                f"{bb.final}\n\n"
                f"[VibeMind verification — {tag}, confidence {v.confidence:.0%}]\n"
                f"Ground truth: {v.ground_truth}"
            )
        if v is not None and v.method == "debate" and v.ground_truth:
            return (
                f"{bb.final}\n\n"
                f"[VibeMind — cross-examined via debate, {v.confidence:.0%} of units "
                f"converged]"
            )
        return bb.final

    async def _flash_reason(self, task_json: TaskJSON, instruction: str) -> str:
        """Refinement passes use Flash VibeMind — 4–9s vs 15–25s for full MoA."""
        from core.flash_vibemind import flash_vibemind
        from core.collective_memory import collective_memory

        criteria = "\n".join(f"- {c}" for c in task_json.success_criteria)
        problem  = (
            f"{instruction}\n\n"
            f"Success criteria:\n{criteria}\n\n"
            f"Provide a refined execution plan."
        )
        logger.info("[brain] flash reasoning via Flash VibeMind (refinement pass)")
        try:
            mem_ctx = await collective_memory.retrieve(problem)
            bb = await flash_vibemind.reason(
                problem=problem, max_tokens=2500, mem_ctx=mem_ctx
            )
            return bb.final
        except Exception as exc:
            logger.warning(
                f"[brain] Flash VibeMind failed ({str(exc)[:60]}) — single planner"
            )
            return await self._get_model(self._PLANNER_MODEL).generate(
                prompt=problem,
                system=_BRAIN_SYSTEM,
                max_tokens=2500,
                temperature=0.4,
            )
