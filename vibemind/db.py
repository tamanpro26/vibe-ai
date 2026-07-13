"""
vibemind/db.py
Async SQLite persistence, mirroring core/state.py's established pattern
(aiosqlite, raw SQL, no ORM) rather than introducing Drizzle+MySQL as the
Manus scaffold did. Two deliberate departures from that original schema:
  - No `users`/OAuth tables: this is a personal, single-user local desktop
    assistant, not a hosted multi-tenant SaaS product -- there's no "user_id"
    to foreign-key against, so it's dropped rather than faked.
  - SQLite instead of MySQL: a local desktop app shouldn't require a running
    MySQL server just to remember conversation history.
The table SHAPES (conversations, messages, tasks, action_logs, agent_status,
voice_transcriptions) are kept close to the original design in
jarvis-ai-assistant/drizzle/schema.ts -- that part was genuinely well thought
out, just built on the wrong storage engine for this use case.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite
from loguru import logger

_DEFAULT_DB_PATH = Path.home() / ".vibemind" / "vibemind.db"
_UNSET = object()   # distinguishes "argument not passed" from "explicitly None"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class VibeMindDB:
    def __init__(self, db_path: str | Path | None = None) -> None:
        self._db_path = str(db_path or _DEFAULT_DB_PATH)
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)

    async def init(self) -> None:
        async with aiosqlite.connect(self._db_path) as db:
            await db.executescript("""
                CREATE TABLE IF NOT EXISTS conversations (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    title        TEXT,
                    agent_mode   TEXT NOT NULL DEFAULT 'brain',
                    created_at   TEXT NOT NULL,
                    updated_at   TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS messages (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id INTEGER NOT NULL,
                    role            TEXT NOT NULL,
                    content         TEXT NOT NULL,
                    agent_label     TEXT,
                    metadata        TEXT,
                    created_at      TEXT NOT NULL,
                    FOREIGN KEY (conversation_id) REFERENCES conversations(id)
                );

                CREATE TABLE IF NOT EXISTS tasks (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id INTEGER,
                    title           TEXT NOT NULL,
                    description     TEXT,
                    status          TEXT NOT NULL DEFAULT 'queued',
                    priority        TEXT NOT NULL DEFAULT 'medium',
                    assigned_agent  TEXT,
                    planned_steps   TEXT,
                    progress        INTEGER DEFAULT 0,
                    result          TEXT,
                    error           TEXT,
                    created_at      TEXT NOT NULL,
                    updated_at      TEXT NOT NULL,
                    completed_at    TEXT
                );

                CREATE TABLE IF NOT EXISTS action_logs (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id         INTEGER,
                    conversation_id INTEGER,
                    action          TEXT NOT NULL,
                    details         TEXT,
                    agent           TEXT,
                    status          TEXT NOT NULL DEFAULT 'pending',
                    timestamp       TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS agent_status (
                    agent_name      TEXT PRIMARY KEY,
                    status          TEXT NOT NULL DEFAULT 'idle',
                    current_task    TEXT,
                    tasks_completed INTEGER DEFAULT 0,
                    last_active     TEXT NOT NULL,
                    model_used      TEXT
                );

                CREATE TABLE IF NOT EXISTS voice_transcriptions (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id INTEGER,
                    transcription   TEXT,
                    confidence      REAL DEFAULT 0,
                    created_at      TEXT NOT NULL
                );
            """)
            await db.commit()
        logger.info(f"[vibemind-db] ready at {self._db_path}")

    # ── Conversations ──────────────────────────────────────────────────

    async def create_conversation(self, title: str | None = None, agent_mode: str = "brain") -> int:
        async with aiosqlite.connect(self._db_path) as db:
            cur = await db.execute(
                "INSERT INTO conversations (title, agent_mode, created_at, updated_at) VALUES (?, ?, ?, ?)",
                (title, agent_mode, _now(), _now()),
            )
            await db.commit()
            return cur.lastrowid

    async def get_conversations(self) -> list[dict]:
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT * FROM conversations ORDER BY updated_at DESC")
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def touch_conversation(self, conversation_id: int) -> None:
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute("UPDATE conversations SET updated_at=? WHERE id=?", (_now(), conversation_id))
            await db.commit()

    # ── Messages ───────────────────────────────────────────────────────

    async def add_message(self, conversation_id: int, role: str, content: str,
                           agent_label: str | None = None, metadata: dict | None = None) -> int:
        async with aiosqlite.connect(self._db_path) as db:
            cur = await db.execute(
                """INSERT INTO messages (conversation_id, role, content, agent_label, metadata, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (conversation_id, role, content, agent_label,
                 json.dumps(metadata) if metadata else None, _now()),
            )
            await db.commit()
            return cur.lastrowid

    async def get_messages(self, conversation_id: int) -> list[dict]:
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT * FROM messages WHERE conversation_id=? ORDER BY id", (conversation_id,),
            )
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    # ── Tasks ──────────────────────────────────────────────────────────

    async def create_task(self, title: str, description: str = "", conversation_id: int | None = None,
                           priority: str = "medium") -> int:
        async with aiosqlite.connect(self._db_path) as db:
            cur = await db.execute(
                """INSERT INTO tasks (conversation_id, title, description, priority, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (conversation_id, title, description, priority, _now(), _now()),
            )
            await db.commit()
            return cur.lastrowid

    async def update_task(self, task_id: int, **fields) -> None:
        if not fields:
            return
        if fields.get("status") in ("completed", "failed", "cancelled"):
            fields.setdefault("completed_at", _now())
        fields["updated_at"] = _now()
        set_clause = ", ".join(f"{k}=?" for k in fields)
        values = list(fields.values()) + [task_id]
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(f"UPDATE tasks SET {set_clause} WHERE id=?", values)
            await db.commit()

    async def get_tasks(self, status: str | None = None) -> list[dict]:
        query = "SELECT * FROM tasks"
        params: list = []
        if status:
            query += " WHERE status=?"
            params.append(status)
        query += " ORDER BY created_at DESC"
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(query, params)
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    # ── Action logs ──────────────────────────────────────────────────

    async def create_action_log(self, action: str, details: str, agent: str | None = None,
                                 task_id: int | None = None, conversation_id: int | None = None) -> int:
        async with aiosqlite.connect(self._db_path) as db:
            cur = await db.execute(
                """INSERT INTO action_logs (task_id, conversation_id, action, details, agent, status, timestamp)
                   VALUES (?, ?, ?, ?, ?, 'pending', ?)""",
                (task_id, conversation_id, action, details, agent, _now()),
            )
            await db.commit()
            return cur.lastrowid

    async def update_action_log_status(self, action_id: int, status: str) -> None:
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute("UPDATE action_logs SET status=? WHERE id=?", (status, action_id))
            await db.commit()

    async def get_action_logs(self, limit: int = 100) -> list[dict]:
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT * FROM action_logs ORDER BY id DESC LIMIT ?", (limit,))
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    # ── Agent status ───────────────────────────────────────────────────

    async def upsert_agent_status(self, agent_name: str, status: str | None = None,
                                   current_task: str | object = _UNSET, tasks_completed: int | None = None,
                                   model_used: str | None = None) -> None:
        """current_task defaults to the sentinel _UNSET (meaning "leave
        unchanged") rather than None -- None must remain a settable value
        (clearing current_task once a step finishes). A plain `= None`
        default made "clear it" and "don't touch it" indistinguishable,
        verified live: agent_status.current_task stayed stuck on the last
        step's label forever because the clearing call was silently a no-op."""
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT * FROM agent_status WHERE agent_name=?", (agent_name,))
            existing = await cur.fetchone()
            if existing:
                new_status = status if status is not None else existing["status"]
                new_task = existing["current_task"] if current_task is _UNSET else current_task
                new_completed = tasks_completed if tasks_completed is not None else existing["tasks_completed"]
                new_model = model_used if model_used is not None else existing["model_used"]
                await db.execute(
                    """UPDATE agent_status SET status=?, current_task=?, tasks_completed=?,
                       last_active=?, model_used=? WHERE agent_name=?""",
                    (new_status, new_task, new_completed, _now(), new_model, agent_name),
                )
            else:
                await db.execute(
                    """INSERT INTO agent_status (agent_name, status, current_task, tasks_completed, last_active, model_used)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (agent_name, status or "idle", None if current_task is _UNSET else current_task,
                     tasks_completed or 0, _now(), model_used),
                )
            await db.commit()

    async def get_agent_statuses(self) -> list[dict]:
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT * FROM agent_status ORDER BY agent_name")
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    # ── Voice transcriptions ───────────────────────────────────────────

    async def add_voice_transcription(self, conversation_id: int | None, transcription: str,
                                       confidence: float = 0.0) -> int:
        async with aiosqlite.connect(self._db_path) as db:
            cur = await db.execute(
                """INSERT INTO voice_transcriptions (conversation_id, transcription, confidence, created_at)
                   VALUES (?, ?, ?, ?)""",
                (conversation_id, transcription, confidence, _now()),
            )
            await db.commit()
            return cur.lastrowid


# Singleton, mirroring core/state.py's `state` convention
db = VibeMindDB()
