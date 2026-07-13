"""
teams/prompt_enhancer.py
Prompt Enhancer — pre-processes raw user prompts before the 5-stage Prompt Refiner.

Why this exists:
  Every downstream model's output quality is bounded by input quality.
  A vague "make me a REST API" produces a mediocre TaskJSON; an enriched,
  explicit prompt gives the Prompt Refiner 5x more signal — better team
  selection, clearer per-team instructions, stronger success criteria.

Architecture (runs BEFORE PromptRefinerPipeline):
  3 angle models in PARALLEL (~2-4s, Groq + OpenRouter):
    1. Intent Amplifier  — expands goals, adds explicit success criteria, edge cases
    2. Context Injector  — injects domain knowledge, best practices, constraints
    3. Clarity Rewriter  — restructures for AI clarity: specific, ordered, unambiguous

  Synthesizer (runs after, ~1-2s):
    Merges the three angles into one coherent, powerful prompt.

Total overhead: ~4-6s, skipped for prompts under 60 chars (fast path handles those).
"""
from __future__ import annotations

import asyncio

from loguru import logger

from models.registry import generate_resilient


# ── Per-angle system prompts ──────────────────────────────────────────────────

_INTENT_SYSTEM = """You are an Intent Amplifier in a multi-AI pipeline.
Given a user's prompt, expand it to make the intent explicit and complete.
Add:
- Specific success criteria (what "done" looks like)
- Edge cases the user likely forgot to mention
- Implicit requirements they assumed were obvious
- Performance and quality expectations

Keep the same goal — do NOT change what they want, just make it more complete.
Output the amplified prompt as plain text only. No preamble, no explanation."""

_CONTEXT_SYSTEM = """You are a Context Injector in a multi-AI pipeline.
Given a user's prompt, enrich it with relevant technical context they didn't state.
Add:
- Relevant best practices and patterns for this type of task
- Common pitfalls to avoid
- Technical constraints worth specifying
- Standard conventions (naming, structure, testing, error handling)

Keep the same goal. Output the context-enriched prompt as plain text only."""

_CLARITY_SYSTEM = """You are a Prompt Clarity Optimizer for AI systems.
Given a user's prompt, rewrite it to maximise downstream AI model performance:
- Use numbered steps for multi-part requests
- Replace vague words (good, proper, clean, simple) with specific measurable requirements
- Specify output format when relevant (e.g. "return as Python class", "use TypeScript")
- Separate requirements from context clearly
- Name the specific language, framework, or pattern expected

Output the clarity-optimised prompt as plain text only. No preamble."""

_SYNTHESIZER_SYSTEM = """You are a Prompt Synthesizer in a multi-AI pipeline.
You receive an original user prompt and three enhanced versions from specialist models.
Merge the best elements from all three into ONE optimal prompt.

Rules:
- Preserve the user's original intent exactly — do not change the goal
- Include the most useful additions from each version
- Remove redundancy and repetition across the three versions
- Result should be 1.5-3x longer than the original, focused and clean
- Write in direct, imperative tone (as if the user wrote it)

Output: the final merged prompt as plain text only. No preamble, no labels."""


# ── Model assignments ─────────────────────────────────────────────────────────

_ANGLE_MODELS = [
    ("qwen36_27b_verifier",   _INTENT_SYSTEM,   "intent"),
    ("gemini_flash",       _CONTEXT_SYSTEM,  "context"),
    ("llama33_70b_memory", _CLARITY_SYSTEM,  "clarity"),
]
_SYNTH_MODEL    = "gpt_oss_120b_planner"
_MIN_PROMPT_LEN = 60   # prompts shorter than this skip enhancement


class PromptEnhancerPipeline:
    """
    Pre-enhances user prompts before the 5-stage Prompt Refiner pipeline.
    Returns an enhanced prompt string, or the original on any failure.
    """

    async def enhance(self, prompt: str) -> str:
        if len(prompt.strip()) < _MIN_PROMPT_LEN:
            logger.debug("[prompt_enhancer] short prompt — skipping")
            return prompt

        logger.info(f"[prompt_enhancer] enhancing | original={len(prompt)} chars")

        # ── 3 angles in parallel ──────────────────────────────────────────────
        raw_results = await asyncio.gather(
            *(
                generate_resilient(
                    model_id,
                    prompt=f"User prompt to enhance:\n\n{prompt}",
                    system=system,
                    max_tokens=800,
                    temperature=0.5,
                )
                for model_id, system, _ in _ANGLE_MODELS
            ),
            return_exceptions=True,
        )

        angles: dict[str, str] = {}
        for (model_id, _, label), result in zip(_ANGLE_MODELS, raw_results):
            if isinstance(result, Exception):
                logger.warning(f"[prompt_enhancer] {label} angle ({model_id}) failed: {result}")
            else:
                angles[label] = result.strip()

        if not angles:
            logger.warning("[prompt_enhancer] all angles failed — using original prompt")
            return prompt

        logger.info(f"[prompt_enhancer] angles done: {list(angles.keys())} — synthesising")

        # ── Synthesize ────────────────────────────────────────────────────────
        angle_block = "\n\n".join(
            f"[{label.upper()} ENHANCED VERSION]:\n{text}"
            for label, text in angles.items()
        )
        synth_prompt = (
            f"ORIGINAL USER PROMPT:\n{prompt}\n\n"
            f"THREE ENHANCED VERSIONS:\n{angle_block}\n\n"
            f"Synthesize into one optimal prompt."
        )

        try:
            enhanced = await generate_resilient(
                _SYNTH_MODEL,
                prompt=synth_prompt,
                system=_SYNTHESIZER_SYSTEM,
                max_tokens=1000,
                temperature=0.3,
            )
            enhanced = enhanced.strip()
            if not enhanced:
                raise ValueError("empty synthesis output")
            logger.info(
                f"[prompt_enhancer] done | "
                f"{len(prompt)} → {len(enhanced)} chars | "
                f"boost={len(enhanced)/max(len(prompt),1):.1f}x"
            )
            return enhanced
        except Exception as exc:
            logger.warning(f"[prompt_enhancer] synthesis failed ({exc}) — using best angle")
            best = max(angles.values(), key=len, default="")
            return best if len(best) > len(prompt) else prompt


# Singleton
prompt_enhancer = PromptEnhancerPipeline()
