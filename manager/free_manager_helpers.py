"""
manager/free_manager_helpers.py
Small helpers for the Free Manager Council's 2-model review consensus.
"""
from __future__ import annotations

import json
import re


def parse_json(text: str) -> dict:
    """Parse a JSON object out of a model response, tolerating extra prose."""
    try:
        return json.loads(text.strip())
    except Exception:
        pass
    m = re.search(r"\{[\s\S]+\}", text)
    if m:
        return json.loads(m.group(0))
    raise ValueError("No JSON object found in response")


def merge_reviews(verdicts: list[dict]) -> dict:
    """
    Merge 1-2 independent review verdicts into a single consensus verdict.

    - quality_score: average across verdicts
    - criteria_passed / criteria_failed / issues: union, de-duplicated
    - action: REFINE if either says REFINE (unless both APPROVE);
              ESCALATE if either ESCALATEs; else the single verdict's action
    - refine_instruction: the longer (more specific) of the two
    """
    if len(verdicts) == 1:
        return verdicts[0]

    scores  = [float(v.get("quality_score", 0.5)) for v in verdicts]
    avg_score = sum(scores) / len(scores)

    def _union(key: str) -> list[str]:
        seen: list[str] = []
        for v in verdicts:
            for item in v.get(key, []):
                if item not in seen:
                    seen.append(item)
        return seen

    actions = [v.get("action", "REFINE") for v in verdicts]
    if "ESCALATE" in actions:
        action = "ESCALATE"
    elif "REFINE" in actions:
        action = "REFINE"
    else:
        action = "APPROVE"

    refine_instructions = [v.get("refine_instruction", "") for v in verdicts]
    refine_instruction   = max(refine_instructions, key=len)

    return {
        "quality_score":      round(avg_score, 3),
        "criteria_passed":    _union("criteria_passed"),
        "criteria_failed":    _union("criteria_failed"),
        "issues":             _union("issues"),
        "refine_instruction": refine_instruction,
        "action":             action,
    }
