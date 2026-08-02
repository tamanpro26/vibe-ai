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

from core.confidence_cascade import run_cascade
from core.imcp import TaskJSON, TaskType, Complexity
from core.peer_consult import with_confidence_invite
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
            result     = await self._generate(
                instruction, image_b64, task_type, complexity,
                task_json.success_criteria,
            )

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
        success_criteria: list[str] | None = None,
    ) -> str:
        budget = _code_budget(complexity, instruction)

        if complexity == Complexity.COMPLEX and not image_b64:
            return await self._best_of_n(instruction, budget)

        if image_b64:
            # Vision-capable models only -- the cascade's cheap tier
            # (llama33_70b_coder) has no vision capability, so a screenshot
            # request skips the cascade and goes straight to the model that
            # can actually see it. Unlike the cascade/best-of-N paths below,
            # this one has no independent quality check of its own, so it's
            # the one call site that actually invites the CONFIDENCE tag
            # (core/peer_consult.py) rather than leaving it dead.
            code = await self._get_model("gpt_oss_120b_coder").generate(
                prompt=instruction,
                system=with_confidence_invite(_CODE_SYSTEM, "gpt_oss_120b_coder"),
                images=[image_b64],
                max_tokens=budget, temperature=0.2,
            )
        else:
            # Confidence-gated cascade: try the cheap/fast tier first, let an
            # independent verifier score it against the task's own success
            # criteria, and only pay for the bigger model when that score is
            # too low. SIMPLE/MODERATE requests (the common case) had no
            # quality gate at all before this -- COMPLEX already gets
            # best-of-N above, which is its own (pricier) quality mechanism.
            primary_tier = "glm_47_cerebras" if task_type == TaskType.VIBE_CODING else "gpt_oss_120b_coder"
            cascade = await run_cascade(
                tiers=["llama33_70b_coder", primary_tier],
                instruction=instruction,
                system=_CODE_SYSTEM,
                rubric=success_criteria,
                max_tokens=budget,
                temperature=0.2,
            )
            code = cascade.output
            if cascade.escalations:
                logger.info(
                    f"[code] cascade escalated to {cascade.model_id} "
                    f"(confidence={cascade.verifier_score:.2f})"
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

        # Execution-based selection: run each candidate instead of asking a
        # judge model to guess which one works. A candidate that fails to
        # import is dropped before the judge ever sees it.
        checked = []
        for m, d in ok:
            code = _extract_python(d)
            error = await self._execution_error(code) if code else "no python code block found"
            checked.append((m, d, error))
        passing = [(m, d) for m, d, error in checked if error is None]

        if len(passing) == 1:
            logger.info("[code] best-of-N: one candidate passed execution — skipping judge pass")
            return passing[0][1]
        if passing:
            logger.info(f"[code] best-of-N: {len(passing)}/{len(checked)} candidates passed execution")
            ok = passing
        # else: none executed cleanly -- fall through to judge across all
        # candidates; _verify below still repairs whatever it picks.

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

    async def _execution_error(self, code: str) -> str | None:
        """GROUND TRUTH. compile() only raises on SyntaxError -- NameError,
        ImportError, undefined symbols and bad attribute access all sail past
        it, and those are precisely what weak models get wrong. Nothing else
        in this pipeline can catch them: every other check is a model scoring
        another model's text, so the strongest reasoner in the pool is the
        ceiling. Actually importing the module supplies information no model
        here possessed."""
        error = _syntax_error(code)
        if error is not None:
            return error

        from tools.code_executor import executor

        # Reassigning __name__ makes this an IMPORT test rather than a run of
        # the program: `if __name__ == "__main__":` will not fire. That
        # matters because a script expecting argv/stdin, or one with a
        # long-running main, would otherwise "fail" verification and burn a
        # repair pass fixing a bug that does not exist. Top-level asserts
        # still execute, so self-checking code is still checked.
        harness = '__name__ = "_vibeai_smoke"\n' + code
        if ">>>" in code:
            # Called explicitly: the main guard above is deliberately dead.
            harness += (
                "\n\nimport doctest as _dt, sys as _sys\n"
                "_r = _dt.testmod(_sys.modules['__main__'])\n"
                "_sys.exit(1 if _r.failed else 0)\n"
            )
        result = await executor.run(harness)
        if not result.success:
            return (result.stderr or "execution failed").strip()[:1500]
        return None

    async def _verify(self, output: str, instruction: str, budget: int = 4000) -> str:
        code = _extract_python(output)
        if not code:
            return output

        # This used to run ONLY when the model happened to emit doctests or an
        # assert, which is a minority of generated code -- so in the common
        # case nothing was ever executed.
        error = await self._execution_error(code)

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
