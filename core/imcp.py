"""
core/imcp.py
Inter-Model Communication Protocol — all Pydantic v2 schemas.
Every message in the system is validated against one of these models.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


# ──────────────────────────────────────────────────────────────────────────────
# Enums
# ──────────────────────────────────────────────────────────────────────────────

class MessageType(str, Enum):
    TASK_ASSIGN      = "TASK_ASSIGN"       # Manager → model: assign task
    RESULT_SUBMIT    = "RESULT_SUBMIT"     # Model → manager: completed output
    INTERNAL_HANDOFF = "INTERNAL_HANDOFF"  # Model A → Model B within same team
    REFINE_REQUEST   = "REFINE_REQUEST"    # Manager → model: redo with instruction
    STATUS_UPDATE    = "STATUS_UPDATE"     # Model → manager: progress report
    CLARIFY_REQUEST  = "CLARIFY_REQUEST"   # Model → manager: need more info
    ESCALATE         = "ESCALATE"          # Manager → stronger model
    APPROVE          = "APPROVE"           # Manager → pipeline: output accepted
    PARALLEL_SPAWN   = "PARALLEL_SPAWN"    # Manager → multiple teams simultaneously
    CONTEXT_SYNC     = "CONTEXT_SYNC"      # Manager → all teams: state updated


class TaskType(str, Enum):
    DEBUGGING       = "debugging"
    VIBE_CODING     = "vibe_coding"
    UI_DESIGN       = "ui_design"
    ANIMATION       = "animation"
    VIDEO_ANALYSIS  = "video_analysis"
    MIXED           = "mixed"


class Complexity(str, Enum):
    SIMPLE   = "simple"
    MODERATE = "moderate"
    COMPLEX  = "complex"


class Priority(int, Enum):
    CRITICAL = 1
    HIGH     = 2
    NORMAL   = 3
    LOW      = 4
    BULK     = 5


# ──────────────────────────────────────────────────────────────────────────────
# Shared sub-models
# ──────────────────────────────────────────────────────────────────────────────

class ModelRef(BaseModel):
    """Identifies a sender or receiver."""
    model: str          # logical model_id from models_config.py
    team: str           # team name
    role: str = ""      # human-readable sub-role (optional)


class RoutingConfig(BaseModel):
    """How this message should be handled and replied to."""
    reply_to: str = "claude_sonnet_4.6"     # always back to manager
    fallback_to: str = ""                    # fallback model_id if primary fails
    timeout_ms: int = 30_000
    iteration: int = 1
    max_iter: int = 3


class Payload(BaseModel):
    """Generic payload — typed subclasses override for specific message types."""
    instruction: str = ""
    task_json_ref: str = ""             # e.g. "t_abc123.team_instructions.code"
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    token_budget: int = 8_000
    extra: dict[str, Any] = Field(default_factory=dict)


# ──────────────────────────────────────────────────────────────────────────────
# Message Envelope — every inter-model message uses this
# ──────────────────────────────────────────────────────────────────────────────

class IMCPMessage(BaseModel):
    """Universal message envelope. Every model call produces or consumes this."""
    msg_id: str = Field(default_factory=lambda: f"m_{uuid.uuid4().hex[:8]}")
    parent_msg_id: str | None = None
    session_id: str = ""
    task_id: str = ""
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    type: MessageType
    priority: Priority = Priority.NORMAL
    from_: ModelRef = Field(alias="from")
    to: ModelRef
    payload: Payload = Field(default_factory=Payload)
    routing: RoutingConfig = Field(default_factory=RoutingConfig)

    model_config = {"populate_by_name": True}


# ──────────────────────────────────────────────────────────────────────────────
# Task JSON — output of the Prompt Refiner pipeline
# ──────────────────────────────────────────────────────────────────────────────

class TeamActivation(BaseModel):
    """Per-team activation status inside the Task JSON."""
    active: bool
    models: list[str] = Field(default_factory=list)   # model_ids to use
    instruction: str = ""
    priority: Priority = Priority.NORMAL
    reason: str = ""    # populated when active=False


class Classification(BaseModel):
    primary_type: TaskType
    sub_types: list[str] = Field(default_factory=list)
    complexity: Complexity = Complexity.MODERATE
    confidence: float = Field(ge=0.0, le=1.0, default=0.9)


class TaskContext(BaseModel):
    tech_stack: list[str] = Field(default_factory=list)
    inferred_framework: str | None = None
    requires_execution: bool = False
    requires_visual: bool = False
    constraints: list[str] = Field(default_factory=list)


class TaskJSON(BaseModel):
    """
    The structured output of the Prompt Refiner pipeline.
    Claude Sonnet 4.6 reads this to dispatch tasks — no re-reasoning needed.
    """
    task_id: str = Field(default_factory=lambda: f"t_{uuid.uuid4().hex[:8]}")
    session_id: str = ""
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    version: str = "1.0"

    # Raw vs refined prompt
    original_prompt: str
    refined_prompt: str = ""

    # Classification (set by Qwen3-30B intent parser)
    classification: Classification = Field(
        default_factory=lambda: Classification(primary_type=TaskType.VIBE_CODING)
    )

    # Team activation (Claude reads this to decide who works)
    active_teams: dict[str, TeamActivation] = Field(default_factory=dict)

    # Per-team instructions (Cogito formats these)
    team_instructions: dict[str, str] = Field(default_factory=dict)

    # Success criteria (Claude checks outputs against these)
    success_criteria: list[str] = Field(default_factory=list)

    context: TaskContext = Field(default_factory=TaskContext)

    @model_validator(mode="after")
    def ensure_router_always_active(self) -> "TaskJSON":
        """Router team is always active regardless of task type."""
        if "router" not in self.active_teams:
            self.active_teams["router"] = TeamActivation(
                active=True,
                models=["gpt_oss_120b_dispatch"],
                instruction="Route messages between active teams.",
                priority=Priority.HIGH,
            )
        return self


# ──────────────────────────────────────────────────────────────────────────────
# Typed payload sub-models for specific message types
# ──────────────────────────────────────────────────────────────────────────────

class TaskAssignPayload(Payload):
    """Payload for TASK_ASSIGN messages from manager to model."""
    task_json: TaskJSON
    team_key: str           # which team this model belongs to


class ResultSubmitPayload(Payload):
    """Payload for RESULT_SUBMIT messages from model back to manager."""
    output: str             # the model's actual output
    confidence: float = Field(ge=0.0, le=1.0, default=0.85)
    verification_status: str = "unverified"
    tokens_used: int = 0
    model_id: str = ""


class RefineRequestPayload(Payload):
    """Payload for REFINE_REQUEST when Claude's review loop rejects output."""
    approved: bool = False
    quality_score: float = Field(ge=0.0, le=1.0, default=0.0)
    criteria_failed: list[str] = Field(default_factory=list)
    issues_found: list[str] = Field(default_factory=list)
    refine_instruction: str = ""
    iteration: int = 1
    max_iter: int = 3


