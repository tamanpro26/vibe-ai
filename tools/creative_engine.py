"""
tools/creative_engine.py — Creative Synthesis Engine

Prose quality gap with Opus 4.8 isn't about knowledge — free models know enough
to write well. The gap is about taste and deliberateness. Opus 4.8 has a strong
internal sense of "what good looks like here." Free models default to generic,
safe writing without a clear voice or direction.

This engine replicates Opus's deliberate process externally:

  Step 1 — Brief (Gemini 2.5 Flash)
            Analyse the specific task: what form, who's the audience, what makes
            excellent writing HERE — not generic advice. Concrete dos/don'ts,
            voice, opening direction.

  Step 2 — Burst (3 models, temperature=0.9, parallel)
            Three different model families each write a full draft following the
            brief. High temperature forces creative risk-taking, not safe defaults.
            Real alternatives — not variations of the same safe idea.

  Step 3 — Select (Gemini 2.5 Flash evaluates all 3 against the brief)
            Picks the draft that best fulfils the specific brief. Also identifies
            the one specific improvement the winning draft needs.

  Step 4 — Polish (Gemini 2.5 Flash precision edit)
            One focused pass: sharpen the opening, cut filler, improve rhythm,
            strengthen the close. NOT a rewrite — a precision edit on the winner.

Result: deliberate, purposeful prose that matches Opus quality on creative tasks.
"""
from __future__ import annotations

import re
from loguru import logger

from models.registry import generate_resilient
import asyncio


# ── Model assignment ───────────────────────────────────────────────────────────
_ANALYST  = "gemini_flash"       # Gemini 2.5 Flash — strong at meta-analysis
_DRAFTERS = [               # Three different families for genuine variety
    "qwen36_27b_verifier",          # GPT-OSS 120B (Groq)
    "gemini_flash",              # Gemini 2.5 Flash (Google)
    "llama33_70b_memory",        # Llama 3.3 70B (Groq/Meta)
]
_SELECTOR = "gemini_flash"       # Gemini — reliable judge
_POLISHER = "gemini_flash"       # Gemini — precise editor


# ── Creative task detection ────────────────────────────────────────────────────
_CREATIVE_SIGNALS = [
    "write a", "write an", "draft a", "draft an", "compose a", "compose an",
    "short story", "story about", "poem", "poetry", "email to", "letter to",
    "blog post", "essay about", "article about", "creative writing", "narrative",
    "cover letter", "speech", "script", "dialogue", "fiction", "write me",
    "write something", "write about",
]

def is_creative_task(text: str) -> bool:
    low = text.lower()
    return any(s in low for s in _CREATIVE_SIGNALS)


# ── System prompts ─────────────────────────────────────────────────────────────

_ANALYST_SYS = """You are a creative writing director. Analyse the creative task
and produce a short, concrete brief for the writers.

Your brief must contain EXACTLY these four sections (keep each to 1–3 lines):
FORM: <what kind of writing this is and its structural requirements>
AUDIENCE: <who will read this and what they need to feel>
VOICE: <the specific tone, register, and personality to project>
OPENING: <a concrete direction for how the piece should begin>
DOS: <2–3 specific things that will make this excellent>
DONTS: <2–3 specific things that will ruin it>

Be specific to THIS task, not generic writing advice."""

_DRAFTER_SYS = """You are a skilled writer. Follow the brief precisely and write
the requested piece. Be bold and specific — avoid safe, generic choices.
Write the full piece, not an outline. Quality over safety."""

_SELECTOR_SYS = """You are a senior editor. You have a creative brief and three
draft pieces. Your job is:

1. Identify which draft best fulfils the brief (strongest opening, best voice,
   most purposeful structure, fewest filler words).
2. State ONE specific improvement the winning draft needs.

Respond with EXACTLY this format:
WINNER: <1, 2, or 3>
IMPROVEMENT: <one concrete, specific thing to fix in the winning draft>
REASON: <one sentence on why this draft won>"""

