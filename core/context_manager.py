"""
core/context_manager.py -- tiered context compaction with a pinned region.

Replaces the "kept 5/14 messages" hard message-dropping truncation (seen live
on Cerebras/Groq) with a tiered scheme that NEVER evicts the load-bearing
parts of the prompt. Concept + constants ported from jcode v0.54.4
(jcode-compaction-core, MIT-licensed).

Why VibeAI needs it (all three observed live this project):
  * whole-message dropping can evict the task spec -> read-loop drift
  * it silently dropped tool definitions once (one of the 4 agent-loop bugs)
  * it lies to downstream components (ReadLoopGuard's cache) about what the
    model can still see -- Feature 3 reads `covers_up_to_turn` from here to
    fix exactly that.

Tiers:
  < SOFT (0.80)   -> fits, do nothing
  < HARD (0.95)   -> soft: kick off a background summary (applies next turn),
                     but do NOT drop anything this turn
  >= HARD         -> hard: synchronous compaction that CANNOT fail the call --
                     pinned region kept verbatim, recent tail kept verbatim,
                     oversized tool results clamped, middle turns -> summary slot

Pinned region (kept verbatim at every tier, by construction): the system
message, anything the caller marks pinned (task spec / plan ledger), and --
because tools are passed to the OpenAI API separately from messages in this
codebase -- tool definitions are never in this list to begin with, so the
"dropped tool defs" bug becomes structurally impossible here.

413 / payload-too-large is a DIFFERENT failure mode from token overflow
(jcode learned this the hard way): on a 413 shrink by BYTES, stripping inline
images/base64 first, not by token estimate.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable

from loguru import logger

# ── Constants (copied near-verbatim from jcode-compaction-core) ─────────────
COMPACTION_THRESHOLD = 0.80     # soft: start background summarization
CRITICAL_THRESHOLD   = 0.95     # hard: synchronous drop so the call can't fail
RECENT_TURNS_TO_KEEP = 10       # recent tail always verbatim
MIN_TURNS_TO_KEEP    = 2        # floor during emergency compaction
EMERGENCY_TOOL_RESULT_MAX_CHARS = 4000   # per-tool-result cap under emergency
SYSTEM_OVERHEAD_TOKENS = 18000  # system prompt + tool defs count against budget
IMAGE_TOKEN_COST = 1600         # flat per image -- never charge base64 length
_CHARS_PER_TOKEN = 3            # conservative char/token ratio (matches connectors)


@dataclass
class CompactionState:
    """The load-bearing export: `covers_up_to_turn` is the exact boundary
    downstream components (Feature 3's ReadResultCache) use to know what is
    still truly in context vs. summarized away."""
    summary_text: str | None = None
    covers_up_to_turn: int = 0     # messages[0:covers_up_to_turn] replaced by summary


@dataclass
class CompactionEvent:
    trigger: str                   # "soft" | "hard" | "none" | "413"
    pre_tokens: int
    post_tokens: int
    messages_before: int
    messages_after: int
    dropped_into_summary: int = 0


def _msg_text(m: dict) -> str:
    c = m.get("content")
    if isinstance(c, list):        # multimodal content parts
        return " ".join(p.get("text", "") for p in c if isinstance(p, dict))
    return str(c or "")


def _image_count(m: dict) -> int:
    c = m.get("content")
    if isinstance(c, list):
        return sum(1 for p in c if isinstance(p, dict) and p.get("type", "").startswith("image"))
    return 0


def estimate_tokens(messages: list[dict], *, system_overhead: bool = True) -> int:
    """Token estimate: text chars/3 + a FLAT cost per image (never the base64
    length) + fixed system/tool-def overhead. Deliberately conservative."""
    total = SYSTEM_OVERHEAD_TOKENS if system_overhead else 0
    for m in messages:
        total += max(1, len(_msg_text(m)) // _CHARS_PER_TOKEN)
        total += _image_count(m) * IMAGE_TOKEN_COST
        tc = m.get("tool_calls")
        if tc:
            try:
                total += max(1, len(json.dumps(tc)) // _CHARS_PER_TOKEN)
            except Exception:
                total += 200
    return total


def _default_pinned(messages: list[dict]) -> set[int]:
    """Pin the system message(s) and the LAST user message (the task/anchor)."""
    pinned = {i for i, m in enumerate(messages) if m.get("role") == "system"}
    for i in range(len(messages) - 1, -1, -1):
        if messages[i].get("role") == "user":
            pinned.add(i)
            break
    return pinned


class ContextManager:
    SOFT, HARD = COMPACTION_THRESHOLD, CRITICAL_THRESHOLD
    RECENT_KEEP, MIN_KEEP = RECENT_TURNS_TO_KEEP, MIN_TURNS_TO_KEEP

    def __init__(
        self,
        budget_tokens: int,
        summarizer: Callable[[list[dict]], str] | None = None,
        pin: Callable[[list[dict]], set[int]] | None = None,
    ) -> None:
        self.budget = max(1, budget_tokens)
        self.summarizer = summarizer            # optional; may be a no-op
        self.pin = pin or _default_pinned
        self.state = CompactionState()
        self.last_event: CompactionEvent | None = None

    # -- top-level decision -------------------------------------------------
    def ensure_fits(self, messages: list[dict]) -> tuple[list[dict], CompactionState]:
        pre = estimate_tokens(messages)
        if pre < self.SOFT * self.budget:
            self._emit(CompactionEvent("none", pre, pre, len(messages), len(messages)))
            return messages, self.state
        if pre < self.HARD * self.budget:
            # soft: prepare a summary for NEXT turn, drop nothing now
            self._maybe_start_summary(messages)
            self._emit(CompactionEvent("soft", pre, pre, len(messages), len(messages)))
            return messages, self.state
        out = self._hard_compact(messages)
        self._emit(CompactionEvent(
            "hard", pre, estimate_tokens(out), len(messages), len(out),
            dropped_into_summary=max(0, len(messages) - len(out))))
        return out, self.state

    # -- hard compaction: cannot fail the call ------------------------------
    def _hard_compact(self, messages: list[dict]) -> list[dict]:
        n = len(messages)
        pinned = self.pin(messages)
        keep_from = max(0, n - self.RECENT_KEEP)

        # indices we keep verbatim: pinned + recent tail
        keep_idx = set(pinned) | set(range(keep_from, n))
        # emergency floor: always keep at least MIN_KEEP most-recent
        keep_idx |= set(range(max(0, n - self.MIN_KEEP), n))

        middle = [i for i in range(n) if i not in keep_idx]
        if middle:
            self._maybe_start_summary([messages[i] for i in middle])
            self.state.covers_up_to_turn = max(middle) + 1

        out: list[dict] = []
        summary_inserted = False
        for i in range(n):
            if i in keep_idx:
                out.append(self._clamp_tool_result(messages[i]))
            elif not summary_inserted:
                out.append(self._summary_message(len(middle)))
                summary_inserted = True
            # else: folded into the already-inserted summary
        return out

    def _clamp_tool_result(self, m: dict) -> dict:
        if m.get("role") != "tool":
            return m
        text = _msg_text(m)
        if len(text) <= EMERGENCY_TOOL_RESULT_MAX_CHARS:
            return m
        clamped = text[:EMERGENCY_TOOL_RESULT_MAX_CHARS] + "\n[...truncated]"
        return {**m, "content": clamped}

    def _summary_message(self, n_dropped: int) -> dict:
        body = self.state.summary_text or f"[{n_dropped} earlier turns compacted]"
        return {"role": "system", "content": f"[EARLIER CONTEXT SUMMARY]\n{body}"}

    def _maybe_start_summary(self, messages: list[dict]) -> None:
        """Produce/refresh the summary. The summarizer is a plug-in (a model
        call, in production; None or a stub in tests). Best-effort -- a
        summarizer failure must never fail compaction."""
        if self.summarizer is None:
            return
        try:
            text = self.summarizer(messages)
            if text:
                self.state.summary_text = text
        except Exception as exc:
            logger.warning(f"[context] summarizer failed: {str(exc)[:80]}")

    # -- 413 / payload-too-large: shrink by BYTES, images first -------------
    @staticmethod
    def shrink_for_413(messages: list[dict]) -> list[dict]:
        """A provider 413 is a byte-size problem, not a token problem. Strip
        inline images/base64 first (they dominate payload bytes), leaving a
        placeholder, before touching any text."""
        out = []
        for m in messages:
            c = m.get("content")
            if isinstance(c, list):
                new_parts = []
                for p in c:
                    if isinstance(p, dict) and p.get("type", "").startswith("image"):
                        new_parts.append({"type": "text", "text": "[image removed to fit payload]"})
                    else:
                        new_parts.append(p)
                out.append({**m, "content": new_parts})
            else:
                out.append(m)
        return out

    # -- observability ------------------------------------------------------
    def _emit(self, ev: CompactionEvent) -> None:
        self.last_event = ev
        if ev.trigger in ("soft", "hard", "413"):
            logger.info(
                f"[context] {ev.trigger} compaction: {ev.pre_tokens}->{ev.post_tokens} tok, "
                f"{ev.messages_before}->{ev.messages_after} msgs, "
                f"covers_up_to_turn={self.state.covers_up_to_turn}"
            )
