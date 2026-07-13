"""
core/change_history.py
Per-workspace, append-only journal of every file mutation the agent makes.

Feature (ii) from the user's expansion list: "the AI has a history of what
changes it made". Every create_file / edit_file / delete_file appends one
JSON line to <workspace>/.vibeai/history.jsonl with a timestamp, the task
that motivated it, and a compact summary of the change — enough to answer
"what did the AI do to my project, when, and why" without storing full file
snapshots (git is the right tool for full content history; this is the
intent trail that git can't capture).

Design constraints:
  - Append-only JSONL: crash-safe (a torn last line loses one entry, not the
    file), greppable, and trivially streamable.
  - Fail-soft everywhere: history recording must NEVER break a run. A
    debugging agent that dies because its own journal hit a disk error would
    be strictly worse than having no journal.
  - The journal lives in .vibeai/, which is hidden from the model's own
    list_dir — the agent must not read, reason about, or corrupt its own
    history mid-run (and it would burn context trying).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger

_HISTORY_DIR  = ".vibeai"
_HISTORY_FILE = "history.jsonl"
_PREVIEW_CHARS = 120


def _history_path(workspace: Path) -> Path:
    return workspace / _HISTORY_DIR / _HISTORY_FILE


def record(
    workspace: Path,
    action:    str,              # "create" | "overwrite" | "edit" | "replace" | "delete"
    path:      str,              # workspace-relative path that was touched
    task:      str = "",         # the task that motivated this change
    **detail,                    # action-specific summary fields
) -> None:
    """Append one change entry. Never raises."""
    try:
        entry = {
            "ts":     datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "action": action,
            "path":   path,
            "task":   task[:200],
            **detail,
        }
        hp = _history_path(workspace)
        hp.parent.mkdir(parents=True, exist_ok=True)
        with hp.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as exc:
        logger.warning(f"[change-history] record skipped: {str(exc)[:60]}")


def get_history(workspace: Path, limit: int = 50) -> list[dict]:
    """Most recent entries, newest last. Empty list when no journal exists
    or on any read/parse problem (torn lines are skipped, not fatal)."""
    hp = _history_path(workspace)
    if not hp.exists():
        return []
    entries: list[dict] = []
    try:
        for line in hp.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue  # torn/corrupt line — skip it, keep the rest
    except OSError as exc:
        logger.warning(f"[change-history] read failed: {str(exc)[:60]}")
        return []
    return entries[-limit:]


def format_history(entries: list[dict]) -> str:
    """Human-readable one-line-per-change rendering, oldest first."""
    if not entries:
        return "No recorded changes in this workspace."
    lines = []
    for e in entries:
        ts     = e.get("ts", "?")
        action = e.get("action", "?").upper()
        path   = e.get("path", "?")
        task   = e.get("task", "")
        extra  = ""
        if e.get("lines") is not None:
            extra = f" ({e['lines']} lines)"
        elif e.get("old_preview") is not None:
            extra = f" ('{e['old_preview'][:40]}' -> '{str(e.get('new_preview', ''))[:40]}')"
        task_part = f"  [task: {task[:60]}]" if task else ""
        lines.append(f"{ts}  {action:<9} {path}{extra}{task_part}")
    return "\n".join(lines)


def preview(text: str) -> str:
    """Bounded single-line preview of file/edit content for journal entries."""
    one_line = " ".join(text.split())
    return one_line[:_PREVIEW_CHARS]
