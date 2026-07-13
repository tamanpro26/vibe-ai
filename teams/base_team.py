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
from models.registry import registry, generate_resilient


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
