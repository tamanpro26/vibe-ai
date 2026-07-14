"""
teams/code.py
Code Team — vibe coding, debugging, full-stack generation.

Models:
  1. gpt_oss_120b_coder         — Primary vibe coder (screenshot → code, #1 agentic)
  2. gpt_oss_120b_debug  — Debugging specialist (SWE-bench optimised)
  3. llama33_70b_coder       — Fast iteration / review
  4. glm_47_cerebras      — Full-stack (back-end + front-end)
  5. nemotron_super_bulk — Bulk processing (tests, boilerplate, CI)

Optimisations active:
  • Dynamic token budget — complexity-scaled (500-8000), not fixed 6000
  • Session-level code cache — 5-min TTL, MD5 key on instruction
  • Skip review pass when code passes clean syntax and has no incomplete markers
  • Best-of-N skips judge when all candidates share the same function structure
  • Parallel debug — devstral + llama33_70b_coder run simultaneously, best or merge wins
"""
from __future__ import annotations

import asyncio
import hashlib
import re
import time
from typing import Any

from loguru import logger

from core.imcp import TaskJSON, TaskType, Complexity
from core.peer_consult import CONFIDENCE_PROMPT_SUFFIX
from teams.base_team import BaseTeam


# ── Token budget ───────────────────────────────────────────────────────────────

def _code_budget(complexity: Complexity, instruction: str) -> int:
    """Scale max_tokens with task size — trivial fixes don't need 6000 tokens."""
    words = len(instruction.split())
    if complexity == Complexity.SIMPLE:
        return min(2000, max(500, words * 8))
    if complexity == Complexity.MODERATE:
        return min(4000, max(1500, words * 10))
    return min(8000, max(3000, words * 12))


# ── Session-level code cache ───────────────────────────────────────────────────

_CODE_CACHE: dict[str, tuple[str, float]] = {}
_CODE_CACHE_TTL = 300   # 5 minutes


def _cache_key(instruction: str) -> str:
    normalised = re.sub(r"\s+", " ", instruction.strip().lower())
    return hashlib.md5(normalised.encode()).hexdigest()[:16]


def _cache_get(instruction: str) -> str | None:
    key   = _cache_key(instruction)
    entry = _CODE_CACHE.get(key)
    if entry and (time.time() - entry[1]) < _CODE_CACHE_TTL:
        return entry[0]
    if entry:
        del _CODE_CACHE[key]
    return None


def _cache_put(instruction: str, result: str) -> None:
    _CODE_CACHE[_cache_key(instruction)] = (result, time.time())


# ── Code analysis helpers ──────────────────────────────────────────────────────

def _extract_python(text: str) -> str:
    blocks = re.findall(r"```python\s*([\s\S]*?)```", text)
    return "\n\n".join(b.strip() for b in blocks).strip()


def _syntax_error(code: str) -> str | None:
    try:
        compile(code, "<generated>", "exec")
        return None
    except SyntaxError as exc:
        return f"SyntaxError: {exc.msg} (line {exc.lineno})"


def _fn_signatures(code: str) -> set[str]:
    return set(re.findall(r"(?:def|class)\s+(\w+)", code))


def _needs_review(code: str) -> bool:
    """True only when the code looks incomplete or broken — skip the review call otherwise."""
    if _syntax_error(code) is not None:
        return True
    lines = code.strip().split("\n")
    tail  = " ".join(lines[-5:]).lower()
    return (
        "todo" in code
        or "pass" in tail
        or "raise notimplementederror" in tail
        or "..." in tail
    )


# ── System prompts ─────────────────────────────────────────────────────────────

_CODE_SYSTEM = """You are part of the Code team in VibeAI.
Write clean, production-ready code. Always include:
- Proper error handling
- Meaningful variable names
- Comments for complex logic
For debugging: identify the exact bug, explain why it occurs, provide the fix."""

_DEBUG_SYSTEM = """You are a debugging specialist in VibeAI.
When given an error or bug:
1. Identify the exact cause
2. Show the minimal failing case
3. Provide the exact fix
4. Explain why the fix works
Always provide the complete corrected code, not just the changed lines."""


