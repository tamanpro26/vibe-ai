"""
core/peer_consult.py — confidence-triggered peer help.

Different from the existing verification machinery in core/verifier.py
(execution/consensus grounding, always run for checkable math/logic
problems) and from teams/code.py's fixed best-of-N / parallel-debug
strategies (chosen up front by task complexity). This module is a
lightweight, OPT-IN, generic primitive any team can use for the case the
existing systems don't cover: a model finishes an answer and is itself
unsure about ONE specific part of it, on a task that isn't a checkable
math/logic problem and wasn't already routed through best-of-N.

Protocol: a team's system prompt may invite the model to end its answer
with a machine-parseable tag (see CONFIDENCE_PROMPT_SUFFIX). If present
and below `threshold`, 1-2 same-team peers are asked for a short, focused
second opinion on just the flagged part (not a full redo), and their
input is handed back to the ORIGINAL model to produce a final answer —
mirroring how a human would go "not sure about this bit, let me ask
someone" rather than starting over. Teams that never emit the tag are
completely unaffected (parse finds nothing, output passes through as-is).
"""
from __future__ import annotations

import asyncio
import re

from loguru import logger

from config.models_config import MODEL_REGISTRY
from models.registry import generate_resilient

DEFAULT_THRESHOLD = 0.6
MAX_PEERS = 2

# Same free/text-capable provider set core registry.py's generate_resilient
# already trusts for automatic cross-model substitution — keeps peer
# selection consistent with the one policy call already made there
# (anthropic/mistral manual-only, pollinations/huggingface are image/asset
# generators with nothing useful to say about a text draft).
_TEXT_PEER_PROVIDERS = {"google", "groq", "cerebras", "openrouter", "ollama", "nvidia", "zai"}

CONFIDENCE_PROMPT_SUFFIX = """

If any part of your answer is genuinely uncertain (an edge case you're not \
sure is handled, a fact you're not confident about, a design choice you're \
guessing at), end your response with exactly these two lines:
CONFIDENCE: <a number from 0.0 to 1.0> (model={model_id})
UNCERTAIN: <one sentence naming the specific part you're unsure about>
Omit both lines entirely if you're confident in the whole answer — don't \
use this to hedge on easy tasks, only for genuine uncertainty."""

_PEER_HELP_SYSTEM = (
    "You are giving a second opinion inside VibeAI. A teammate model drafted an "
    "answer and flagged uncertainty about one specific part. Address ONLY that "
    "part: say whether it's right, wrong, or needs a specific correction. Keep it "
    "short — you are not rewriting the whole answer."
)

_FINALIZE_SYSTEM = (
    "You previously drafted an answer and flagged uncertainty about one part of "
    "it. Peers have now weighed in on that specific part. Produce your FINAL "
    "answer: adopt a peer's correction only if it's actually right, otherwise "
    "keep your original approach. Do not mention peers or this review process "
    "in the final answer."
)

_TAG_RE = re.compile(
    r"CONFIDENCE:\s*([01](?:\.\d+)?)\s*\(model=([\w][\w.\-]*)\)", re.IGNORECASE
)
_UNCERTAIN_RE = re.compile(r"UNCERTAIN:\s*(.+)", re.IGNORECASE)


def _parse(output: str) -> tuple[str, float | None, str, str]:
    """Extract and strip the CONFIDENCE/UNCERTAIN tag, if present.

    Returns (cleaned_text, confidence_or_None, model_id, uncertain_note).
    """
    cm = _TAG_RE.search(output)
    if not cm:
        return output, None, "", ""
    um = _UNCERTAIN_RE.search(output)
    spans = sorted([cm.span()] + ([um.span()] if um else []), reverse=True)
    cleaned = output
    for start, end in spans:
        cleaned = cleaned[:start] + cleaned[end:]
    return cleaned.rstrip(), float(cm.group(1)), cm.group(2), (um.group(1).strip() if um else "")


def _pick_peers(team_name: str, exclude_model_id: str) -> list[str]:
    peers = [
        mid for mid, d in MODEL_REGISTRY.items()
        if d.team == team_name and mid != exclude_model_id and d.provider in _TEXT_PEER_PROVIDERS
    ]
    return peers[:MAX_PEERS]


async def _ask_peer(peer_model_id: str, instruction: str, draft: str, uncertain: str) -> str:
    return await generate_resilient(
        peer_model_id,
        prompt=(
            f"ORIGINAL TASK:\n{instruction}\n\n"
            f"A TEAMMATE'S DRAFT ANSWER:\n{draft}\n\n"
            f"THE TEAMMATE IS UNSURE ABOUT:\n{uncertain or '(not specified — skim the whole draft)'}\n\n"
            f"Give a short, focused second opinion on that specific point only."
        ),
        system=_PEER_HELP_SYSTEM,
        max_tokens=400,
        temperature=0.3,
    )


async def consult_if_unsure(
    team_name: str,
    instruction: str,
    output: str,
    threshold: float = DEFAULT_THRESHOLD,
) -> str:
    """Return `output` unchanged (tag stripped) unless it self-reports low
    confidence, in which case peers are consulted and the original model
    finalizes with their input before this returns.
    """
    cleaned, confidence, model_id, uncertain = _parse(output)
    if confidence is None or confidence >= threshold or not model_id:
        return cleaned

    peers = _pick_peers(team_name, model_id)
    if not peers:
        logger.info(
            f"[peer_consult] {model_id} confidence={confidence:.2f} but no "
            f"peers available in team={team_name} — keeping original"
        )
        return cleaned

    logger.info(
        f"[peer_consult] {model_id} confidence={confidence:.2f} < {threshold} "
        f"on '{uncertain[:60]}' — consulting {peers}"
    )
    opinions = await asyncio.gather(
        *(_ask_peer(p, instruction, cleaned, uncertain) for p in peers),
        return_exceptions=True,
    )
    notes = [
        f"--- {p} ---\n{o}" for p, o in zip(peers, opinions)
        if not isinstance(o, Exception) and o
    ]
    if not notes:
        logger.warning(f"[peer_consult] all peers failed — keeping {model_id}'s original answer")
        return cleaned

    try:
        final = await generate_resilient(
            model_id,
            prompt=(
                f"ORIGINAL TASK:\n{instruction}\n\n"
                f"YOUR DRAFT:\n{cleaned}\n\n"
                f"YOU FLAGGED UNCERTAINTY ABOUT: {uncertain or '(unspecified)'}\n\n"
                f"PEER OPINIONS ON THAT POINT:\n" + "\n\n".join(notes) + "\n\n"
                f"Produce your FINAL answer."
            ),
            system=_FINALIZE_SYSTEM,
            max_tokens=2000,
            temperature=0.2,
        )
        logger.info(f"[peer_consult] {model_id} finalized after peer input from {peers}")
        return final
    except Exception as exc:
        logger.warning(f"[peer_consult] finalize call failed ({str(exc)[:80]}) — keeping original")
        return cleaned
