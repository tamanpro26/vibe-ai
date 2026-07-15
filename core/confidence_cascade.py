"""
core/confidence_cascade.py — confidence-gated model cascade.

Complements core/peer_consult.py's post-hoc same-tier consultation with a
pre-hoc tiered escalation: run the cheapest/fastest model in a tier list
first, score its answer with an INDEPENDENT verifier model against the
task's own rubric (TaskJSON.success_criteria, or a generic fallback), and
only escalate to the next -- bigger or different-provider -- tier when
that score falls below threshold. Scoring by a separate model rather than
trusting the generating model's own self-report is the whole point: a
model grading its own work is exactly the biased signal an independent
rubric-scoring verifier corrects for.

Most calls should clear the bar on the cheap tier and stop there; only the
genuinely hard fraction should ever reach the expensive tier -- that ratio
(CascadeResult.escalations across calls) is itself worth logging as a
health metric: an escalation rate that creeps toward 100% means the cheap
tier isn't earning its keep, or the threshold is miscalibrated.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from loguru import logger

from models.registry import generate_resilient

DEFAULT_THRESHOLD = 0.65
DEFAULT_VERIFIER = "qwen36_27b_verifier"   # brain team's "Reasoning verifier" — cheap, fast, cross-team reuse

_GENERIC_RUBRIC = [
    "Directly and completely addresses the instruction.",
    "Is technically correct with no obvious errors.",
    "Handles reasonable edge cases, not just the happy path.",
]

_VERIFIER_SYSTEM = """You are a strict independent reviewer in VibeAI. You did \
not write the candidate answer -- grade it, don't rationalize it. Score how \
well it satisfies EVERY rubric point, not just the easy ones. Return ONLY \
this JSON, no prose, no markdown fences:
{"confidence": <0.0-1.0>, "failed_points": ["<rubric point not met>", ...], "reasoning": "<one sentence>"}"""


@dataclass
class CascadeResult:
    output:             str
    model_id:           str
    tier_index:         int
    escalations:        int
    verifier_score:     float
    verifier_reasoning: str
    scores:             list[float] = field(default_factory=list)


async def _score(
    verifier_model: str, instruction: str, rubric: list[str], candidate: str,
) -> tuple[float, str]:
    rubric_block = "\n".join(f"- {r}" for r in rubric)
    raw = await generate_resilient(
        verifier_model,
        prompt=(
            f"TASK:\n{instruction}\n\n"
            f"RUBRIC:\n{rubric_block}\n\n"
            f"CANDIDATE ANSWER:\n{candidate}\n\n"
            f"Score it."
        ),
        system=_VERIFIER_SYSTEM,
        max_tokens=300,
        temperature=0.0,
    )
    match = re.search(r"\{.*\}", raw, re.DOTALL)
    if not match:
        logger.warning(f"[cascade] verifier {verifier_model} returned unparseable output — treating as low confidence")
        return 0.0, "verifier output unparseable"
    try:
        data = json.loads(match.group(0))
        return float(data.get("confidence", 0.0)), str(data.get("reasoning", ""))
    except Exception:
        return 0.0, "verifier JSON malformed"


async def run_cascade(
    tiers: list[str],
    instruction: str,
    system: str,
    rubric: list[str] | None = None,
    verifier_model: str = DEFAULT_VERIFIER,
    threshold: float = DEFAULT_THRESHOLD,
    max_tokens: int = 4000,
    temperature: float = 0.2,
    images: list[str] | None = None,
) -> CascadeResult:
    """Try `tiers` in order (cheap -> expensive); stop at the first one the
    verifier scores >= threshold. Falls through to the last (most capable)
    tier's answer if nothing clears the bar — never silently drops the
    strongest attempt on the table.
    """
    if not tiers:
        raise ValueError("run_cascade requires at least one tier")
    rubric = rubric or _GENERIC_RUBRIC
    scores: list[float] = []
    last_output, last_model = "", tiers[0]

    for i, model_id in enumerate(tiers):
        output = await generate_resilient(
            model_id, prompt=instruction, system=system,
            images=images or [], max_tokens=max_tokens, temperature=temperature,
        )
        last_output, last_model = output, model_id

        try:
            confidence, reasoning = await _score(verifier_model, instruction, rubric, output)
        except Exception as exc:
            # A verifier outage is an infrastructure failure, not a quality
            # signal -- it must not discard an already-successful generation.
            # Accept this tier's output as-is rather than escalating blind
            # (escalating without a real confidence signal would defeat the
            # cascade's cost-control purpose and could walk every remaining
            # tier for nothing if the verifier stays down).
            logger.warning(f"[cascade] verifier unavailable ({str(exc)[:80]}) — accepting {model_id} as-is")
            return CascadeResult(
                output=output, model_id=model_id, tier_index=i,
                escalations=i, verifier_score=0.0,
                verifier_reasoning="verifier unavailable — accepted without scoring",
                scores=scores,
            )
        scores.append(confidence)

        if confidence >= threshold:
            if i > 0:
                logger.info(f"[cascade] {model_id} cleared bar after {i} escalation(s) — confidence={confidence:.2f}")
            return CascadeResult(
                output=output, model_id=model_id, tier_index=i,
                escalations=i, verifier_score=confidence,
                verifier_reasoning=reasoning, scores=scores,
            )

        if i < len(tiers) - 1:
            logger.info(
                f"[cascade] {model_id} confidence={confidence:.2f} < {threshold} "
                f"({reasoning[:60]}) — escalating to {tiers[i + 1]}"
            )

    logger.warning(
        f"[cascade] all {len(tiers)} tier(s) scored below {threshold} "
        f"(best={max(scores):.2f}) — returning most capable tier's answer"
    )
    return CascadeResult(
        output=last_output, model_id=last_model, tier_index=len(tiers) - 1,
        escalations=len(tiers) - 1, verifier_score=scores[-1],
        verifier_reasoning="threshold never cleared — returned strongest tier",
        scores=scores,
    )
