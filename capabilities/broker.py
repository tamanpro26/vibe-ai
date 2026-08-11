"""Durable, owner-bound confirmation broker."""

from __future__ import annotations

from capabilities.policy import ActionPolicy


class ActionBroker:
    def __init__(self, store, policy: ActionPolicy | None = None) -> None:
        self.store = store
        self.policy = policy or ActionPolicy()

    async def propose(self, owner_id: str, proposal, *, workflow_id: str | None = None):
        self.policy.validate_shape(proposal)
        action = await self.store.create_action_request(
            owner_id, proposal, workflow_id=workflow_id
        )
        await self.store.append_audit(
            owner_id,
            "action.proposed",
            action.id,
            {
                "operation": action.operation,
                "request_digest": action.request_digest,
                "shared_data": action.shared_data,
                "mutable_resources": action.mutable_resources,
            },
        )
        return action

    async def approve(self, owner_id: str, action_id: str, request_digest: str):
        action = await self.store.approve_action(owner_id, action_id, request_digest)
        await self.store.append_audit(
            owner_id, "action.approved", action.id, {"request_digest": request_digest}
        )
        return action

    async def deny(self, owner_id: str, action_id: str):
        action = await self.store.deny_action(owner_id, action_id)
        await self.store.append_audit(owner_id, "action.denied", action.id, {})
        return action

    async def get(self, owner_id: str, action_id: str):
        return await self.store.get_action_request(owner_id, action_id)

    async def pending(self, owner_id: str):
        return await self.store.list_pending_actions(owner_id)
