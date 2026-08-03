#!/usr/bin/env python3
"""
scripts/vibeloop_check.py
Generic "script"-type check for vibe-loop coding/debugging tasks: extract the
Python VibeAI wrote out of its (prose + code) answer, execute it, then run a
task-specific assertion snippet against the resulting namespace.

One reusable harness instead of one file per task -- the assertion snippet is
supplied as this script's own argv (each task's check.value embeds its own
assertions inline), so 16 coding/debugging tasks share this single file rather
than needing 16 near-identical ones.

This is real ground truth, not a text judge: the check is "does the corrected
code actually produce the right output", the same principle this project's
own tools/code_executor.py verification pass is built on.

Usage (as a run_eval.py "script" check):
    python scripts/vibeloop_check.py <answer_file> "<assertion code>"

Exit 0 = pass, 1 = fail (assertion or runtime exception), 2 = no code found.
"""
from __future__ import annotations

import re
import sys

_FENCE_RE = re.compile(r"```(?:python|py)?\s*\n(.*?)```", re.S)


def extract_code(text: str) -> str:
    """Pick the one fenced block that is the real answer.

    A model answering a debugging prompt routinely includes a SECOND fence
    alongside the fix -- a bare `>>>` usage transcript, or a short before/
    after snippet -- because the system prompt asks it to "show the minimal
    failing case". Concatenating every fence (the previous behaviour here,
    mirroring the same bug found live in teams/code.py::_extract_python)
    glues that illustration onto the real code: a bare `>>>` line isn't valid
    top-level Python, and a demo referencing a class/function defined in a
    LATER fence raises NameError -- both false failures on code that already
    works. Verified on real data: 5/21 train rows in one baseline "failed"
    this way; all 5 pass once isolated to their real fenced block.
    """
    blocks = [b.strip() for b in _FENCE_RE.findall(text)]
    if not blocks:
        # No fence at all: some models answer with bare code and no markdown.
        # Treating the whole answer as code -- rather than failing every
        # unfenced answer outright -- mirrors teams/code.py's own posture of
        # preferring a real execution attempt over a format nitpick.
        return text.strip()
    real = [b for b in blocks if not re.match(r"^\s*>>>", b)] or blocks
    return max(real, key=len)


def main() -> int:
    if len(sys.argv) < 3:
        print("usage: vibeloop_check.py <answer_file> <assertion_code>", file=sys.stderr)
        return 2

    answer_path, assertion_code = sys.argv[1], sys.argv[2]
    answer = open(answer_path, encoding="utf-8").read()
    code = extract_code(answer)
    if not code:
        print("no code found in answer", file=sys.stderr)
        return 2

    ns: dict = {}
    try:
        exec(compile(code, "<vibeai-answer>", "exec"), ns)
        exec(compile(assertion_code, "<vibeloop-assertion>", "exec"), ns)
    except Exception as exc:
        print(f"FAIL: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1

    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
