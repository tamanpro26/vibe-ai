"""
core/comparison_judge.py
CePO-scoped comparison-based judging for fix cycles. When a blind single-shot
retry has ALREADY failed once on the same findings, generate two independent
candidate fix STRATEGIES (short text plans, not file edits) from two
different models, then have a third model judge which is actually more
likely to resolve every listed finding. The winning strategy is handed to
the acting model as guidance text; it still performs the real edits itself
through its normal tool-calling turn.

Deliberately scoped down from the full CePO paper (which compares many full
completions and rewrites the reasoning chain): this compares short plan-text
against plan-text for the SPECIFIC recurring findings, which is cheap (small
max_tokens, no tool schema, 2-3 short calls total) and fits directly into the
existing single-model tool-calling loop without needing to fork workspace
state to actually execute two competing patches.

Only worth the extra cost on the tail where blind retry already failed once —
callers must gate this on cycle count, never call it on a first attempt.
Fails soft at every step: this must never block or crash the agent run.
"""
from __future__ import annotations

import asyncio

from loguru import logger

# Three DIFFERENT model_ids on purpose: candidates come from two distinct
# providers/models (real second opinions, not the same model re-sampled),
# and the judge is a third, separate model — asking a model to grade its own
# proposal is a well-known self-preference bias in LLM-judge setups.
_CANDIDATE_A_MODEL = "gemini_flash"
_CANDIDATE_B_MODEL = "gpt_oss_120b_debug"
_JUDGE_MODEL       = "llama33_70b_coder"


async def propose_and_pick_fix_strategy(findings: list[str], context: str) -> str | None:
    """
    Returns a short winning fix strategy (plain text) to inject as guidance,
    or None if any step of this pipeline is unavailable/fails — callers must
    fall back to the plain findings list on None.
    """
    from models.registry import generate_resilient

    findings_text = "\n".join(f"- {f}" for f in findings[:10])
    prompt = (
        "A previous fix attempt for this project did NOT resolve these "
        "automated findings (they recurred):\n"
        f"{findings_text}\n\n"
        f"Relevant context:\n{context[:1500]}\n\n"
        "In 3-5 sentences, propose a SPECIFIC strategy to actually fix all "
        "of these (name the files/approach, not generic advice like 'review "
        "the code'). Do not write full code — just the concrete plan. Be "
        "terse: no filler, no restating the findings back, name exact "
        "files/approach directly — this text gets fed into a later prompt."
    )

    try:
        candidate_a, candidate_b = await asyncio.gather(
            generate_resilient(_CANDIDATE_A_MODEL, prompt=prompt, max_tokens=300, temperature=0.3),
            generate_resilient(_CANDIDATE_B_MODEL, prompt=prompt, max_tokens=300, temperature=0.7),
            return_exceptions=True,
        )
    except Exception as exc:
        logger.warning(f"[comparison-judge] candidate generation errored: {str(exc)[:80]}")
        return None

    a_ok = isinstance(candidate_a, str) and candidate_a.strip()
    b_ok = isinstance(candidate_b, str) and candidate_b.strip()
    if not a_ok and not b_ok:
        logger.warning("[comparison-judge] both candidates failed — falling back to plain retry")
        return None
    if not b_ok:
        return candidate_a.strip()
    if not a_ok:
        return candidate_b.strip()

    judge_prompt = (
        "Two engineers each proposed a fix strategy for the same recurring "
        f"issues:\n{findings_text}\n\n"
        f"STRATEGY A:\n{candidate_a}\n\nSTRATEGY B:\n{candidate_b}\n\n"
        "Which strategy is more likely to ACTUALLY fix all the listed "
        "findings, and is concrete enough to execute directly? Reply with "
        "exactly 'A' or 'B' on the first line, then one sentence why."
    )
    try:
        verdict = await generate_resilient(_JUDGE_MODEL, prompt=judge_prompt, max_tokens=100, temperature=0.1)
    except Exception as exc:
        logger.warning(f"[comparison-judge] judge call failed, defaulting to candidate A: {str(exc)[:80]}")
        return candidate_a.strip()

    verdict = (verdict or "").strip()
    winner_is_b = verdict.upper().lstrip().startswith("B")
    winner = candidate_b if winner_is_b else candidate_a
    logger.info(f"[comparison-judge] picked strategy {'B' if winner_is_b else 'A'}: {verdict[:80]}")
    return winner.strip()
