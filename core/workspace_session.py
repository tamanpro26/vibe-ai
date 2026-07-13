"""
core/workspace_session.py
Active-workspace + multi-chat state for the CLI.

Two capabilities the user asked for, in one small stateful object:

  1. Workspace choosing — the agent no longer operates on one fixed folder.
     `switch_workspace(path)` points all subsequent work at a chosen folder;
     recently-used folders are remembered (~/.vibeai/recent.json) so the CLI
     can offer them by number.

  2. Multiple chats per workspace — a "chat" is a named conversation with its
     own message history, scoped to a workspace FOLDER. You can keep several
     going for one project (e.g. "auth", "styling", "bugfix") and switch
     between them. Chats persist inside the workspace at
     <workspace>/.vibeai/chats/<slug>.json, so they travel with the folder
     (like .git) and survive CLI restarts.

Design notes:
  - Reuses the .vibeai/ convention already established by change_history.py;
    that directory is already hidden from the agent's own list_dir and from
    the verifier battery, so chat files never pollute the model's context.
  - Persistence is fail-soft: a corrupt/missing chat file yields an empty
    history rather than crashing the CLI. Losing a conversation log is
    annoying; a CLI that won't start is worse.
  - Pure state + disk I/O, no LLM calls — fully unit-testable offline.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger

_DEFAULT_CHAT = "main"
_RECENT_PATH  = Path.home() / ".vibeai" / "recent.json"
_MAX_RECENT   = 8


def _slug(name: str) -> str:
    """Filesystem-safe slug for a chat name; the display name is preserved
    inside the file, so this only needs to be unique + safe, not pretty."""
    s = re.sub(r"[^a-zA-Z0-9_-]+", "-", name.strip().lower()).strip("-")
    return s or "chat"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class ChatInfo:
    name:      str
    slug:      str
    messages:  int
    updated:   str


class WorkspaceSession:
    def __init__(self, workspace: Path) -> None:
        self.workspace = Path(workspace).resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.chat_name  = _DEFAULT_CHAT
        self._messages: list[dict] = []
        self._remember_workspace(self.workspace)
        self._load_chat(_DEFAULT_CHAT)

    # ── paths ──────────────────────────────────────────────────────────────────

    @property
    def _chats_dir(self) -> Path:
        return self.workspace / ".vibeai" / "chats"

    def _chat_file(self, name: str) -> Path:
        return self._chats_dir / f"{_slug(name)}.json"

    # ── workspace switching ────────────────────────────────────────────────────

    def switch_workspace(self, path: str | Path) -> None:
        """Point subsequent work at a different folder, resetting to that
        folder's 'main' chat. Raises ValueError if the path exists but is a
        file; a non-existent directory is CREATED (choosing a fresh project
        folder is a normal, expected action)."""
        p = Path(path).expanduser().resolve()
        if p.exists() and not p.is_dir():
            raise ValueError(f"Not a directory: {p}")
        p.mkdir(parents=True, exist_ok=True)
        self._save_chat()                 # flush the outgoing workspace's chat
        self.workspace = p
        self._remember_workspace(p)
        self.chat_name = _DEFAULT_CHAT
        self._messages = []
        self._load_chat(_DEFAULT_CHAT)

    # ── chat management ────────────────────────────────────────────────────────

    def list_chats(self) -> list[ChatInfo]:
        out: list[ChatInfo] = []
        if not self._chats_dir.exists():
            return out
        for f in sorted(self._chats_dir.glob("*.json")):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            out.append(ChatInfo(
                name=data.get("name", f.stem),
                slug=f.stem,
                messages=len(data.get("messages", [])),
                updated=data.get("updated", "?"),
            ))
        return out

    def new_chat(self, name: str) -> None:
        """Create and switch to a new chat. Raises ValueError if a chat with
        the same slug already exists (switch to it instead of clobbering)."""
        name = name.strip()
        if not name:
            raise ValueError("Chat name cannot be empty.")
        if self._chat_file(name).exists():
            raise ValueError(f"Chat '{name}' already exists — use /chat switch {name}")
        self._save_chat()                 # flush current before leaving it
        self.chat_name = name
        self._messages = []
        self._save_chat()                 # materialize the new (empty) chat file

    def switch_chat(self, name: str) -> None:
        """Switch to an existing chat. Raises ValueError if it doesn't exist."""
        if not self._chat_file(name).exists():
            raise ValueError(f"No chat named '{name}' — use /chat new {name} to create it")
        self._save_chat()
        self._load_chat(name)

    def delete_chat(self, name: str) -> None:
        """Delete a chat file. Deleting the ACTIVE chat resets to 'main'
        (recreating an empty main if needed) so there's always a valid chat."""
        f = self._chat_file(name)
        if not f.exists():
            raise ValueError(f"No chat named '{name}'")
        was_active = _slug(name) == _slug(self.chat_name)
        f.unlink()
        if was_active:
            self.chat_name = _DEFAULT_CHAT
            self._messages = []
            self._load_chat(_DEFAULT_CHAT)

    def rename_chat(self, old: str, new: str) -> None:
        new = new.strip()
        if not new:
            raise ValueError("New chat name cannot be empty.")
        src = self._chat_file(old)
        if not src.exists():
            raise ValueError(f"No chat named '{old}'")
        if self._chat_file(new).exists() and _slug(new) != _slug(old):
            raise ValueError(f"Chat '{new}' already exists")
        try:
            data = json.loads(src.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            data = {"messages": []}
        data["name"] = new
        data["updated"] = _now()
        self._chat_file(new).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        if _slug(new) != _slug(old):
            src.unlink(missing_ok=True)
        if _slug(old) == _slug(self.chat_name):
            self.chat_name = new

    # ── message history ────────────────────────────────────────────────────────

    def history(self) -> list[dict]:
        """Copy of the current chat's messages (role/content dicts)."""
        return list(self._messages)

    def append(self, role: str, content: str) -> None:
        self._messages.append({"role": role, "content": content})
        self._save_chat()

    def replace_history(self, messages: list[dict]) -> None:
        """Overwrite the current chat's messages (used after summarization)."""
        self._messages = list(messages)
        self._save_chat()

    def clear_current(self) -> None:
        self._messages = []
        self._save_chat()

    # ── persistence (fail-soft) ────────────────────────────────────────────────

    def _load_chat(self, name: str) -> None:
        self.chat_name = name
        f = self._chat_file(name)
        if not f.exists():
            self._messages = []
            return
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            self.chat_name = data.get("name", name)
            self._messages = data.get("messages", []) or []
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning(f"[workspace-session] chat '{name}' unreadable ({str(exc)[:50]}) — starting empty")
            self._messages = []

    def _save_chat(self) -> None:
        try:
            self._chats_dir.mkdir(parents=True, exist_ok=True)
            payload = {
                "name":     self.chat_name,
                "updated":  _now(),
                "messages": self._messages,
            }
            self._chat_file(self.chat_name).write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception as exc:
            logger.warning(f"[workspace-session] chat save skipped: {str(exc)[:60]}")

    # ── recent workspaces (~/.vibeai/recent.json) ──────────────────────────────

    @staticmethod
    def recent_workspaces() -> list[str]:
        try:
            data = json.loads(_RECENT_PATH.read_text(encoding="utf-8"))
            return [p for p in data if Path(p).is_dir()][:_MAX_RECENT]
        except (json.JSONDecodeError, OSError):
            return []

    @staticmethod
    def _remember_workspace(path: Path) -> None:
        try:
            existing = []
            if _RECENT_PATH.exists():
                try:
                    existing = json.loads(_RECENT_PATH.read_text(encoding="utf-8"))
                except json.JSONDecodeError:
                    existing = []
            s = str(path)
            existing = [s] + [p for p in existing if p != s]
            _RECENT_PATH.parent.mkdir(parents=True, exist_ok=True)
            _RECENT_PATH.write_text(
                json.dumps(existing[:_MAX_RECENT], ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except Exception as exc:
            logger.warning(f"[workspace-session] recent-list update skipped: {str(exc)[:50]}")
