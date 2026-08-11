from __future__ import annotations

from capabilities.manifests import CapabilityManifest
from capabilities.models import ActivationMode, ScopeKind, ScopeState
from capabilities.resolver import (
    CapabilityCandidate,
    CapabilityResolver,
    ResolutionRequest,
    ScopeInput,
    resolve_from_store,
)
from capabilities.registry import CapabilityRegistry
from capabilities.store import CapabilityStore
from tests.capabilities.test_manifests import native_manifest


def candidate(
    capability_id: str,
    *,
    version_id: str | None = None,
    tasks: list[str] | None = None,
    priority: int = 0,
    conflicts: list[str] | None = None,
    installed: bool = True,
    scopes: list[ScopeInput] | None = None,
    kind: str = "instruction_skill",
    dependencies: list[dict] | None = None,
) -> CapabilityCandidate:
    manifest = native_manifest(
        capability_id=capability_id,
        name=capability_id.replace("-", " ").title(),
        version="1.0.0",
        kind=kind,
        instructions=None if kind == "bundle" else f"Instructions for {capability_id}.",
        supported_tasks=tasks or [],
        activation={
            "mode": "automatic",
            "intent_tags": tasks or [],
            "explicit_triggers": [],
            "required_features": [],
            "priority": priority,
            "conflicts": conflicts or [],
        },
        dependencies=dependencies or [],
    )
    return CapabilityCandidate(
        version_id=version_id or f"version-{capability_id}",
        installation_id=f"install-{capability_id}" if installed else None,
        manifest=CapabilityManifest.model_validate(manifest),
        installed=installed,
        scopes=scopes or [],
    )


def test_scope_precedence_uses_most_specific_non_inherit_value():
    item = candidate(
        "research-analyst",
        tasks=["research"],
        scopes=[
            ScopeInput(scope_kind=ScopeKind.ACCOUNT, scope_id="user-1", state=ScopeState.DISABLED),
            ScopeInput(scope_kind=ScopeKind.PROJECT, scope_id="project-1", state=ScopeState.ENABLED),
            ScopeInput(scope_kind=ScopeKind.CHAT, scope_id="chat-1", state=ScopeState.INHERIT),
        ],
    )
    snapshot = CapabilityResolver().resolve(
        ResolutionRequest(
            owner_id="user-1",
            prompt="Research current battery technology",
            activation_mode=ActivationMode.AUTOMATIC,
            project_id="project-1",
            chat_id="chat-1",
        ),
        [item],
    )
    assert [selected.capability_id for selected in snapshot.selected] == ["research-analyst"]
    assert snapshot.selected[0].scope_provenance == "project:project-1"


def test_activation_modes_are_deterministic_and_do_not_auto_install():
    installed = candidate("research-analyst", tasks=["research"])
    suggestion = candidate("citation-export", tasks=["research"], installed=False)
    resolver = CapabilityResolver()

    automatic = resolver.resolve(
        ResolutionRequest(owner_id="user-1", prompt="Research this", activation_mode="automatic"),
        [installed, suggestion],
    )
    manual = resolver.resolve(
        ResolutionRequest(
            owner_id="user-1", prompt="Research this", activation_mode="manual_only",
            explicit_capability_ids=["research-analyst"],
        ),
        [installed, suggestion],
    )
    disabled = resolver.resolve(
        ResolutionRequest(owner_id="user-1", prompt="Research this", activation_mode="disabled"),
        [installed, suggestion],
    )

    assert [item.capability_id for item in automatic.selected] == ["research-analyst"]
    assert [item.capability_id for item in automatic.recommendations] == ["citation-export"]
    assert [item.capability_id for item in manual.selected] == ["research-analyst"]
    assert disabled.selected == () and disabled.recommendations == ()


