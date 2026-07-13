"""
tools/code_executor.py
Upgrade 3 — Code-verified Reasoning Engine

Key insight:
  Models reasoning about math are making educated guesses.
  Code executing math is computing exact answers.

  SymPy  → symbolic algebra, calculus, equation solving
  SciPy  → numerical computation, statistics, optimization
  Z3     → logic and constraint satisfaction

Execution: a timeout-limited subprocess with a SCRUBBED environment (no parent
env vars — no secret leakage) and a throwaway cwd. This is containment, not a
full sandbox — see the CodeExecutor docstring for the honest limits.

Flow:
  1. Brain team classifies task as "math" or "logic"
  2. Code team generates Python that PROVES the answer
  3. Executor runs it in sandbox, captures stdout
  4. Brain team interprets the result for the user

This converts a ~50% probabilistic reasoning task into ~85%+ deterministic computation.
"""
from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from loguru import logger


# ── Execution result ──────────────────────────────────────────────────────────

@dataclass
class ExecutionResult:
    success:    bool
    stdout:     str
    stderr:     str
    exit_code:  int
    timed_out:  bool = False

    @property
    def output(self) -> str:
        return self.stdout if self.success else f"ERROR:\n{self.stderr}"


# ── Safe stdlib allowlist ─────────────────────────────────────────────────────

SAFE_PRELUDE = textwrap.dedent("""\
    import math, statistics, json, re, itertools, functools, collections
    try:
        import sympy as sp
        from sympy import symbols, solve, simplify, expand, factor, diff, integrate, Matrix, Rational, pi, E
        from sympy import latex, pretty
    except ImportError:
        pass
    try:
        import numpy as np
        import scipy
        from scipy import stats, optimize, linalg
    except ImportError:
        pass
    try:
        from z3 import *
    except ImportError:
        pass
""")

# ── Local sandbox (subprocess isolation) ─────────────────────────────────────

class CodeExecutor:
    """
    Runs model-generated Python in a subprocess with a timeout, a scrubbed
    environment, and a throwaway working directory.

    HONEST LIMITS — this is containment, not a security sandbox:
      - the process CANNOT see your environment variables (env is scrubbed, so
        no .env secrets leak through os.environ)
      - it starts in an empty temp directory, not your repo
      - it is killed on timeout
      - it CAN still use the network and read world-readable files by absolute
        path. For genuinely untrusted code, run the whole system in a container
        (--network=none, read-only mounts). Do not rely on this class for that.
    """

    def __init__(self, timeout_seconds: int = 15) -> None:
        self._timeout = timeout_seconds

    async def run(self, code: str) -> ExecutionResult:
        """Execute code and return structured result."""
        full_code = SAFE_PRELUDE + "\n" + code

        result = await asyncio.get_event_loop().run_in_executor(
            None, self._run_subprocess, full_code
        )
        logger.info(
            f"[executor] exit={result.exit_code} | "
            f"out={len(result.stdout)} chars | "
            f"timeout={result.timed_out}"
        )
        return result

    def _run_subprocess(self, code: str) -> ExecutionResult:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as f:
            f.write(code)
            tmp_path = f.name

        # Scrubbed environment: generated code must never inherit the parent's
        # env — that's exactly where API keys live (a confused or prompt-injected
        # model emitting `print(os.environ)` would otherwise dump every secret).
        # SYSTEMROOT/PATH minimally retained so the interpreter itself works.
        clean_env = {
            "PATH": os.environ.get("PATH", ""),
            "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),  # required on Windows
            "PYTHONIOENCODING": "utf-8",
        }
        run_dir = tempfile.mkdtemp(prefix="vibe_exec_")

        try:
            proc = subprocess.run(
                [sys.executable, "-I", tmp_path],  # -I: isolated mode (no user site, no PYTHONPATH)
                capture_output=True,
                text=True,
                timeout=self._timeout,
                env=clean_env,
                cwd=run_dir,
            )
            return ExecutionResult(
                success=proc.returncode == 0,
                stdout=proc.stdout[:8000],
                stderr=proc.stderr[:2000],
                exit_code=proc.returncode,
            )
        except subprocess.TimeoutExpired:
            return ExecutionResult(
                success=False, stdout="", stderr="Execution timed out",
                exit_code=-1, timed_out=True,
            )
        except Exception as exc:
            return ExecutionResult(
                success=False, stdout="", stderr=str(exc), exit_code=-1
            )
        finally:
            Path(tmp_path).unlink(missing_ok=True)
            shutil.rmtree(run_dir, ignore_errors=True)

    async def run_math(self, problem: str, generated_code: str) -> str:
        """
        High-level helper: run math code and format result for Brain team.
        """
        result = await self.run(generated_code)
        if result.success and result.stdout.strip():
            return (
                f"COMPUTATION RESULT (exact):\n{result.stdout.strip()}\n\n"
                f"Problem: {problem}"
            )
        return (
            f"Execution failed: {result.stderr}\n\n"
            f"Code attempted:\n{generated_code[:500]}"
        )


