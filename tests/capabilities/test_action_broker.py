from __future__ import annotations

import pytest

from capabilities.actions import ProposedAction
from capabilities.broker import ActionBroker
from capabilities.registry import CapabilityRegistry
from capabilities.manifests import ActivationMode
from capabilities.models import ScopeKind, ScopeState
from capabilities.store import CapabilityStore
from capabilities.worker import ActionWorker
from tests.capabilities.test_action_policy import proposal
from tests.capabilities.test_manifests import native_manifest


class FakeAdapter:
    def __init__(self):
        self.preflight_calls = 0
        self.execute_calls = 0

    async def preflight(self, _action, _connection):
        self.preflight_calls += 1
        return {"issue_id": "issue-12", "state": "open"}

    async def execute(self, _action, _connection):
        self.execute_calls += 1
        return {"comment_id": "comment-1", "url": "https://github.test/comment-1"}


async def setup_broker(tmp_path):
    store = CapabilityStore(f"sqlite+aiosqlite:///{tmp_path / 'actions.db'}")
    await store.init()
    registry = CapabilityRegistry(store)
    manifest = native_manifest(
        capability_id="github-issue-comment",
        name="GitHub issue comment",
        description="Appends a user-approved comment to one GitHub issue.",
        kind="approved_action",
        trust="vibeai_builtin",
        risk="medium",
        instructions=None,
        permissions=[{
            "name": "github.issue.comment",
            "purpose": "Append the approved comment body to the selected issue.",
            "shared_data": ["comment_body"],
            "mutable_resources": ["github.issue.comments"],
        }],
        services=[{
            "provider": "github",
            "operations": ["issues:read", "issues:comment"],
            "required": True,
        }],
    )
    draft = await registry.create_draft("vibeai", manifest)
    version = await registry.publish("vibeai", draft.id)
    await store.install("user-1", version.id)
    connection = await store.create_service_connection(
        "user-1", "github", "installation:1", "credential:1",
        ["issues:read", "issues:comment"], ["repository:456"],
    )
    data = proposal(
        capability_version_id=version.id,
        capability_digest=version.content_digest,
        connection_id=connection.id,
        project_id=None,
        chat_id=None,
    )
    return store, ActionBroker(store), data


@pytest.mark.asyncio
async def test_zero_external_calls_before_exact_approval_and_one_after(tmp_path):
    store, broker, data = await setup_broker(tmp_path)
    adapter = FakeAdapter()
    action = await broker.propose("user-1", data)
    assert action.status == "pending"
    assert adapter.preflight_calls == adapter.execute_calls == 0

    approved = await broker.approve("user-1", action.id, action.request_digest)
    duplicate = await broker.approve("user-1", action.id, action.request_digest)
    assert approved.id == duplicate.id
    assert adapter.execute_calls == 0

    await ActionWorker(store, {"github.issue.comment": adapter}).run_once("worker-1")
    completed = await broker.get("user-1", action.id)
    assert completed.status == "succeeded"
    assert adapter.preflight_calls == adapter.execute_calls == 1


@pytest.mark.asyncio
async def test_denial_payload_swap_and_cross_user_ids_never_execute(tmp_path):
    store, broker, data = await setup_broker(tmp_path)
    adapter = FakeAdapter()
    action = await broker.propose("user-1", data)

    with pytest.raises(PermissionError):
        await broker.approve("user-2", action.id, action.request_digest)
    with pytest.raises(ValueError):
        await broker.approve("user-1", action.id, "sha256:" + "0" * 64)
    await broker.deny("user-1", action.id)
    assert await ActionWorker(store, {"github.issue.comment": adapter}).run_once("worker") is None
    assert adapter.execute_calls == 0


@pytest.mark.asyncio
async def test_disabled_or_revoked_capability_is_rechecked_before_dispatch(tmp_path):
    store, broker, data = await setup_broker(tmp_path)
    adapter = FakeAdapter()
    action = await broker.propose("user-1", data)
    await broker.approve("user-1", action.id, action.request_digest)
    version = await store.get_version(data.capability_version_id)
    await store.archive_version("vibeai", version.id)

    await ActionWorker(store, {"github.issue.comment": adapter}).run_once("worker")
    failed = await broker.get("user-1", action.id)
    assert failed.status == "invalidated"
    assert adapter.execute_calls == 0


@pytest.mark.asyncio
async def test_account_disable_blocks_actions_but_chat_enable_overrides_project_disable(tmp_path):
    store, broker, data = await setup_broker(tmp_path)
    await store.set_activation_mode("user-1", ActivationMode.DISABLED)
    with pytest.raises(ValueError, match="disabled for this account"):
        await broker.propose("user-1", data)

    await store.set_activation_mode("user-1", ActivationMode.MANUAL_ONLY)
    await store.register_owned_scope("user-1", ScopeKind.PROJECT, "project-1")
    await store.register_owned_scope("user-1", ScopeKind.CHAT, "chat-1", "project-1")
    installation = await store.get_active_installation(
        "user-1", data.capability_version_id
    )
    await store.set_scope_override(
        "user-1", installation.id, ScopeKind.PROJECT, "project-1", ScopeState.DISABLED
    )
    await store.set_scope_override(
        "user-1", installation.id, ScopeKind.CHAT, "chat-1", ScopeState.ENABLED
    )
    scoped = data.model_copy(update={"project_id": "project-1", "chat_id": "chat-1"})

    assert (await broker.propose("user-1", scoped)).status == "pending"
