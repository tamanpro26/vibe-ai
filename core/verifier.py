"""
core/verifier.py — execution-grounded verification

This is what turns "the models *think* the answer is X" into "code *proved* the
answer is X." It is the single biggest lever for verifiable reasoning, and it is
deliberately MANAGER-AGNOSTIC: the ground truth produced by running code is the
same whether Claude or the Free Manager Council is in charge. The manager only
reads the verdict — the executor produces it — so both modes converge to the
same verifiable-reasoning quality.

Three methods, chosen by problem type:

  1. EXECUTION   — for computational / countable / algorithmic problems.
     A code model writes Python that solves the problem from scratch (ignoring
     any candidate answer), runs in the sandbox, and prints ground truth.
     The candidate is then checked against the computed value.

  2. CONSTRAINTS — for puzzles with explicit conditions ("digits sum to 12,
     hundreds = 2×units…"). The model encodes the constraints as Python
     predicates and brute-forces / checks them. Correctly reports
     "no solution" for impossible problems.

  3. CONSENSUS   — fallback for open-ended problems that can't be executed:
     independent re-derivation by several models + agreement score.

Returns a Verdict carrying the verified ground truth, a confidence, and the
executed evidence, which the reasoning core injects back so the final answer is
grounded in execution rather than assertion.
"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

from loguru import logger

from models.registry import generate_resilient
from tools.code_executor import executor, _strip_fences


@dataclass
class Verdict:
    verified:     bool
    method:       str       # "execution" | "constraints" | "consensus" | "unverifiable"
    ground_truth: str = ""
    confidence:   float = 0.0
    evidence:     str = ""

    def as_hint(self) -> str:
        """Render the verdict as a hint for the finalizer / manager review."""
        if self.method in ("execution", "constraints"):
            if self.verified:
                return (
                    f"\n\n[VERIFIED by {self.method} — confidence {self.confidence:.0%}] "
                    f"Code executed and confirmed the answer: {self.ground_truth}"
                )
            if self.ground_truth:
                return (
                    f"\n\n[VERIFICATION OVERRIDE — confidence {self.confidence:.0%}] "
                    f"Code executed and computed: {self.ground_truth}. "
                    f"The reasoning units' consensus was WRONG — use the computed value."
                )
        if self.method == "debate" and self.ground_truth:
            return (
                f"\n\n[CROSS-EXAMINED via debate — {self.confidence:.0%} of units "
                f"converged after challenging each other] {self.ground_truth}"
            )
        if self.method == "consensus" and self.ground_truth:
            return (
                f"\n\n[CROSS-CHECK — {self.confidence:.0%} of independent units agree] "
                f"{self.ground_truth}"
            )
        return ""


# Problems worth trying to execute: numbers + computational/puzzle vocabulary.
_COMPUTE_WORDS = [
    "how many", "calculate", "compute", "sum", "product", "divisible",
    "prime", "factor", "probability", "permutation", "combination",
    "average", "median", "percentage", "remainder", "modulo", "count",
    "maximum", "minimum", "solve", "equation", "digit", "integer",
]
_CONSTRAINT_WORDS = ["constraint", "such that", "satisfies", "digits", "puzzle", "exactly"]


_SOLVER_SYS = """You are a verification engine. Write Python that solves the
problem FROM SCRATCH — do NOT trust any candidate answer given to you; derive
the answer independently (brute force is fine; correctness matters, not elegance).
Use sympy/itertools/math as needed.

CRITICAL robustness rules:
- Your script MUST NOT raise an exception. Wrap the logic so that even if no
  solution is found, it still prints a COMPUTED line.
- Always print, on its own line: COMPUTED: <answer>   (use COMPUTED: NONE if no
  solution exists).
