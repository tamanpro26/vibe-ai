"""
teams/leadership.py — per-team Leader review.

Every team gets a designated Leader: its own strongest, already-proven
model, elevated into an explicit decision-making role. Distinct from the
other two confidence mechanisms already in this codebase:

  - core/peer_consult.py    — OPT-IN, only fires when a model self-reports
                              low confidence via a tag. Most teams never
                              emit the tag, so it never fires for them.
  - core/confidence_cascade.py — generation-TIME cost/quality tradeoff,
                              wired into one team's one path (Code team's
                              SIMPLE/MODERATE generation).

The Leader is neither: it is a MANDATORY final check on every team's
output, regardless of self-reported confidence or which generation path
produced it. It has real authority — approve, or send the work back with
a specific instruction — mirroring how a human team lead reviews work
before it ships, not just another opinion in the mix.

Bounded to one revision retry per team run, matching this project's
established fix-cycle cap convention (core/agent_loop.py's reflexion
cycles, core/confidence_cascade.py's escalation, manager/supervisor.py's
stage checkpoints) — a Leader that keeps rejecting needs the Manager's
attention, not an unbounded loop.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

from models.registry import generate_resilient

# Each team's own already-proven-reliable model, elevated into an explicit
# leadership role rather than left as implicit "whichever model happened to
# run." Design borrows Vision's model: flux_asset (and Pollinations image
# generation generally) has no self-review capability of its own -- it is a
# pure generator -- so the system's one real "can look at an image" model
# reviews what Design produced instead.
TEAM_LEADERS: dict[str, str] = {
    "brain":  "gemini_flash",          # already "Master orchestrator"
    "code":   "glm_47_cerebras",       # already "Full-stack generator", 0% observed failure rate
    "vision": "gemini_flash_vision",   # already "UI screenshot critic"
    "design": "gemini_flash_vision",   # borrowed -- Design has no vision model of its own
    "router": "gpt_oss_120b_dispatch", # already "Primary dispatcher"
}

# What each team is actually supposed to produce, as distinct from "solve
# the user's whole original request." Found load-bearing via live testing
# (2026-07-16): without this, the Router Leader rejected a CORRECT routing
# classification ({"task_type": "vibe_coding", ...}) because it judged the
# output against the raw user request ("write a prime-checking function")
# instead of Router's real job -- classify, don't solve. Same failure shape
# would hit any team whose `instruction` argument is the top-level request
# rather than a team-scoped one.
TEAM_MANDATES: dict[str, str] = {
    "brain":  "Produce a final coherent answer or plan for the user's request.",
    "code":   "Produce working code (functions, files, or diffs) that implements the request.",
    "vision": "Produce an analysis or critique of an image/video/screenshot -- not code.",
    "design": "Produce a visual asset or its generation instructions -- not code.",
    "router": "Produce ONLY a routing/classification decision (task_type, complexity, "
              "which teams are needed) -- NOT a solution to the user's request itself.",
}

_LEADER_SYSTEM = """You are the {team} team's Leader in VibeAI -- the senior
decision-maker for this team's work, not just another reviewer. You have
real authority: approve this output, or send it back with ONE specific,
actionable instruction for what must change.

THIS TEAM'S ACTUAL JOB: {mandate}

Judge the output against THIS TEAM'S ACTUAL JOB above, not against whether
it single-handedly solves the user's entire original request -- other
teams handle the other parts of that. Do not invent additional
requirements the team was never asked to meet. Return ONLY this JSON, no
prose, no markdown fences:
{{"approved": true/false, "reasoning": "one sentence", "revision_instruction": ""}}
revision_instruction must be non-empty when approved is false, and empty
when approved is true."""

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)
_LOG_PATH = Path("./logs/leader_verdicts.jsonl")


@dataclass
class LeaderVerdict:
    approved:            bool
    reasoning:           str
    revision_instruction: str


def _fallback_verdict(reason: str) -> LeaderVerdict:
    """Fail open: a Leader outage (or no Leader configured for this team)
    must never block the team's work -- absence of review is not evidence
    of a problem."""
    return LeaderVerdict(approved=True, reasoning=reason, revision_instruction="")


async def leader_review(team_name: str, instruction: str, output: str) -> LeaderVerdict:
    """One review call against the team's designated Leader model. Never
    raises -- same fail-open bias as every other best-effort side channel
    in this codebase (tools/memory.py, core/peer_consult.py,
    manager/supervisor.py)."""
    leader_model = TEAM_LEADERS.get(team_name)
    if not leader_model:
        return _fallback_verdict(f"no Leader configured for team '{team_name}'")

    mandate = TEAM_MANDATES.get(team_name, "Fulfill the instruction below.")
    try:
        raw = await generate_resilient(
            leader_model,
            prompt=f"INSTRUCTION:\n{instruction}\n\nTEAM OUTPUT:\n{output[:3000]}",
            system=_LEADER_SYSTEM.format(team=team_name, mandate=mandate),
            max_tokens=300,
            temperature=0.1,
        )
        match = _JSON_RE.search(raw)
        if not match:
            logger.warning(f"[leadership] {team_name}: unparseable verdict — approving by default")
            return _fallback_verdict("unparseable Leader response")
        data = json.loads(match.group(0))
        approved = bool(data.get("approved", True))
        return LeaderVerdict(
            approved=approved,
            reasoning=str(data.get("reasoning", ""))[:300],
            revision_instruction=str(data.get("revision_instruction", ""))[:500] if not approved else "",
        )
    except Exception as exc:
        logger.warning(f"[leadership] {team_name} Leader review failed ({str(exc)[:80]}) — approving by default")
        return _fallback_verdict(f"Leader unavailable: {str(exc)[:80]}")


def log_verdict(team_name: str, verdict: LeaderVerdict) -> None:
    """Best-effort record for the CEO's aggregate oversight reports
    (manager/ceo.py). Logging must never affect the team run it's
    observing."""
    try:
        _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "ts":       time.time(),
            "team":     team_name,
            "approved": verdict.approved,
            "reasoning": verdict.reasoning,
        }
        with open(_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
    except Exception as exc:
        logger.warning(f"[leadership] verdict logging skipped: {str(exc)[:60]}")