_POLISHER_SYS = """You are a precision editor. You have a piece of writing and
one specific improvement to make. Apply ONLY that improvement — do not rewrite
the whole piece or change what is already working.

Fix the specific issue, then return the complete polished piece."""


class CreativeEngine:
    """5-step pipeline that gives free models the deliberate creative process
    Opus 4.8 has internalised."""

    async def synthesize(self, task: str, context: str = "") -> str:
        """Run the full pipeline and return polished prose."""
        ctx_block = f"\n\nContext: {context}" if context else ""
        full_task = task + ctx_block

        # Step 1 — Brief
        logger.info("[creative] step 1/4 — analysing task")
        brief = await generate_resilient(
            _ANALYST,
            prompt=f"Creative task:\n{full_task}",
            system=_ANALYST_SYS,
            max_tokens=400,
            temperature=0.3,
        )
        logger.info(f"[creative] brief ready ({len(brief)} chars)")

        # Step 2 — Burst (parallel)
        logger.info("[creative] step 2/4 — 3 drafters writing in parallel (temp=0.9)")
        drafter_prompt = (
            f"BRIEF:\n{brief}\n\n"
            f"TASK:\n{full_task}\n\n"
            f"Write the complete piece now."
        )
        draft_results = await asyncio.gather(
            *(generate_resilient(
                m, prompt=drafter_prompt, system=_DRAFTER_SYS,
                max_tokens=1500, temperature=0.9,
              )
              for m in _DRAFTERS),
            return_exceptions=True,
        )
        drafts = [
            r for r in draft_results
            if isinstance(r, str) and r.strip()
        ]
        if not drafts:
            logger.warning("[creative] all drafters failed — returning empty")
            return ""
        logger.info(f"[creative] {len(drafts)} drafts received")

        # If only one draft survived, skip selection
        if len(drafts) == 1:
            winner_text = drafts[0]
            improvement = ""
        else:
            # Step 3 — Select
            logger.info("[creative] step 3/4 — selecting best draft")
            draft_block = "\n\n".join(
                f"--- Draft {i + 1} ---\n{d}" for i, d in enumerate(drafts)
            )
            sel_raw = await generate_resilient(
                _SELECTOR,
                prompt=(
                    f"BRIEF:\n{brief}\n\n"
                    f"TASK:\n{full_task}\n\n"
                    f"{draft_block}"
                ),
                system=_SELECTOR_SYS,
                max_tokens=200,
                temperature=0.1,
            )
            winner_idx  = self._parse_winner(sel_raw, len(drafts))
            improvement = self._parse_improvement(sel_raw)
            winner_text = drafts[winner_idx]
            logger.info(
                f"[creative] draft {winner_idx + 1} selected | "
                f"improvement: {improvement[:60]}"
            )

        if not improvement:
            return winner_text

        # Step 4 — Polish
        logger.info("[creative] step 4/4 — precision polish")
        polished = await generate_resilient(
            _POLISHER,
            prompt=(
                f"PIECE TO POLISH:\n{winner_text}\n\n"
                f"SPECIFIC IMPROVEMENT TO APPLY:\n{improvement}\n\n"
                f"Return the complete polished piece."
            ),
            system=_POLISHER_SYS,
            max_tokens=1800,
            temperature=0.4,
        )
        logger.info("[creative] pipeline complete")
        return polished or winner_text

    # ── Helpers ────────────────────────────────────────────────────────────────

    @staticmethod
    def _parse_winner(raw: str, n_drafts: int) -> int:
        m = re.search(r"WINNER:\s*(\d)", raw)
        if m:
            idx = int(m.group(1)) - 1
            return max(0, min(idx, n_drafts - 1))
        return 0

    @staticmethod
    def _parse_improvement(raw: str) -> str:
        m = re.search(r"IMPROVEMENT:\s*(.+)", raw)
        return m.group(1).strip() if m else ""


# Singleton
creative_engine = CreativeEngine()
