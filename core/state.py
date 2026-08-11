"""
core/state.py
Session state manager — stores Task JSON, model outputs, and message
history in SQLite so the system survives restarts and long runs.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite
from loguru import logger

from config.settings import settings
from core.imcp import TaskJSON, IMCPMessage, ReviewResult


class StateManager:
    """
    Async SQLite-backed state store.
    Three tables: sessions, task_outputs, review_history.
    """

    def __init__(self, db_path: str | None = None) -> None:
        self._db_path = db_path or settings.session_db_path
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)

    # ── Schema init ───────────────────────────────────────────────────

    async def init(self) -> None:
        async with aiosqlite.connect(self._db_path) as db:
            await db.executescript("""
                CREATE TABLE IF NOT EXISTS sessions (
                    session_id   TEXT PRIMARY KEY,
                    created_at   TEXT NOT NULL,
                    task_json    TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS task_outputs (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id   TEXT NOT NULL,
                    task_id      TEXT NOT NULL,
                    model_id     TEXT NOT NULL,
                    team         TEXT NOT NULL,
                    output       TEXT NOT NULL,
                    quality_score REAL DEFAULT 0.0,
                    approved     INTEGER DEFAULT 0,
                    iteration    INTEGER DEFAULT 1,
                    created_at   TEXT NOT NULL,
                    FOREIGN KEY (session_id) REFERENCES sessions(session_id)
                );

                CREATE TABLE IF NOT EXISTS review_history (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id   TEXT NOT NULL,
                    task_id      TEXT NOT NULL,
                    model_id     TEXT NOT NULL,
                    action       TEXT NOT NULL,
                    quality_score REAL,
                    issues       TEXT,
                    instruction  TEXT,
                    created_at   TEXT NOT NULL
                );
            """)
            await db.commit()
        logger.info(f"[state] Database ready at {self._db_path}")

    # ── Sessions ──────────────────────────────────────────────────────

    async def save_session(self, session_id: str, task_json: TaskJSON) -> None:
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                "INSERT OR REPLACE INTO sessions VALUES (?, ?, ?)",
                (session_id, _now(), task_json.model_dump_json()),
            )
            await db.commit()

    async def load_session(self, session_id: str) -> TaskJSON | None:
        async with aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute(
                "SELECT task_json FROM sessions WHERE session_id = ?",
                (session_id,),
            )
            row = await cursor.fetchone()
        if row:
            return TaskJSON.model_validate_json(row[0])
        return None

    # ── Task outputs ──────────────────────────────────────────────────

    async def save_output(
        self,
        session_id: str,
        task_id: str,
        model_id: str,
        team: str,
        output: str,
        quality_score: float = 0.0,
        approved: bool = False,
        iteration: int = 1,
    ) -> None:
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                """INSERT INTO task_outputs
                   (session_id, task_id, model_id, team, output,
                    quality_score, approved, iteration, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (session_id, task_id, model_id, team, output,
                 quality_score, int(approved), iteration, _now()),
            )
            await db.commit()

    async def get_outputs(
        self, session_id: str, task_id: str, approved_only: bool = False
    ) -> list[dict]:
        query = "SELECT * FROM task_outputs WHERE session_id=? AND task_id=?"
        params: list = [session_id, task_id]
        if approved_only:
            query += " AND approved=1"
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(query, params)
            rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    # ── Review history ────────────────────────────────────────────────

    async def save_review(self, session_id: str, review: ReviewResult) -> None:
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                """INSERT INTO review_history
                   (session_id, task_id, model_id, action,
                    quality_score, issues, instruction, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    session_id,
                    review.task_id,
                    review.model_id,
                    review.action,
                    review.quality_score,
                    json.dumps(review.issues),
                    review.refine_instruction,
                    _now(),
                ),
            )
            await db.commit()

    async def get_review_history(self, session_id: str, task_id: str) -> list[dict]:
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM review_history WHERE session_id=? AND task_id=? ORDER BY id",
                (session_id, task_id),
            )
            rows = await cursor.fetchall()
        return [dict(r) for r in rows]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# Singleton
state = StateManager()


def get_capability_store():
    """Return the lazily-created Capability Hub store."""
    from capabilities.store import CapabilityStore

    global _capability_store
    if _capability_store is None:
        _capability_store = CapabilityStore(
            settings.capability_database_url,
            environment=settings.deployment_environment,
        )
    return _capability_store


_capability_store = None
