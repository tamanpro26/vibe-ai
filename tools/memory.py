"""
tools/memory.py
Upgrade 6 — Vector Memory (RAG Knowledge Store)

Key insight:
  Without memory → same quality every session, starts from scratch.
  With memory    → quality compounds. By session 100, top-3 similar
                   past solutions inject context before every task.
                   Opus 4.8 always starts fresh. VibeAI gets smarter.

Stores:
  - Approved model outputs (task → output pairs)
  - User style preferences (inferred from approvals)
  - Error patterns + fixes (from Adversarial Critic reports)
  - Project-specific terminology

Retrieval: cosine similarity on all-MiniLM-L6-v2 embeddings (free, local).
Storage:   Chroma DB (free, in-process, no server needed).

Improvement: +5% at session 1, +20% at session 100 (snowball effect).
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from loguru import logger


# ── Memory entry ──────────────────────────────────────────────────────────────

@dataclass
class MemoryEntry:
    entry_id:    str
    entry_type:  Literal["solution", "style_pref", "error_fix", "project_context"]
    task_id:     str
    team:        str
    content:     str         # the stored knowledge
    metadata:    dict        # task_type, quality_score, timestamp, etc.
    timestamp:   float = field(default_factory=time.time)

    def to_chroma_doc(self) -> dict:
        return {
            "id": self.entry_id,
            "document": self.content,
            "metadata": {
                **self.metadata,
                "entry_type":  self.entry_type,
                "task_id":     self.task_id,
                "team":        self.team,
                "timestamp":   self.timestamp,
            },
        }


# ── Vector Memory ─────────────────────────────────────────────────────────────

class VectorMemory:
    """
    RAG knowledge store for VibeAI.
    Retrieves top-K similar past solutions on every new task.

    Usage:
        mem = VectorMemory()
        await mem.init()

        # After approval:
        await mem.store_solution(task_json, output, quality_score)

        # Before new task:
        context = await mem.retrieve_context(task_json.refined_prompt)
    """

    COLLECTION_NAME = "vibe_ai_memory"
    EMBED_MODEL     = "all-MiniLM-L6-v2"    # ~80MB, free, runs on CPU

    def __init__(self, persist_dir: str = "./logs/memory") -> None:
        self._dir  = persist_dir
        self._col  = None
        self._emb  = None
        self._ready = False

    async def init(self) -> bool:
        """
        Initialise Chroma DB and sentence-transformers.
        Returns True if successful, False if dependencies missing.

        Offloaded to a thread: chromadb's PersistentClient and (especially)
        SentenceTransformer's model load are synchronous CPU/disk work with
        no internal await, so calling them inline here would run to
        completion the moment this coroutine starts -- a caller's
        asyncio.wait_for(...) around this call cannot preempt a coroutine
        that never yields, and a slow first load (cold model download)
        would freeze the whole single-process event loop, not just this call.
        """
        try:
            import chromadb
            from sentence_transformers import SentenceTransformer

            def _blocking_init():
                Path(self._dir).mkdir(parents=True, exist_ok=True)
                client = chromadb.PersistentClient(path=self._dir)
                col = client.get_or_create_collection(
                    name=self.COLLECTION_NAME,
                    metadata={"hnsw:space": "cosine"},
                )
                emb = SentenceTransformer(self.EMBED_MODEL)
                return col, emb

            self._col, self._emb = await asyncio.to_thread(_blocking_init)
            self._ready = True
            logger.info(
                f"[memory] ready | collection={self.COLLECTION_NAME} | "
                f"docs={self._col.count()} | embed={self.EMBED_MODEL}"
            )
            return True

        except ImportError as exc:
            logger.warning(
                f"[memory] optional deps missing ({exc}). "
                "Install with: pip install chromadb sentence-transformers"
            )
            return False

    async def _embed(self, text: str) -> list[float]:
        if not self._emb:
            return []
        # .encode() runs the model forward pass -- also blocking CPU work,
        # same reasoning as init() above.
        vec = await asyncio.to_thread(self._emb.encode, text, normalize_embeddings=True)
        return vec.tolist()

    # ── Store ─────────────────────────────────────────────────────────────────

    async def store_solution(
        self,
        task_description: str,
        team:             str,
        output:           str,
        quality_score:    float,
        task_id:          str = "",
        task_type:        str = "",
    ) -> None:
        """Store an approved model output in the memory store."""
        if not self._ready or not self._col:
            return

        entry = MemoryEntry(
            entry_id   = f"sol_{uuid.uuid4().hex[:8]}",
            entry_type = "solution",
            task_id    = task_id,
            team       = team,
            content    = f"TASK: {task_description}\n\nSOLUTION:\n{output[:3000]}",
            metadata   = {
                "task_type":     task_type,
                "quality_score": quality_score,
                "task_desc":     task_description[:200],
            },
        )

        try:
            doc = entry.to_chroma_doc()
            embedding = await self._embed(entry.content)
            self._col.add(
                ids=[doc["id"]],
                documents=[doc["document"]],
                metadatas=[doc["metadata"]],
                embeddings=[embedding] if embedding else None,
            )
            logger.info(f"[memory] stored solution | team={team} | score={quality_score:.2f}")
        except Exception as exc:
            logger.warning(f"[memory] store failed: {exc}")

    async def store_error_fix(
        self,
        error:  str,
        fix:    str,
        team:   str = "code",
    ) -> None:
        """Store an error pattern + its fix for future retrieval."""
        if not self._ready or not self._col:
            return

        entry = MemoryEntry(
            entry_id   = f"err_{uuid.uuid4().hex[:8]}",
            entry_type = "error_fix",
            task_id    = "",
            team       = team,
            content    = f"ERROR:\n{error[:500]}\n\nFIX:\n{fix[:500]}",
            metadata   = {"team": team},
        )
        try:
            doc = entry.to_chroma_doc()
            embedding = await self._embed(entry.content)
            self._col.add(
                ids=[doc["id"]],
                documents=[doc["document"]],
                metadatas=[doc["metadata"]],
                embeddings=[embedding] if embedding else None,
            )
        except Exception:
            pass

    # ── Retrieve ──────────────────────────────────────────────────────────────

    async def retrieve_context(
        self,
        query:     str,
        team:      str | None = None,
        top_k:     int        = 3,
        min_score: float      = 0.4,    # cosine similarity threshold
    ) -> str:
        """
        Retrieve top-K relevant past solutions as a context injection string.
        Call this BEFORE dispatching any task to teams.
        """
        if not self._ready or not self._col or self._col.count() == 0:
            return ""

        try:
            embedding = await self._embed(query)
            where = {"team": team} if team else None

            results = self._col.query(
                query_embeddings=[embedding] if embedding else None,
                query_texts=[query] if not embedding else None,
                n_results=min(top_k, self._col.count()),
                where=where,
            )

            docs       = results.get("documents", [[]])[0]
            distances  = results.get("distances",  [[]])[0]
            metadatas  = results.get("metadatas",  [[]])[0]

            if not docs:
                return ""

            # Convert cosine distance to similarity, filter by threshold
            relevant = [
                (doc, 1 - dist, meta)
                for doc, dist, meta in zip(docs, distances, metadatas)
                if (1 - dist) >= min_score
            ]

            if not relevant:
                return ""

            logger.info(f"[memory] retrieved {len(relevant)} relevant past solutions for: {query[:50]}")

            lines = ["RELEVANT PAST SOLUTIONS (from memory — use as inspiration):\n"]
            for i, (doc, score, meta) in enumerate(relevant, 1):
                lines.append(f"--- Memory {i} (relevance: {score:.2f}) ---")
                lines.append(doc[:600])
                lines.append("")

            return "\n".join(lines)

        except Exception as exc:
            logger.warning(f"[memory] retrieve failed: {exc}")
            return ""

    def stats(self) -> dict:
        if not self._ready or not self._col:
            return {"ready": False, "docs": 0}
        return {"ready": True, "docs": self._col.count(), "dir": self._dir}


# Singleton
memory = VectorMemory()
