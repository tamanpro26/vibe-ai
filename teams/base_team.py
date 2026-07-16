"""
teams/base_team.py
Abstract base class for all 6 specialist teams.
Every team has the same interface so the manager can call them uniformly.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from loguru import logger

from core.imcp import TaskJSON
from core.peer_consult import consult_if_unsure
from models.registry import registry, generate_resilient
from teams.leadership import leader_review, log_verdict


class _ResilientModel:
    """
    Drop-in for a connector's generate(): same interface, but a dead
    model/provider automatically fails over to teammates via
    registry.generate_resilient instead of crashing the team.
    """

    def __init__(self, model_id: str) -> None:
        self.model_id = model_id

    async def generate(self, **kwargs) -> str:
        return await generate_resilient(self.model_id, **kwargs)


class BaseTeam(ABC):
    """
    Every team must implement `run()`.
    Teams have access to the model registry and can call any model they need.
    """

    team_name: str = "base"

    async def run(
        self,
        task_json: TaskJSON,
        instruction: str = "",
        iteration: int = 1,
        extra: dict[str, Any] | None = None,
    ) -> str:
        """
        Execute the team's task.
        Returns the output string for the manager to review.
        """
        extra = extra or {}
        logger.info(
            f"[{self.team_name}] starting | "
            f"task={task_json.task_id} | iter={iteration}"
        )
        result = await self._execute(task_json, instruction, iteration, extra)
        # No-op unless the team's system prompt invited a CONFIDENCE tag
        # (see core/peer_consult.py) and the model actually flagged low
        # confidence on part of its answer.
        result = await consult_if_unsure(self.team_name, instruction, result)

        # Mandatory Leader sign-off (teams/leadership.py) -- unlike the
        # CONFIDENCE-tag path above, this runs every time regardless of
        # self-reported confidence. Bounded to one revision retry so a
        # Leader that keeps rejecting can't loop the team forever.
        verdict = await leader_review(self.team_name, instruction, result)
        log_verdict(self.team_name, verdict)
        if not verdict.approved:
            logger.info(
                f"[{self.team_name}] Leader sent work back: {verdict.reasoning}"
            )
            revised_instruction = (
                f"{instruction}\n\n"
                f"Your team Leader reviewed your previous attempt and sent it back "
                f"with this required fix: {verdict.revision_instruction}"
            )
            result = await self._execute(task_json, revised_instruction, iteration, extra)
            result = await consult_if_unsure(self.team_name, instruction, result)

        logger.info(
            f"[{self.team_name}] done | output_len={len(result)} chars"
        )
        return result

    @abstractmethod
    async def _execute(
        self,
        task_json: TaskJSON,
        instruction: str,
        iteration: int,
        extra: dict[str, Any],
    ) -> str: ...

    def _get_model(self, model_id: str) -> _ResilientModel:
        return _ResilientModel(model_id)

    def _instruction(self, task_json: TaskJSON) -> str:
        return task_json.team_instructions.get(self.team_name, task_json.refined_prompt)
