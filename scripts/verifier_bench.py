#!/usr/bin/env python3
"""Re-check that the confidence cascade's verifier can still tell good code
from bad, and how long it takes to say so.

A verifier that scores broken code above DEFAULT_THRESHOLD makes the whole
cascade inert -- it escalates nothing and rubber-stamps everything. That is
exactly what qwen36_27b_verifier was doing when this was written (2026-08-08),
silently, for every cascade call.

Model line-ups change under us (four registry models went 404 the same day),
so this is a script to re-run rather than a fact to trust. Live API calls, so
it is deliberately NOT part of the pytest suite.

    python scripts/verifier_bench.py                    # check the current default
    python scripts/verifier_bench.py MODEL_A MODEL_B    # compare candidates
"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.confidence_cascade import DEFAULT_THRESHOLD, DEFAULT_VERIFIER, _score  # noqa: E402

INSTRUCTION = (
    "Write diff_dicts(old, new) returning {added, removed, changed} with dotted "
    "paths, recursing into nested dicts."
)
RUBRIC = [
    "Handles nested dicts recursively",
    "Dotted path keys",
    "changed maps path to (old, new) tuple",
    "No crash on empty dicts",
]

GOOD = '''
def diff_dicts(old, new, prefix=""):
    added, removed, changed = [], [], {}
    for k in new:
        p = f"{prefix}{k}"
        if k not in old:
            added.append(p)
        elif isinstance(old[k], dict) and isinstance(new[k], dict):
            s = diff_dicts(old[k], new[k], p + ".")
            added += s["added"]; removed += s["removed"]; changed.update(s["changed"])
        elif old[k] != new[k]:
            changed[p] = (old[k], new[k])
    for k in old:
        if k not in new:
            removed.append(f"{prefix}{k}")
    return {"added": added, "removed": removed, "changed": changed}
'''

# Flat, no recursion, no dotted paths, ignores removals, wrong `changed` shape.
BAD = '''
def diff_dicts(old, new):
    changed = {}
    for k in new:
        if old.get(k) != new[k]:
            changed[k] = new[k]
    return changed
'''

SAMPLES = 3


async def bench(model: str) -> bool:
    rows = {}
    for label, candidate in (("GOOD", GOOD), ("BAD", BAD)):
        scores, secs = [], []
        for _ in range(SAMPLES):
            t0 = time.perf_counter()
            try:
                conf, _ = await _score(model, INSTRUCTION, RUBRIC, candidate)
            except Exception as exc:
                print(f"  {model}: {type(exc).__name__}: {str(exc)[:70]}")
                return False
            scores.append(conf)
            secs.append(time.perf_counter() - t0)
        rows[label] = (scores, secs)

    good, bad = rows["GOOD"][0], rows["BAD"][0]
    slowest = max(rows["GOOD"][1] + rows["BAD"][1])
    # The gate only works if correct code clears the bar and broken code doesn't.
    passes = min(good) >= DEFAULT_THRESHOLD and max(bad) < DEFAULT_THRESHOLD
    print(f"  {model:<26} GOOD {[round(s, 2) for s in good]}  "
          f"BAD {[round(s, 2) for s in bad]}  slowest={slowest:.1f}s  "
          f"{'OK' if passes else 'DISCRIMINATION FAILURE'}")
    return passes


async def main() -> int:
    models = sys.argv[1:] or [DEFAULT_VERIFIER]
    print(f"threshold={DEFAULT_THRESHOLD}  samples={SAMPLES}  default={DEFAULT_VERIFIER}\n")
    results = [await bench(m) for m in models]
    print()
    if all(results):
        print("verifier separates correct from broken code.")
        return 0
    print("!!! a verifier scored broken code at or above the threshold — "
          "the cascade gate is inert with it. Pick a different DEFAULT_VERIFIER.")
    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
