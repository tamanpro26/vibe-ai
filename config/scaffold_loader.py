"""
config/scaffold_loader.py
Optional override layer for the handful of highest-traffic system prompts on
the chat/manager path.

Exists so the vibe-loop optimization skill (~/.claude/skills/vibe-loop) has a
real config surface to mutate. Every one of these prompts was previously a bare
Python string constant, which the loop's scaffold-mutator is forbidden from
touching -- application code is off-limits by design, since a loop with write
access to its own measurement will eventually improve the measurement instead
of the system. This gives it a JSON file to edit instead, with no change to
behavior when that file doesn't exist.

Precedence: VIBE_SCAFFOLD_CONFIG env var (a JSON file path) > the calling
module's own default text. Absent the env var, every get_prompt() call returns
its default and the module behaves exactly as it did before this existed.
"""
from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=8)
def _load(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def get_prompt(key: str, default: str) -> str:
    """Scaffold override for `key`, or `default` if VIBE_SCAFFOLD_CONFIG is
    unset or doesn't define it.

    Cached per config path: a single process runs under exactly one scaffold
    (the loop's runner sets VIBE_SCAFFOLD_CONFIG once, before importing
    anything that calls this), so there's no case where the same process needs
    two different answers for the same path.
    """
    path = os.environ.get("VIBE_SCAFFOLD_CONFIG")
    if not path:
        return default
    return _load(path).get(key, default)