- If a candidate answer was provided, also print: MATCH: <true|false>
Output ONLY the Python code — no markdown fences, no prose."""


class VerificationEngine:

    async def verify(self, problem: str, candidate: str = "", reasoning: str = "") -> Verdict:
        kind = self._classify(problem)
        if kind == "execution":
            v = await self._verify_by_execution(problem, candidate)
            if v is not None:
                return v
        # Not executable, or execution errored → cross-model consensus
        return await self._verify_by_consensus(problem, candidate)

    def is_checkable(self, problem: str) -> bool:
        """True when the problem can be ground-truthed by running code."""
        return self._classify(problem) == "execution"

    # ── classification ───────────────────────────────────────────────────────

    def _classify(self, problem: str) -> str:
        low = problem.lower()
        has_num = bool(re.search(r"\d", problem))
        # arithmetic expressions, powers, roots — clear execution candidates
        if re.search(r"\d\s*[\+\-\*/\^%]\s*\d", problem) or any(
            p in low for p in ("to the power", "raised to", "squared", "cubed", "square root", "factorial")
        ):
            return "execution"
        if any(w in low for w in _COMPUTE_WORDS):
            return "execution"
        if has_num and (any(w in low for w in _CONSTRAINT_WORDS) or low.startswith(("what is", "what's", "what are"))):
            return "execution"
        return "open"

    # ── execution-grounded ───────────────────────────────────────────────────

    async def _verify_by_execution(self, problem: str, candidate: str) -> Verdict | None:
        logger.info("[verifier] verifying by execution")
        cand_block = f"\n\nCANDIDATE ANSWER (verify, don't trust): {candidate}" if candidate else ""
        try:
            code = await generate_resilient(
                "llama33_70b_coder",   # Llama 3.3 70B on Groq — fast (~1s) and writes solid solver code
                prompt=f"PROBLEM:\n{problem}{cand_block}",
                system=_SOLVER_SYS,
                max_tokens=1500,
                temperature=0.0,
            )
        except Exception as exc:
            logger.warning(f"[verifier] solver-code generation failed: {str(exc)[:60]}")
            return None

        result = await executor.run(_strip_fences(code))
        if not result.success:
            logger.warning(f"[verifier] verification code errored — falling back to consensus")
            return None

        computed = self._grab(result.stdout, "COMPUTED")
        match    = self._grab(result.stdout, "MATCH")
        if not computed:
            return None

        if computed.upper() == "NONE":
            logger.info("[verifier] execution: problem has NO solution")
            return Verdict(
                verified=False, method="execution",
                ground_truth="no solution exists", confidence=0.9,
                evidence=result.stdout[:500],
            )

        verified = (match or "").lower() == "true" or _norm(computed) == _norm(candidate)
        logger.info(f"[verifier] execution: computed={computed!r} verified={verified}")
        return Verdict(
            verified=verified, method="execution",
            ground_truth=computed, confidence=0.95 if verified else 0.9,
            evidence=result.stdout[:500],
        )

    # ── consensus (open-ended fallback) ──────────────────────────────────────

    _CHECKERS = ["qwen36_27b_verifier", "glm_47_cerebras", "llama33_70b_memory"]

    async def _verify_by_consensus(self, problem: str, candidate: str) -> Verdict:
        import asyncio
        logger.info("[verifier] verifying by cross-model consensus")
        sys = (
            "Independently solve the problem. Ignore any provided answer. "
            "Reason briefly, then end with a single line: ANSWER: <answer>"
        )
        results = await asyncio.gather(
            *(
                generate_resilient(m, prompt=problem, system=sys, max_tokens=1200, temperature=0.4)
                for m in self._CHECKERS
            ),
            return_exceptions=True,
        )
        answers = []
        for r in results:
            if not isinstance(r, Exception):
                found = re.findall(r"ANSWER:\s*(.+)", r)
                if found:
                    answers.append(_norm(found[-1]))
        if not answers:
            return Verdict(verified=False, method="unverifiable", confidence=0.0)
        winner, count = Counter(answers).most_common(1)[0]
        agree = count / len(answers)
        verified = bool(candidate) and _norm(candidate) == winner and agree >= 0.5
        return Verdict(
            verified=verified, method="consensus",
            ground_truth=winner, confidence=agree,
            evidence=f"{count}/{len(answers)} independent units agree",
        )

    # ── helpers ──────────────────────────────────────────────────────────────

    @staticmethod
    def _grab(stdout: str, key: str) -> str:
        m = re.search(rf"{key}:\s*(.+)", stdout)
        return m.group(1).strip() if m else ""


def _norm(s: str) -> str:
    s = s.strip().strip("*_`.").strip().lower()
    return re.sub(r"\s+", " ", s)[:120]


# Singleton
verifier = VerificationEngine()
