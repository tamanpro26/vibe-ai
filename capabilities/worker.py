"""Outbox worker with an explicit pre-send durability boundary."""

from __future__ import annotations

from capabilities.models import ActionStatus
from capabilities.policy import ActionPolicy


class SimulatedProcessDeath(BaseException):
    """Test-only crash signal deliberately excluded from Exception handlers."""


class ActionWorker:
    def __init__(self, store, adapters: dict[str, object], policy: ActionPolicy | None = None):
        self.store = store
        self.adapters = adapters
        self.policy = policy or ActionPolicy()

    async def run_once(self, worker_id: str):
        action = await self.store.claim_action(worker_id)
        if action is None:
            return None
        try:
            connection = await self.policy.validate_runtime(self.store, action.owner_id, action)
            adapter = self.adapters.get(action.operation)
            if adapter is None:
                raise ValueError("no approved adapter is registered for this operation")
            await adapter.preflight(action, connection)
        except Exception as exc:
            return await self.store.finish_action(
                action.id, worker_id, ActionStatus.INVALIDATED, error=str(exc)
            )
        await self.store.mark_action_send_attempted(action.id, worker_id)
        try:
            result = await adapter.execute(action, connection)
        except Exception as exc:
            return await self.store.finish_action(
                action.id, worker_id, ActionStatus.FAILED, error=str(exc)
            )
        return await self.store.finish_action(
            action.id, worker_id, ActionStatus.SUCCEEDED, result=result
        )