class CodeTeam(BaseTeam):
    team_name = "code"

    async def _execute(
        self,
        task_json: TaskJSON,
        instruction: str,
        iteration: int,
        extra: dict[str, Any],
    ) -> str:
        instruction = instruction or self._instruction(task_json)
        task_type   = task_json.classification.primary_type
        image_b64   = extra.get("image_b64")
        error_trace = extra.get("error_trace")

        # Session cache — same code request twice in one conversation is a no-op
        cached = _cache_get(instruction)
        if cached:
            logger.info("[code] session cache hit — returning cached result")
            return cached

        if task_type == TaskType.DEBUGGING or error_trace:
            result = await self._debug(instruction, error_trace, image_b64, task_json)
        else:
            complexity = task_json.classification.complexity
            result     = await self._generate(instruction, image_b64, task_type, complexity)

        if result:
            _cache_put(instruction, result)
        return result

    # ── Code generation ────────────────────────────────────────────────────────

    async def _generate(
        self,
        instruction: str,
        image_b64: str | None,
        task_type: TaskType,
        complexity: Complexity,
    ) -> str:
        budget = _code_budget(complexity, instruction)

        if complexity == Complexity.COMPLEX and not image_b64:
            return await self._best_of_n(instruction, budget)

        images  = [image_b64] if image_b64 else []
        primary = (
            self._get_model("glm_47_cerebras")
            if task_type == TaskType.VIBE_CODING and not image_b64
            else self._get_model("gpt_oss_120b_coder")
        )

        code = await primary.generate(
            prompt=instruction,
            system=_CODE_SYSTEM + CONFIDENCE_PROMPT_SUFFIX.format(model_id=primary.model_id),
            images=images,
            max_tokens=budget,
            temperature=0.2,
        )

        # Review pass only when the generated code shows signs of being incomplete.
        # Clean code that compiles = skip the whole extra model call.
        if complexity != Complexity.SIMPLE:
            extracted = _extract_python(code)
            if extracted and _needs_review(extracted):
                logger.info("[code] review pass triggered — incomplete markers found")
                code = await self._get_model("llama33_70b_coder").generate(
                    prompt=(
                        f"Review this code for bugs, missing imports, and incomplete sections.\n\n"
                        f"CODE:\n{code}\n\n"
                        f"Return the corrected code only. If no issues found, return it unchanged."
                    ),
                    system=_CODE_SYSTEM,
                    max_tokens=budget,
                    temperature=0.1,
                )

        return await self._verify(code, instruction, budget)

    # ── Best-of-N ──────────────────────────────────────────────────────────────

    _BON_MODELS = ["gpt_oss_120b_coder", "glm_47_cerebras", "nemotron_super_bulk"]

    async def _best_of_n(self, instruction: str, budget: int) -> str:
        drafts = await asyncio.gather(
            *(
                self._get_model(m).generate(
                    prompt=instruction, system=_CODE_SYSTEM,
                    max_tokens=budget, temperature=0.3,
                )
                for m in self._BON_MODELS
            ),
            return_exceptions=True,
        )
        ok = [
            (m, d) for m, d in zip(self._BON_MODELS, drafts)
            if not isinstance(d, Exception) and d
        ]
        if not ok:
            raise RuntimeError("code team: all best-of-N candidates failed")
        logger.info(f"[code] best-of-{len(ok)} | evaluating candidates")

        if len(ok) == 1:
            return await self._verify(ok[0][1], instruction, budget)

        # Structural agreement check: if all candidates define the same
        # functions/classes, they all arrived at the same design — take the
        # primary and skip the expensive judge pass entirely.
        sigs = [_fn_signatures(d) for _, d in ok]
        if sigs[0] and all(s == sigs[0] for s in sigs[1:]):
            logger.info("[code] best-of-N: structural agreement — skipping judge pass")
            return await self._verify(ok[0][1], instruction, budget)

        block = "\n\n".join(
            f"=== CANDIDATE {i+1} ({m}) ===\n{d}" for i, (m, d) in enumerate(ok)
        )
        merged = await self._get_model("gpt_oss_120b_debug").generate(
            prompt=(
                f"INSTRUCTION:\n{instruction}\n\n{block}\n\n"
                f"Pick the best candidate, fix any defects (correctness first), "
                f"and return ONE final response with the complete code."
            ),
            system=_CODE_SYSTEM,
            max_tokens=min(budget + 2000, 10000),
            temperature=0.1,
        )
        return await self._verify(merged, instruction, budget)

    # ── Empirical verification ─────────────────────────────────────────────────

    async def _verify(self, output: str, instruction: str, budget: int = 4000) -> str:
        code = _extract_python(output)
        if not code:
            return output

        error = _syntax_error(code)
        if error is None and (">>>" in code or "assert " in code):
            from tools.code_executor import executor
            harness = code
            if ">>>" in code:
                harness += (
                    "\n\nif __name__ == '__main__':\n"
                    "    import doctest, sys\n"
                    "    r = doctest.testmod()\n"
                    "    sys.exit(1 if r.failed else 0)\n"
                )
            result = await executor.run(harness)
            if not result.success:
                error = (result.stderr or "self-tests failed").strip()[:1500]

        if error is None:
            return output

        logger.info(f"[code] verification failed ({error[:80]}) — repairing")
        return await self._get_model("gpt_oss_120b_debug").generate(
            prompt=(
                f"This code fails verification.\n\n"
                f"INSTRUCTION:\n{instruction}\n\n"
                f"CODE:\n{code}\n\n"
                f"VERIFICATION ERROR:\n{error}\n\n"
                f"Return the full corrected response with the fixed code block."
            ),
            system=_DEBUG_SYSTEM,
            max_tokens=budget,
            temperature=0.1,
        )

    # ── Parallel debug ─────────────────────────────────────────────────────────

    async def _debug(
        self,
        instruction: str,
        error_trace: str | None,
        image_b64: str | None,
        task_json: TaskJSON,
    ) -> str:
        """
        Devstral + llama33_70b_coder run simultaneously.
        If they agree on the same fix (identical function signatures changed),
        return devstral's output. If they differ, merge into one definitive fix.
        """
        images      = [image_b64] if image_b64 else []
        budget      = _code_budget(task_json.classification.complexity, instruction)
        full_prompt = instruction
        if error_trace:
            full_prompt += f"\n\nERROR / STACK TRACE:\n{error_trace}"

        dev_result, qwen_result = await asyncio.gather(
            self._get_model("gpt_oss_120b_debug").generate(
                prompt=full_prompt,
                system=_DEBUG_SYSTEM,
                images=images,
                max_tokens=budget,
                temperature=0.1,
            ),
            self._get_model("llama33_70b_coder").generate(
                prompt=full_prompt,
                system=_DEBUG_SYSTEM,
                max_tokens=min(budget, 2000),
                temperature=0.1,
            ),
            return_exceptions=True,
        )

        dev  = dev_result  if not isinstance(dev_result,  Exception) else None
        qwen = qwen_result if not isinstance(qwen_result, Exception) else None

        if dev and qwen:
            dev_sigs  = _fn_signatures(dev)
            qwen_sigs = _fn_signatures(qwen)
            if dev_sigs == qwen_sigs or not qwen_sigs:
                # Both debuggers agree on the same structure — devstral leads
                return await self._verify(dev, instruction, budget)
            # Different root cause diagnoses — ask devstral to reconcile
            logger.info("[code] parallel debug: differing diagnoses — merging")
            merged = await self._get_model("gpt_oss_120b_debug").generate(
                prompt=(
                    f"Two debug analyses produced different fixes. Merge into the best.\n\n"
                    f"ORIGINAL PROBLEM:\n{full_prompt}\n\n"
                    f"ANALYSIS 1 (devstral):\n{dev}\n\n"
                    f"ANALYSIS 2 (qwen3):\n{qwen}\n\n"
                    f"Return ONE final corrected response."
                ),
                system=_DEBUG_SYSTEM,
                max_tokens=budget,
                temperature=0.1,
            )
            return await self._verify(merged, instruction, budget)

        if dev:
            return await self._verify(dev, instruction, budget)
        if qwen:
            return await self._verify(qwen, instruction, budget)
        raise RuntimeError("code team: both debug models failed")
