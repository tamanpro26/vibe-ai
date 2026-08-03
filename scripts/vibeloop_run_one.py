#!/usr/bin/env python3
"""
scripts/vibeloop_run_one.py
Headless one-shot entry point: run a single prompt through VibeAI's real
chat/manager pipeline and write the answer to a file.

Built for the vibe-loop optimization skill's run_eval.py, which spawns one
VibeAI process per task per repeat via `vibe.command` in config.json --
cli.py is an interactive REPL with no headless single-prompt mode, so this is
the "how VibeAI runs one task" contract the loop's setup step asks for.

Usage:
    python scripts/vibeloop_run_one.py \
        --config configs/scaffold.json \
        --prompt-file /tmp/prompt.txt \
        --out /tmp/answer.txt

--config points at the scaffold JSON (config/scaffold_loader.py) -- the ONE
file the optimization loop is allowed to rewrite. It must be exported as
VIBE_SCAFFOLD_CONFIG and set BEFORE any VibeAI module is imported: every
scaffold-overridden prompt constant (_FAST_SYSTEM, _MANAGER_SYSTEM,
_CODE_SYSTEM, _DEBUG_SYSTEM, _BRAIN_SYSTEM, _ROUTER_SYSTEM) is read once at
module import time, not per-call.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", required=True,
                     help="scaffold JSON path; missing file means every prompt uses its default")
    ap.add_argument("--prompt-file", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--quiet", action="store_true",
                     help="accepted for CLI-shape compatibility; loguru already writes to stderr")
    args = ap.parse_args()

    # Must be set before the first `import manager...`/`import teams...` below.
    os.environ["VIBE_SCAFFOLD_CONFIG"] = args.config

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from core.state import state           # noqa: E402  (deliberately late)
    from manager.claude_manager import manager  # noqa: E402

    prompt = Path(args.prompt_file).read_text(encoding="utf-8")

    async def _run() -> str:
        # api/server.py's FastAPI lifespan does this once at process startup;
        # a one-shot process has no lifespan, so it happens here instead.
        # Idempotent (CREATE TABLE IF NOT EXISTS) -- safe to call every run.
        await state.init()
        return await manager.handle_user_request(prompt)

    answer = asyncio.run(_run())
    Path(args.out).write_text(answer, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
