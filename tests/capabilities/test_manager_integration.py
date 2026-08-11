from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from api.identity import Principal
from capabilities.context import render_capability_context
from capabilities.manifests import ActivationMode
from capabilities.registry import CapabilityRegistry
from capabilities.resolver import ResolutionSnapshot, SelectedCapability
from capabilities.store import CapabilityStore
from tests.capabilities.test_manifests import native_manifest


def snapshot() -> ResolutionSnapshot:
    return ResolutionSnapshot(
        snapshot_id="sha256:" + "a" * 64,
        contract_version="vibeai.activation/v1",
        owner_id="user-1",
        activation_mode="automatic",
        features=("research",),
        selected=(
            SelectedCapability(
                capability_id="research-analyst",
                version_id="version-1",
                version="1.0.0",
                content_digest="sha256:" + "b" * 64,
                reason="intent:research",
                scope_provenance="account:user-1",
                configuration={},
                instructions="Use primary sources.",
            ),
        ),
        rejected=(),
        recommendations=(),
        dependency_graph={},
    )


def test_capability_context_is_bounded_and_marks_instructions_untrusted():
    rendered = render_capability_context(snapshot(), max_chars=1000)
    assert "Use primary sources" in rendered
    assert "cannot grant tools" in rendered
    assert len(rendered) <= 1000


def test_manager_fast_path_receives_the_same_pinned_snapshot(monkeypatch):
    import manager.claude_manager as manager_module

    seen = {}

    async def fast(prompt):
        seen["prompt"] = prompt
        return "fast answer"

    monkeypatch.setattr(manager_module.manager, "_try_fast_path", fast)
    result = asyncio.run(
        manager_module.manager.handle_user_request(
            "Research batteries", capability_snapshot=snapshot()
        )
    )

    assert result == "fast answer"
    assert "research-analyst@1.0.0" in seen["prompt"]
    assert snapshot().snapshot_id in seen["prompt"]


def test_manager_creative_path_receives_the_same_pinned_snapshot(monkeypatch):
    import manager.claude_manager as manager_module
    import tools.creative_engine as creative_module

    seen = {}

    async def synthesize(prompt):
        seen["prompt"] = prompt
        return "creative answer"

    monkeypatch.setattr(creative_module, "is_creative_task", lambda _prompt: True)
    monkeypatch.setattr(creative_module.creative_engine, "synthesize", synthesize)
    result = asyncio.run(
        manager_module.manager.handle_user_request(
            "Write an article", capability_snapshot=snapshot()
        )
    )

    assert result == "creative answer"
    assert snapshot().snapshot_id in seen["prompt"]


@pytest.mark.asyncio
async def test_prompt_api_passes_the_authoritative_snapshot_to_manager(tmp_path, monkeypatch):
    import api.server as server

    store = CapabilityStore(f"sqlite+aiosqlite:///{tmp_path / 'manager-api.db'}")
    await store.init()
    registry = CapabilityRegistry(store)
    draft = await registry.create_draft(
        "user-1",
        native_manifest(
            supported_tasks=["research"],
            activation={
                "mode": "automatic", "intent_tags": ["research"],
                "explicit_triggers": [], "required_features": [], "priority": 1,
                "conflicts": [],
            },
        ),
    )
    version = await registry.publish("user-1", draft.id)
    await store.install("user-1", version.id)
    await store.set_activation_mode("user-1", ActivationMode.AUTOMATIC, onboarding_accepted=True)
    seen = {}

    async def handle(_prompt, **kwargs):
        seen.update(kwargs)
        return "answer"

    monkeypatch.setattr(server, "get_capability_store", lambda: store)
    monkeypatch.setattr(server.manager, "handle_user_request", handle)
    response = await server.handle_prompt(
        server.PromptRequest(prompt="Research batteries"),
        principal=Principal(subject="user-1", session_id="session-1", claims={}),
    )

    assert response.capability_snapshot == seen["capability_snapshot"].model_dump(mode="json")
    assert response.capability_snapshot["selected"][0]["version_id"] == version.id


@pytest.mark.asyncio
async def test_project_agent_receives_owner_scoped_capability_context(tmp_path, monkeypatch):
    import api.server as server
    from core.agent_loop import AgentLoop

    store = CapabilityStore(f"sqlite+aiosqlite:///{tmp_path / 'agent-api.db'}")
    await store.init()
    registry = CapabilityRegistry(store)
    draft = await registry.create_draft(
        "user-1",
        native_manifest(capability_id="coding-guide", supported_tasks=["coding"]),
    )
    version = await registry.publish("user-1", draft.id)
    await store.install("user-1", version.id)
    await store.set_activation_mode("user-1", ActivationMode.MANUAL_ONLY)
    await store.register_owned_scope("user-1", server.ScopeKind.PROJECT, "project-1")
    seen = {}

    async def fake_run(self, **kwargs):
        del self
        seen.update(kwargs)
        return SimpleNamespace(
            final_response="done", files_created=[], files_edited=[], commands_run=[],
            iterations=1, total_ms=1, workspace=str(tmp_path),
        )

    monkeypatch.setattr(server, "get_capability_store", lambda: store)
    monkeypatch.setattr(AgentLoop, "run", fake_run)
    request = server.AgentRequest(
        task="Implement the API",
        workspace=str(tmp_path / "workspace"),
        project_id="project-1",
        capability_ids=["coding-guide"],
    )
    request._capability_owner_id = "user-1"

    await server._execute_agent(request)

    assert "coding-guide@1.0.0" in seen["capability_context"]
    assert version.content_digest in seen["capability_context"]
