"""
manager/intent_contract.py — the Free Manager Council's shared intent record.

NOT a fix for "the pipeline forgets what the user asked" — manager/free_manager.py
already threads the verbatim ORIGINAL REQUEST into every one of its 5 stages
(Planner/Drafter/Critic/Refiner/Synthesizer all receive it directly). What's
actually missing is a STRUCTURED read of that request built ONCE — a goal,
testable success criteria, constraints, explicit non-goals, and open
ambiguities — so:
  - the ambiguity gate (below) can block dispatch on a genuinely unclear ask
    instead of the Council silently picking an interpretation
  - ExecutionSupervisor (manager/supervisor.py) has something concrete to
    check each stage's output against, not just "does this seem fine"
  - the Critic has criteria to cite instead of a vibe

Frozen so no stage can quietly rewrite it out from under the others.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from loguru import logger

from models.registry import generate_resilient

# Cheap, fast, generous free-tier quota (Llama 3.1 8B on Groq) -- this is a
# classification task, not content generation, so a small model is the
# right tool, not a cost-cutting compromise.
_CONTRACT_MODEL = "llama31_8b_router"

_CONTRACT_SYSTEM = """You read a user's request and extract a structured intent
record. Do not answer the request -- only analyze it. Return ONLY this JSON,
no prose, no markdown fences:
{
  "goal": "<one sentence: what the user actually wants>",
  "success_criteria": ["<user-visible, checkable outcome>", ...],
  "constraints": ["<explicit tech/style/format requirement or 'don't'>", ...],
  "non_goals": ["<explicitly out of scope, if the request implies any>", ...],
  "open_ambiguities": ["<a genuinely material question whose answer would change what gets built>", ...]
}
open_ambiguities is for MATERIAL ambiguity only -- something where guessing
wrong means building the wrong thing (which framework, which of two
conflicting requirements wins, a scope boundary left undefined). Do not list
stylistic judgment calls a competent builder would just make (color choices,
minor wording, obvious defaults). Most requests have zero entries here --
only flag it when you would genuinely have to guess."""

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


@dataclass(frozen=True)
class IntentContract:
    raw_request:      str                    # user's verbatim words, never paraphrased
    goal:             str        = ""        # one-sentence interpretation
    success_criteria: list[str]  = field(default_factory=list)
    constraints:      list[str]  = field(default_factory=list)
    non_goals:        list[str]  = field(default_factory=list)
    open_ambiguities: list[str]  = field(default_factory=list)

    @property
    def has_open_ambiguities(self) -> bool:
        return bool(self.open_ambiguities)

    def as_prompt_block(self) -> str:
        """Rendered form injected into every downstream stage prompt under a
        fixed header, so a stage can't mistake it for part of the request."""
        lines = [f"INTENT CONTRACT (do not violate; do not rewrite):",
                 f"Goal: {self.goal}"]
        if self.success_criteria:
            lines.append("Success criteria:")
            lines += [f"  - {c}" for c in self.success_criteria]
        if self.constraints:
            lines.append("Constraints:")
            lines += [f"  - {c}" for c in self.constraints]
        if self.non_goals:
            lines.append("Explicitly out of scope:")
            lines += [f"  - {n}" for n in self.non_goals]
        return "\n".join(lines)


def _fallback_contract(raw_request: str) -> IntentContract:
    """Used when extraction fails -- never blocks the Council on an
    infrastructure hiccup. No ambiguities flagged (fail open, not closed):
    an extraction failure is not evidence the request is actually ambiguous."""
    return IntentContract(raw_request=raw_request, goal=raw_request[:200])


async def build_intent_contract(raw_request: str) -> IntentContract:
    """One cheap model call, converting the raw request into a structured,
    checkable record. Never raises -- infrastructure failure here degrades to
    a bare-goal contract with no ambiguities, so a flaky classifier call can
    never itself block the pipeline.
    """
    try:
        raw = await generate_resilient(
            _CONTRACT_MODEL,
            prompt=f"USER REQUEST:\n{raw_request}",
            system=_CONTRACT_SYSTEM,
            max_tokens=500,
            temperature=0.1,
        )
        match = _JSON_RE.search(raw)
        if not match:
            logger.warning("[intent_contract] unparseable extraction — falling back to bare goal")
            return _fallback_contract(raw_request)
        data = json.loads(match.group(0))
        return IntentContract(
            raw_request=raw_request,
            goal=str(data.get("goal", ""))[:300] or raw_request[:200],
            success_criteria=[str(c) for c in data.get("success_criteria", []) if str(c).strip()][:10],
            constraints=[str(c) for c in data.get("constraints", []) if str(c).strip()][:10],
            non_goals=[str(c) for c in data.get("non_goals", []) if str(c).strip()][:10],
            open_ambiguities=[str(c) for c in data.get("open_ambiguities", []) if str(c).strip()][:5],
        )
    except Exception as exc:
        logger.warning(f"[intent_contract] extraction failed ({str(exc)[:80]}) — falling back to bare goal")
        return _fallback_contract(raw_request)
