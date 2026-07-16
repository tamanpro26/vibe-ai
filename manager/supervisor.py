"""
manager/supervisor.py — stage-checkpoint supervision for the Free Manager Council.

The real gap this closes: the Council's 5 stages (manager/free_manager.py)
run as one unsupervised sequential relay -- Plan -> Draft -> Critique ->
Refine -> Polish -- and nothing checks alignment with the user's actual
intent until the Critic stage, by which point a drifted Draft has already
been spent. There is no long-running sub-agent to send progress events here
(each stage is one model call, not a multi-step autonomous task) -- so this
is NOT an event-queue/heartbeat system watching work-in-progress. It is a
checkpoint: after each stage produces output, before the pipeline commits to
the next stage, a cheap model compares that output against the
IntentContract and recommends continue / correct / escalate. That is the
real value the "frequent supervision" idea was after, mapped onto what this
Council actually is.

Bounded by design: at most one correction retry and one model-tier
escalation per stage. Uncapped retries on the same failure shape is exactly
the infinite-loop risk this project's own error-taxonomy already warns
about elsewhere (core/agent_loop.py's reflexion cycles, verifier fix
cycles -- both hard-capped for the same reason).
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from loguru import logger

from manager.intent_contract import IntentContract
from models.registry import generate_resilient

_SUPERVISOR_MODEL = "llama31_8b_router"   # cheap, fast, generous free quota -- see intent_contract.py
_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)

_SUPERVISOR_SYSTEM = """You are a supervision check inside VibeAI's Free Manager
Council. Compare a pipeline stage's output against the intent contract it was
supposed to satisfy. You are not grading writing quality -- you are checking
whether this stage drifted from what the user actually asked for.
Return ONLY this JSON, no prose, no markdown fences:
{
  "intent_alignment": <integer 0-10>,
  "criteria_on_track": <true/false>,
  "drift_detected": <true/false>,
  "drift_description": "<one sentence, empty string if no drift>",
  "recommend": "continue" | "correct" | "escalate"
}
"continue" when aligned. "correct" when a specific, fixable drift exists a
retry with guidance can address. "escalate" when the output ignores the
contract's constraints/success_criteria in a way a same-tier retry is
unlikely to fix (recommend this, don't just lower the score, when you're
genuinely unsure a retry would help)."""


@dataclass
class SupervisionVerdict:
    intent_alignment:   int
    criteria_on_track:  bool
    drift_detected:     bool
    drift_description:  str
    recommend:          Literal["continue", "correct", "escalate"]


def _fallback_verdict() -> SupervisionVerdict:
    """Fail open: an unavailable supervisor must never block or degrade the
    pipeline it's supervising -- absence of a check is not evidence of drift."""
    return SupervisionVerdict(
        intent_alignment=10, criteria_on_track=True,
        drift_detected=False, drift_description="", recommend="continue",
    )


async def supervisor_ping(
    contract: IntentContract, stage_role: str, stage_output: str,
) -> SupervisionVerdict:
    """One cheap model call checking a stage's output against the contract.
    Never raises -- a supervisor outage degrades to "continue", the same
    bias every other best-effort side channel in this codebase uses
    (tools/memory.py, core/peer_consult.py), so a flaky classifier can never
    itself stall or fail the Council.
    """
    try:
        summary = stage_output[:600]   # capped self-summary, not a raw transcript
        raw = await generate_resilient(
            _SUPERVISOR_MODEL,
            prompt=(
                f"{contract.as_prompt_block()}\n\n"
                f"STAGE: {stage_role}\n"
                f"STAGE OUTPUT (truncated to 600 chars):\n{summary}"
            ),
            system=_SUPERVISOR_SYSTEM,
            max_tokens=250,
            temperature=0.0,
        )
        match = _JSON_RE.search(raw)
        if not match:
            logger.warning(f"[supervisor] unparseable verdict for {stage_role} — treating as aligned")
            return _fallback_verdict()
        data = json.loads(match.group(0))
        recommend = data.get("recommend", "continue")
        if recommend not in ("continue", "correct", "escalate"):
            recommend = "continue"
        return SupervisionVerdict(
            intent_alignment=int(data.get("intent_alignment", 10)),
            criteria_on_track=bool(data.get("criteria_on_track", True)),
            drift_detected=bool(data.get("drift_detected", False)),
            drift_description=str(data.get("drift_description", ""))[:300],
            recommend=recommend,
        )
    except Exception as exc:
        logger.warning(f"[supervisor] ping failed for {stage_role} ({str(exc)[:80]}) — treating as aligned")
        return _fallback_verdict()


# ── Calibration logging ──────────────────────────────────────────────────────
#
# This is logging, not calibration -- there is no accumulated data yet to
# calibrate a threshold against, and building an analysis system with zero
# rows to analyze would be theater. What this DOES do: give every verdict a
# durable record (stage, scores, recommendation, and -- once the caller knows
# it -- the eventual outcome) so a real calibration pass has something to
# read after enough real Council runs accumulate.

_LOG_PATH = Path("./logs/supervisor_verdicts.jsonl")


def log_verdict(stage_role: str, verdict: SupervisionVerdict, outcome: str = "") -> None:
    """Append one verdict record. Best-effort -- logging must never affect
    the pipeline it's observing. `outcome` is filled in later by the caller
    once known (e.g. "review_approved", "review_refined") when available;
    empty string means not yet known.
    """
    try:
        _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "ts":                time.time(),
            "stage":              stage_role,
            "intent_alignment":   verdict.intent_alignment,
            "criteria_on_track":  verdict.criteria_on_track,
            "drift_detected":     verdict.drift_detected,
            "recommend":          verdict.recommend,
            "outcome":            outcome,
        }
        with open(_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
    except Exception as exc:
        logger.warning(f"[supervisor] verdict logging skipped: {str(exc)[:60]}")