class ReviewResult(BaseModel):
    """Structured output from Claude's quality gate review."""
    task_id: str
    model_id: str
    approved: bool
    quality_score: float
    criteria_passed: list[str] = Field(default_factory=list)
    criteria_failed: list[str] = Field(default_factory=list)
    issues: list[str] = Field(default_factory=list)
    refine_instruction: str = ""
    action: Literal["APPROVE", "REFINE", "ESCALATE"] = "REFINE"


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def make_task_assign(
    session_id: str,
    task_json: TaskJSON,
    from_model: str,
    to_model: str,
    to_team: str,
    to_role: str = "",
    iteration: int = 1,
    fallback: str = "",
) -> IMCPMessage:
    """Factory for TASK_ASSIGN messages."""
    return IMCPMessage(
        session_id=session_id,
        task_id=task_json.task_id,
        type=MessageType.TASK_ASSIGN,
        **{"from": ModelRef(model=from_model, team="manager", role="manager_controller")},
        to=ModelRef(model=to_model, team=to_team, role=to_role),
        payload=TaskAssignPayload(
            instruction=task_json.team_instructions.get(to_team, ""),
            task_json=task_json,
            team_key=to_team,
        ),
        routing=RoutingConfig(
            reply_to="claude_sonnet_4.6",
            fallback_to=fallback,
            iteration=iteration,
        ),
    )


def make_refine_request(
    parent_msg: IMCPMessage,
    review: ReviewResult,
) -> IMCPMessage:
    """Factory for REFINE_REQUEST messages after a failed review."""
    return IMCPMessage(
        session_id=parent_msg.session_id,
        task_id=parent_msg.task_id,
        parent_msg_id=parent_msg.msg_id,
        type=MessageType.REFINE_REQUEST,
        **{"from": ModelRef(model="claude_sonnet_4.6", team="manager")},
        to=parent_msg.from_,
        payload=RefineRequestPayload(
            approved=False,
            quality_score=review.quality_score,
            criteria_failed=review.criteria_failed,
            issues_found=review.issues,
            refine_instruction=review.refine_instruction,
            iteration=parent_msg.routing.iteration + 1,
            max_iter=parent_msg.routing.max_iter,
        ),
        routing=RoutingConfig(
            reply_to="claude_sonnet_4.6",
            fallback_to=parent_msg.routing.fallback_to,
            iteration=parent_msg.routing.iteration + 1,
            max_iter=parent_msg.routing.max_iter,
        ),
    )
