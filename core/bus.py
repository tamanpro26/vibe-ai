"""
core/bus.py
Async message bus — all 31 models publish and subscribe through this.
Uses asyncio queues; swap for Redis streams in production if needed.
"""
from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Callable, Awaitable

from loguru import logger

from core.imcp import IMCPMessage, MessageType


Handler = Callable[[IMCPMessage], Awaitable[None]]


class MessageBus:
    """
    Lightweight async pub/sub bus.

    Usage:
        bus = MessageBus()
        bus.subscribe("claude_sonnet_4.6", my_handler)
        await bus.publish(message)
    """

    def __init__(self) -> None:
        # model_id → list of async handlers
        self._subscribers: dict[str, list[Handler]] = defaultdict(list)
        # global log of every message (capped at 1000)
        self._history: list[IMCPMessage] = []
        self._history_cap = 1000
        self._lock = asyncio.Lock()

    # ── Subscribe / unsubscribe ───────────────────────────────────────

    def subscribe(self, model_id: str, handler: Handler) -> None:
        """Register an async handler for messages addressed to model_id."""
        self._subscribers[model_id].append(handler)
        logger.debug(f"[bus] {model_id} subscribed ({len(self._subscribers[model_id])} handlers)")

    def unsubscribe(self, model_id: str, handler: Handler) -> None:
        try:
            self._subscribers[model_id].remove(handler)
        except ValueError:
            pass

    # ── Publish ───────────────────────────────────────────────────────

    async def publish(self, message: IMCPMessage) -> None:
        """
        Deliver message to all handlers registered for message.to.model.
        Runs all handlers concurrently.
        """
        target = message.to.model

        async with self._lock:
            self._history.append(message)
            if len(self._history) > self._history_cap:
                self._history = self._history[-self._history_cap:]

        logger.info(
            f"[bus] {message.type.value:20s} | "
            f"{message.from_.model:30s} → {target:30s} | "
            f"task={message.task_id}"
        )

        handlers = self._subscribers.get(target, [])
        if not handlers:
            logger.warning(f"[bus] No handlers registered for '{target}' — message dropped")
            return

        await asyncio.gather(*(h(message) for h in handlers), return_exceptions=True)

    # ── Broadcast ─────────────────────────────────────────────────────

    async def broadcast(self, message: IMCPMessage, team: str) -> None:
        """Deliver the same message to every model in a team."""
        targets = [
            mid for mid, handlers in self._subscribers.items()
            if handlers  # only subscribed models
        ]
        for target in targets:
            msg_copy = message.model_copy(
                update={"to": message.to.model_copy(update={"model": target})}
            )
            await self.publish(msg_copy)

    # ── History / debug ───────────────────────────────────────────────

    def history(
        self,
        task_id: str | None = None,
        msg_type: MessageType | None = None,
        limit: int = 50,
    ) -> list[IMCPMessage]:
        msgs = self._history
        if task_id:
            msgs = [m for m in msgs if m.task_id == task_id]
        if msg_type:
            msgs = [m for m in msgs if m.type == msg_type]
        return msgs[-limit:]

    def stats(self) -> dict:
        return {
            "total_messages": len(self._history),
            "subscribers": {k: len(v) for k, v in self._subscribers.items()},
        }


# Singleton bus instance — import across modules
bus = MessageBus()