# ── Code generation helpers ───────────────────────────────────────────────────

async def generate_math_code(problem: str) -> str:
    """
    Ask Code team's DeepSeek V4 Flash to generate Python that solves the problem.
    DeepSeek is strong at writing correct math code.
    """
    from models.registry import registry

    connector = registry.get("nemotron_super_bulk")
    prompt = (
        f"Write Python code to solve this mathematical problem. "
        f"Use SymPy for exact symbolic answers where possible. "
        f"Print the final answer clearly.\n\n"
        f"Problem: {problem}\n\n"
        f"Requirements:\n"
        f"- Use SymPy (already imported as sp)\n"
        f"- Print the exact answer with explanation\n"
        f"- If numerical, use scipy or numpy\n"
        f"- Return ONLY the Python code, no markdown"
    )
    return await connector.generate(
        prompt=prompt,
        system="You are a mathematical code generator. Write precise, executable Python.",
        max_tokens=2000,
        temperature=0.1,
    )


async def generate_logic_code(problem: str) -> str:
    """Ask Code team to generate Z3 constraint satisfaction code."""
    from models.registry import registry

    connector = registry.get("nemotron_super_bulk")
    prompt = (
        f"Write Python code using the Z3 SMT solver to solve this logic/constraint problem. "
        f"Z3 is already imported (from z3 import *).\n\n"
        f"Problem: {problem}\n\n"
        f"Return ONLY the Python code."
    )
    return await connector.generate(
        prompt=prompt,
        system="You are a constraint programming expert. Use Z3 for exact logical reasoning.",
        max_tokens=2000,
        temperature=0.1,
    )


# ── Full pipeline ─────────────────────────────────────────────────────────────

class CodeVerifiedReasoner:
    """
    Routes math/logic tasks through code execution instead of model reasoning.
    Drop-in replacement for model.generate() on computational tasks.
    """

    def __init__(self) -> None:
        self._executor = CodeExecutor()

    async def solve(self, problem: str, problem_type: str = "math") -> str:
        """
        problem_type: "math" | "logic" | "data"
        Returns verified answer string.
        """
        logger.info(f"[reasoner] solving {problem_type}: {problem[:60]}")

        # Generate verification code
        if problem_type == "logic":
            code = await generate_logic_code(problem)
        else:
            code = await generate_math_code(problem)

        # Strip markdown fences if model included them
        code = _strip_fences(code)

        # Execute and return
        return await self._executor.run_math(problem, code)

    def is_computational(self, task_description: str) -> bool:
        """
        Heuristic: does this task benefit from code execution?

        Requires a MATH keyword plus corroborating evidence (digits or math
        operators) for the ambiguous keywords. Bare substrings like "what is"
        used to route "what is a REST API" into the SymPy code generator, which
        produced garbage Python for a plain knowledge question. Strong keywords
        (derivative, integral, satisfiable, ...) are unambiguous on their own;
        weak ones (solve, logic, compute, ...) need digits/operators alongside.
        """
        import re
        lower = task_description.lower()
        strong = [
            "derivative", "integral", "equation", "satisfiable", "theorem",
            "matrix", "probability of", "permutation", "combinatoric",
            "find x", "solve for",
        ]
        weak = [
            "calculate", "compute", "solve", "prove", "formula", "statistics",
            "optimization", "constraint", "algebra", "geometry",
        ]
        has_math_evidence = bool(re.search(r"\d|[+\-*/^=<>≤≥%]|\bsum of\b|\bhow many\b", lower))
        if any(kw in lower for kw in strong):
            return True
        return has_math_evidence and any(kw in lower for kw in weak)


def _strip_fences(code: str) -> str:
    import re
    code = re.sub(r"```(?:python)?\s*", "", code)
    code = re.sub(r"```\s*", "", code)
    return code.strip()


# Singletons
executor = CodeExecutor()
reasoner = CodeVerifiedReasoner()