def test_priority_conflicts_and_ties_have_stable_explanations():
    candidates = [
        candidate("zeta", tasks=["coding"], priority=5, conflicts=["alpha"]),
        candidate("alpha", tasks=["coding"], priority=5, conflicts=["zeta"]),
        candidate("beta", tasks=["coding"], priority=10),
    ]
    request = ResolutionRequest(
        owner_id="user-1", prompt="Write Python code", activation_mode="automatic"
    )
    resolver = CapabilityResolver()
    first = resolver.resolve(request, candidates)
    second = resolver.resolve(request, list(reversed(candidates)))

    assert first.snapshot_id == second.snapshot_id
    assert [item.capability_id for item in first.selected] == ["beta", "alpha"]
    assert any(item.capability_id == "zeta" and item.reason == "conflict:alpha" for item in first.rejected)


def test_bundle_expansion_is_pinned_and_deduplicated():
    research = candidate("research-analyst", tasks=["research"])
    writing = candidate("content-studio", tasks=["writing"])
    bundle = candidate(
        "research-to-creation",
        tasks=["research", "writing"],
        kind="bundle",
        dependencies=[
            {"capability_id": "research-analyst", "version": "1.0.0", "required": True},
            {"capability_id": "content-studio", "version": "1.0.0", "required": True},
        ],
    )
    snapshot = CapabilityResolver().resolve(
        ResolutionRequest(
            owner_id="user-1", prompt="Research and write a report", activation_mode="automatic"
        ),
        [bundle, research, writing],
    )

    selected = [item.capability_id for item in snapshot.selected]
    assert selected.count("research-analyst") == 1
    assert selected.count("content-studio") == 1
    assert snapshot.dependency_graph["research-to-creation"] == (
        "research-analyst", "content-studio"
    )


def test_unreviewed_revoked_or_incompatible_candidates_are_explained():
    values = [
        candidate("unreviewed", tasks=["research"]).model_copy(update={"reviewed": False}),
        candidate("revoked", tasks=["research"]).model_copy(update={"revoked": True}),
        candidate("incompatible", tasks=["research"]).model_copy(update={"compatible": False}),
    ]
    snapshot = CapabilityResolver().resolve(
        ResolutionRequest(owner_id="user-1", prompt="Research", activation_mode="automatic"),
        values,
    )
    assert snapshot.selected == ()
    assert {item.reason for item in snapshot.rejected} == {
        "not_reviewed", "revoked", "incompatible"
    }


def test_suggestion_accept_and_reject_are_request_scoped_without_auto_install():
    installed = candidate("research-analyst", tasks=["research"])
    uninstalled = candidate("citation-export", tasks=["research"], installed=False)
    request = ResolutionRequest(
        owner_id="user-1",
        prompt="Research this",
        activation_mode="automatic",
        accepted_suggestion_ids=["citation-export"],
        rejected_suggestion_ids=["research-analyst"],
    )
    snapshot = CapabilityResolver().resolve(request, [installed, uninstalled])

    assert "research-analyst" in [item.capability_id for item in snapshot.selected]
    assert snapshot.recommendations[0].reason == "install_review_required"
    assert "citation-export" not in [item.capability_id for item in snapshot.selected]


async def test_store_preview_and_runtime_use_identical_snapshot(tmp_path):
    store = CapabilityStore(f"sqlite+aiosqlite:///{tmp_path / 'resolver.db'}")
    await store.init()
    registry = CapabilityRegistry(store)
    draft = await registry.create_draft(
        "user-1", native_manifest(supported_tasks=["research"], activation={
            "mode": "automatic", "intent_tags": ["research"], "explicit_triggers": [],
            "required_features": [], "priority": 10, "conflicts": [],
        })
    )
    version = await registry.publish("user-1", draft.id)
    await store.install("user-1", version.id)
    await store.set_activation_mode("user-1", ActivationMode.AUTOMATIC, onboarding_accepted=True)
    request = ResolutionRequest(
        owner_id="user-1", prompt="Research this topic", activation_mode="automatic"
    )

    preview = await resolve_from_store(store, request)
    runtime = await resolve_from_store(store, request)
    assert preview == runtime
    assert preview.selected[0].version_id == version.id
