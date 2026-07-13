"""
core/activity_log.py
Human-readable AI activity log — logs every model call, tool use, and agent
iteration to logs/ai_activity.log so you can see exactly which AI is being
used for each part of every task.

Format:
  ━━━ SESSION 2026-06-28 13:45:00 ━━━

  [13:45:00.123] ▶  TASK: "fix the landing page in the workspace"

  [13:45:00.145] 🤖  gemini_flash → gemini-2.5-flash (google) [agent/coding]
                 in:  "fix the landing page in the workspace"
                 out: → tool_calls: [list_dir, read_file]
                 ✓ 1,234ms

  [13:45:01.380] 🔧  TOOL: list_dir(.) → "📄 index.html 📄 style.css" ✓ 12ms
  [13:45:01.392] 🔧  TOOL: read_file(index.html) → "<!DOCTYPE html><html..." ✓ 3ms

  [13:45:02.500] 🤖  gemini_flash → gemini-2.5-flash (google) [agent/coding]
                 out: → tool_calls: [edit_file]
                 ✓ 890ms

  [13:45:03.400] ✅  DONE: iter=3 | files_edited=1 | commands=0 | 3.2s
"""
from __future__ import annotations

import threading
from datetime import datetime
from pathlib import Path

_LOG_DIR  = Path("./logs")
_LOG_FILE = _LOG_DIR / "ai_activity.log"
_LOCK     = threading.Lock()
_STARTED  = False


def _ts() -> str:
    return datetime.now().strftime("%H:%M:%S.%f")[:12]  # HH:MM:SS.mmm


def _write(line: str) -> None:
    try:
        _LOG_DIR.mkdir(parents=True, exist_ok=True)
        with _LOCK:
            with open(_LOG_FILE, "a", encoding="utf-8") as f:
                f.write(line + "\n")
    except Exception:
        pass  # never let logging crash the pipeline


class ActivityLog:

    def __init__(self) -> None:
        self._start_session()

    def _start_session(self) -> None:
        global _STARTED
        if _STARTED:
            return
        _STARTED = True
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        _write(f"\n{'━' * 70}")
        _write(f"SESSION  {ts}  [VibeAI v4]")
        _write(f"{'━' * 70}\n")

    # ── Public logging methods ─────────────────────────────────────────────────

    def log_task_start(self, prompt: str) -> None:
        preview = prompt[:140].replace("\n", " ")
        _write(f'[{_ts()}] ▶  TASK: "{preview}"')

    def log_model(
        self,
        model_id:    str,
        api_model:   str,
        provider:    str,
        role:        str,
        prompt:      str   = "",
        output:      str   = "",
        duration_ms: float = 0,
        success:     bool  = True,
    ) -> None:
        icon = "✓" if success else "✗"
        ms   = f"{duration_ms:.0f}ms" if duration_ms else "—"
        pad  = " " * 17  # align continuation lines under the model name

        lines = [f"[{_ts()}] 🤖  {model_id}  →  {api_model}  ({provider})  [{role}]"]
        if prompt:
            lines.append(f"{pad}in:  \"{prompt[:100].replace(chr(10), ' ')}\"")
        if output:
            lines.append(f"{pad}out: {output[:100].replace(chr(10), ' ')}")
        lines.append(f"{pad}{icon} {ms}")
        _write("\n".join(lines))
        # Collaboration view: every model call in the system passes through
        # here, so this single emit gives the live flow display its per-model
        # lines without instrumenting every team (no-op when nobody watches).
        try:
            from core.collab_viz import emit as _viz
            _viz("model", model_id, status="done" if success else "fail",
                 model=model_id, role=role, duration_ms=duration_ms)
        except Exception:
            pass

    def log_tool(
        self,
        name:           str,
        args_preview:   str   = "",
        result_preview: str   = "",
        duration_ms:    float = 0,
        success:        bool  = True,
    ) -> None:
        icon   = "✓" if success else "✗"
        ms     = f"{duration_ms:.0f}ms" if duration_ms else "—"
        result = result_preview[:90].replace("\n", " ")
        args   = args_preview[:50]
        _write(f"[{_ts()}] 🔧  TOOL: {name}({args}) → {result}  {icon} {ms}")

    def log_agent_iter(self, n: int, model: str) -> None:
        _write(f"[{_ts()}]  ─── Agent iteration {n}/25  (model: {model})")

    def log_vibemind(self, stage: str, info: str) -> None:
        _write(f"[{_ts()}] 🧠  VibeMind [{stage}]: {info}")

    def log_task_done(self, summary: str) -> None:
        _write(f"[{_ts()}] ✅  DONE: {summary}")
        _write("")  # blank separator between tasks


# Module-level singleton — imported everywhere
activity_log = ActivityLog()
