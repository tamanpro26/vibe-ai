"""
core/collective_memory.py — Shared long-term memory for the VibeMind fleet

All models in the fleet read from and write to the same memory bank.
When VibeMind produces a verified answer, the reasoning path that led to it
is stored. The next time a similar problem appears, the top-K most relevant
past memories are injected into every proposer's context — so the fleet
literally improves over time.

Three memory types:
  reasoning_success  — verified correct reasoning path + answer
  debate_insight     — winning argument that caused other units to flip
  failure_pattern    — what the models initially got wrong (avoid this)

Storage: ChromaDB collection 'vibemind_collective' (same dir as pipeline
memory, different collection). Embedder is borrowed from the pipeline
memory singleton if already loaded, to avoid loading the model twice.
"""
from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from loguru import logger


_MEMORY_TYPES = Literal["reasoning_success", "debate_insight", "failure_pattern"]

COLLECTION = "vibemind_collective"
PERSIST_DIR = "./logs/memory"
MIN_SCORE   = 0.45   # cosine-similarity threshold for retrieval


@dataclass
class _MemoryRecord:
    record_id:   str
    memory_type: str
    problem:     str
    content:     str         # what to inject into proposer context
    answer:      str = ""    # the verified final answer
    confidence:  float = 0.0
    method:      str = ""    # execution | debate | consensus
    timestamp:   float = 0.0


class CollectiveMemory:
    """Shared reasoning memory bank for the VibeMind fleet."""

    def __init__(self) -> None:
        self._col    = None
        self._emb    = None
        self._ready  = False

    # ── Initialisation ────────────────────────────────────────────────────────

    async def init(self) -> bool:
        """
        Initialise the ChromaDB collection.
        Borrows the embedding model from the pipeline memory singleton if it
        is already loaded (avoids loading all-MiniLM-L6-v2 twice).
        """
        try:
            import chromadb

            # Try to borrow the already-loaded embedder from pipeline memory
            try:
                from tools.memory import memory as _pipeline_mem
                if getattr(_pipeline_mem, "_ready", False) and _pipeline_mem._emb is not None:
                    self._emb = _pipeline_mem._emb
                    logger.info("[collective_memory] borrowing embedder from pipeline memory")
            except Exception:
                pass

            if self._emb is None:
                from sentence_transformers import SentenceTransformer
                self._emb = SentenceTransformer("all-MiniLM-L6-v2")
                logger.info("[collective_memory] loaded embedder independently")

            Path(PERSIST_DIR).mkdir(parents=True, exist_ok=True)
            client     = chromadb.PersistentClient(path=PERSIST_DIR)
            self._col  = client.get_or_create_collection(
                name=COLLECTION,
                metadata={"hnsw:space": "cosine"},
            )
            self._ready = True
            logger.info(
                f"[collective_memory] ready | docs={self._col.count()}"
            )
            return True

        except ImportError as exc:
            logger.warning(
                f"[collective_memory] deps missing ({exc}). "
                "Install with: pip install chromadb sentence-transformers"
            )
            return False
        except Exception as exc:
            logger.warning(f"[collective_memory] init failed: {exc}")
            return False

    # ── Retrieval ─────────────────────────────────────────────────────────────

    async def retrieve(self, problem: str, top_k: int = 3) -> str:
        """
        Semantic search for the top-K most relevant past memories.
        Returns a formatted string ready to inject into proposer prompts,
        or "" when memory is empty or nothing relevant is found.
        """
        if not self._ready or not self._col or self._col.count() == 0:
            return ""

        try:
            embedding = self._embed(problem)
            results   = self._col.query(
                query_embeddings=[embedding] if embedding else None,
                query_texts=[problem]       if not embedding else None,
                n_results=min(top_k, self._col.count()),
            )

            docs       = results.get("documents", [[]])[0]
            distances  = results.get("distances",  [[]])[0]
            metadatas  = results.get("metadatas",  [[]])[0]

            relevant = [
                (doc, 1 - dist, meta)
                for doc, dist, meta in zip(docs, distances, metadatas)
                if (1 - dist) >= MIN_SCORE
            ]
            if not relevant:
                return ""

            logger.info(
                f"[collective_memory] retrieved {len(relevant)} memories "
                f"for: {problem[:55]}"
            )

            lines = ["[PAST MEMORY FROM COLLECTIVE — hints only; verify independently]\n"]
            for i, (doc, score, meta) in enumerate(relevant, 1):
                mtype  = meta.get("memory_type", "")
                method = meta.get("method", "")
                conf   = meta.get("confidence", 0.0)
                # Use pre-compressed brief (80 tokens) stored at write time
                # Falls back to doc[:200] for legacy records without brief
                brief  = meta.get("brief", doc[:200])
                tag    = f"{mtype} · {method} · {conf:.0%}" if method else mtype
                lines.append(f"[{i}] sim={score:.2f} {tag}: {brief}")
            lines.append("[END MEMORY]\n")
            return "\n".join(lines)

        except Exception as exc:
            logger.warning(f"[collective_memory] retrieve failed: {exc}")
            return ""

    # ── Write: success ────────────────────────────────────────────────────────

    async def remember_success(
        self,
        problem:    str,
        approach:   str,
        answer:     str,
        method:     str,
        confidence: float,
    ) -> None:
        """
        Store a verified correct reasoning path.
        Called when execution or high-confidence debate confirms the answer.
        """
        content = (
            f"PROBLEM: {problem[:400]}\n\n"
            f"APPROACH THAT WORKED:\n{approach[:600]}\n\n"
            f"VERIFIED ANSWER: {answer[:200]}"
        )
        await self._store(
            memory_type="reasoning_success",
            problem=problem,
            content=content,
            answer=answer,
            method=method,
            confidence=confidence,
        )
        logger.info(
            f"[collective_memory] stored reasoning_success | method={method} | "
            f"conf={confidence:.0%} | answer={answer[:40]}"
        )

    # ── Write: debate insight ─────────────────────────────────────────────────

    async def remember_debate(
        self,
        problem:          str,
        winning_consensus: str,
        agreement_before: float,
        agreement_after:  float,
    ) -> None:
        """
        Store a debate-earned consensus.
        Called when the debate round meaningfully improved agreement
        (not just noise), so future proposers can skip the controversy
        and start closer to the correct answer.
        """
        content = (
            f"PROBLEM: {problem[:400]}\n\n"
            f"DEBATE OUTCOME: Agreement improved {agreement_before:.0%} → "
            f"{agreement_after:.0%} after cross-examination.\n\n"
            f"CONVERGED ANSWER: {winning_consensus[:200]}"
        )
        await self._store(
            memory_type="debate_insight",
            problem=problem,
            content=content,
            answer=winning_consensus,
            method="debate",
            confidence=agreement_after,
        )
        logger.info(
            f"[collective_memory] stored debate_insight | "
            f"{agreement_before:.0%}→{agreement_after:.0%} | "
            f"answer={winning_consensus[:40]}"
        )

    # ── Write: failure pattern ────────────────────────────────────────────────

    async def remember_failure(
        self,
        problem:       str,
        wrong_approach: str,
        correction:    str,
    ) -> None:
        """
        Store what the fleet got wrong and how it was corrected.
        Future proposers see this as an explicit 'avoid this' signal.
        """
        content = (
            f"PROBLEM: {problem[:400]}\n\n"
            f"WRONG APPROACH (avoid): {wrong_approach[:400]}\n\n"
            f"CORRECT ANSWER (from execution): {correction[:200]}"
        )
        await self._store(
            memory_type="failure_pattern",
            problem=problem,
            content=content,
            answer=correction,
            method="execution",
            confidence=0.9,
        )
        logger.info(
            f"[collective_memory] stored failure_pattern | "
            f"wrong={wrong_approach[:40]} | correct={correction[:40]}"
        )

    # ── Stats ─────────────────────────────────────────────────────────────────

    def stats(self) -> dict:
        if not self._ready or not self._col:
            return {"ready": False, "docs": 0}
        counts: dict[str, int] = {}
        try:
            all_meta = self._col.get(include=["metadatas"])["metadatas"] or []
            for m in all_meta:
                t = m.get("memory_type", "unknown")
                counts[t] = counts.get(t, 0) + 1
        except Exception:
            pass
        return {
            "ready": True,
            "docs": self._col.count(),
            "by_type": counts,
            "dir": PERSIST_DIR,
        }

    # ── Internals ─────────────────────────────────────────────────────────────

    def _embed(self, text: str) -> list[float]:
        if not self._emb:
            return []
        return self._emb.encode(text, normalize_embeddings=True).tolist()

    @staticmethod
    def _make_brief(problem: str, answer: str, memory_type: str, content: str) -> str:
        """Compact 80-token summary stored at write time, injected instead of full doc."""
        p = problem[:55].rstrip()
        a = answer[:80]
        if memory_type == "reasoning_success":
            return f"[✓] '{p}' → {a}"
        if memory_type == "failure_pattern":
            wrong = ""
            for line in content.split("\n"):
                if "WRONG APPROACH" in line.upper():
                    wrong = line.replace("WRONG APPROACH (avoid):", "").strip()[:50]
                    break
            return f"[✗] '{p}' avoid:{wrong} → correct:{a}"
        if memory_type == "debate_insight":
            return f"[⚡] '{p}' debate→{a}"
        return content[:200]

    async def _store(
        self,
        memory_type: str,
        problem:     str,
        content:     str,
        answer:      str,
        method:      str,
        confidence:  float,
    ) -> None:
        if not self._ready or not self._col:
            return
        try:
            rid       = f"vm_{uuid.uuid4().hex[:8]}"
            embedding = self._embed(content)
            brief     = self._make_brief(problem, answer, memory_type, content)
            self._col.add(
                ids=[rid],
                documents=[content],
                metadatas=[{
                    "memory_type": memory_type,
                    "problem":     problem[:200],
                    "answer":      answer[:200],
                    "brief":       brief,
                    "method":      method,
                    "confidence":  confidence,
                    "timestamp":   time.time(),
                }],
                embeddings=[embedding] if embedding else None,
            )
        except Exception as exc:
            logger.warning(f"[collective_memory] store failed: {exc}")


# Singleton — one shared mind
collective_memory = CollectiveMemory()
