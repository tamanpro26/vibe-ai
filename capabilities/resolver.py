"""Deterministic Capability Hub activation and composition."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from capabilities.manifests import ActivationMode, CapabilityKind, CapabilityManifest
from capabilities.models import ScopeKind, ScopeState

CONTRACT_VERSION = "vibeai.activation/v1"
_FEATURE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("research", re.compile(r"\b(research|sources?|citations?|fact[- ]?check|find evidence)\b", re.I)),
    ("coding", re.compile(r"\b(code|coding|python|javascript|typescript|api|debug|program|script)\b", re.I)),
    ("writing", re.compile(r"\b(write|writing|article|report|essay|copy|draft|rewrite)\b", re.I)),
    ("creative", re.compile(r"\b(design|creative|story|campaign|video|animation|visual)\b", re.I)),
    ("analysis", re.compile(r"\b(analy[sz]e|compare|evaluate|reason|calculate)\b", re.I)),
)


class ScopeInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    scope_kind: ScopeKind
    scope_id: str
    state: ScopeState = ScopeState.INHERIT
    configuration_patch: dict[str, Any] = Field(default_factory=dict)


class CapabilityCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    version_id: str
    installation_id: str | None = None
    manifest: CapabilityManifest
    installed: bool = True
    reviewed: bool = True
    revoked: bool = False
    compatible: bool = True
    scopes: list[ScopeInput] = Field(default_factory=list)


class ResolutionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    owner_id: str
    prompt: str
    activation_mode: ActivationMode = ActivationMode.MANUAL_ONLY
    project_id: str | None = None
    chat_id: str | None = None
    explicit_capability_ids: list[str] = Field(default_factory=list)
    accepted_suggestion_ids: list[str] = Field(default_factory=list)
    rejected_suggestion_ids: list[str] = Field(default_factory=list)


class ResolutionDecision(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    capability_id: str
    version_id: str
    reason: str


class SelectedCapability(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    capability_id: str
    version_id: str
    version: str
    content_digest: str
    reason: str
    scope_provenance: str
    configuration: dict[str, Any] = Field(default_factory=dict)
    instructions: str = ""


class ResolutionSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    snapshot_id: str
    contract_version: str = CONTRACT_VERSION
    owner_id: str
    activation_mode: ActivationMode
    features: tuple[str, ...]
    selected: tuple[SelectedCapability, ...]
    rejected: tuple[ResolutionDecision, ...]
    recommendations: tuple[ResolutionDecision, ...]
    dependency_graph: dict[str, tuple[str, ...]]


class CapabilityResolver:
    def resolve(
        self, request: ResolutionRequest, candidates: list[CapabilityCandidate]
    ) -> ResolutionSnapshot:
        features = extract_features(request.prompt)
        ordered = sorted(
            candidates,
            key=lambda item: (
                -item.manifest.activation.priority,
                item.manifest.capability_id,
                item.manifest.version,
                item.version_id,
            ),
        )
        by_identity = {
            (item.manifest.capability_id, item.manifest.version): item for item in ordered
        }
        selected_candidates: list[tuple[CapabilityCandidate, str, str, dict[str, Any]]] = []
        rejected: list[ResolutionDecision] = []
        recommendations: list[ResolutionDecision] = []

        if request.activation_mode is not ActivationMode.DISABLED:
            for candidate in ordered:
                denied = _base_denial(candidate)
                if denied:
                    rejected.append(_decision(candidate, denied))
                    continue
                enabled, provenance, configuration = _scope_state(request, candidate)
                if not enabled:
                    rejected.append(_decision(candidate, "scope_disabled"))
                    continue
                match_reason = _match_reason(candidate.manifest, request.prompt, features)
                explicit = candidate.manifest.capability_id in {
                    *request.explicit_capability_ids,
                    *request.accepted_suggestion_ids,
                }
                if not candidate.installed:
                    if (
                        request.activation_mode is ActivationMode.AUTOMATIC
                        and match_reason
                        and candidate.manifest.capability_id not in request.rejected_suggestion_ids
                    ):
                        reason = (
                            "install_review_required"
                            if candidate.manifest.capability_id in request.accepted_suggestion_ids
                            else f"suggested:{match_reason}"
                        )
                        recommendations.append(_decision(candidate, reason))
                    continue
                if request.activation_mode is ActivationMode.MANUAL_ONLY:
                    if not explicit:
                        rejected.append(_decision(candidate, "manual_selection_required"))
                        continue
                    reason = "explicit_selection"
                else:
                    if explicit:
                        reason = "explicit_selection"
                    elif candidate.manifest.activation.mode is not ActivationMode.AUTOMATIC:
                        rejected.append(_decision(candidate, "manual_activation_only"))
                        continue
                    elif not match_reason:
                        rejected.append(_decision(candidate, "no_activation_match"))
                        continue
                    else:
                        reason = match_reason
                conflict = next(
                    (
                        selected.manifest.capability_id
                        for selected, *_ in selected_candidates
                        if _conflicts(candidate.manifest, selected.manifest)
                    ),
                    None,
                )
                if conflict:
                    rejected.append(_decision(candidate, f"conflict:{conflict}"))
                    continue
                selected_candidates.append((candidate, reason, provenance, configuration))

        dependency_graph: dict[str, tuple[str, ...]] = {}
        expanded = list(selected_candidates)
        expanded_ids = {item.manifest.capability_id for item, *_ in expanded}
        for candidate, reason, provenance, configuration in list(selected_candidates):
            if candidate.manifest.kind is not CapabilityKind.BUNDLE:
                continue
            member_ids: list[str] = []
            missing_required = None
            for dependency in candidate.manifest.dependencies:
                member = by_identity.get((dependency.capability_id, dependency.version))
                if member is None or not member.installed or _base_denial(member):
                    if dependency.required:
                        missing_required = dependency.capability_id
                        break
                    continue
                member_ids.append(dependency.capability_id)
                if dependency.capability_id not in expanded_ids:
                    member_enabled, member_provenance, member_config = _scope_state(request, member)
                    if member_enabled:
                        expanded.append((member, f"bundle:{candidate.manifest.capability_id}", member_provenance, member_config))
                        expanded_ids.add(dependency.capability_id)
            if missing_required:
                expanded = [item for item in expanded if item[0].version_id != candidate.version_id]
                rejected.append(_decision(candidate, f"missing_required:{missing_required}"))
                continue
            dependency_graph[candidate.manifest.capability_id] = tuple(member_ids)

        selected = tuple(
            SelectedCapability(
                capability_id=candidate.manifest.capability_id,
                version_id=candidate.version_id,
                version=candidate.manifest.version,
                content_digest=candidate.manifest.content_digest,
                reason=reason,
                scope_provenance=provenance,
                configuration=configuration,
                instructions=candidate.manifest.instructions or "",
            )
            for candidate, reason, provenance, configuration in expanded
        )
        snapshot_data = {
            "contract_version": CONTRACT_VERSION,
            "owner_id": request.owner_id,
            "activation_mode": request.activation_mode.value,
            "features": features,
            "selected": [item.model_dump(mode="json") for item in selected],
            "rejected": [item.model_dump(mode="json") for item in rejected],
            "recommendations": [item.model_dump(mode="json") for item in recommendations],
            "dependency_graph": dependency_graph,
        }
        digest = hashlib.sha256(
            json.dumps(snapshot_data, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        return ResolutionSnapshot(
            snapshot_id=f"sha256:{digest}",
            **snapshot_data,
        )


def extract_features(prompt: str) -> tuple[str, ...]:
    return tuple(name for name, pattern in _FEATURE_PATTERNS if pattern.search(prompt))


async def resolve_from_store(store, request: ResolutionRequest) -> ResolutionSnapshot:
    candidates = await store.list_resolution_candidates(request.owner_id)
    snapshot = CapabilityResolver().resolve(request, candidates)
    await store.record_resolution(snapshot, request.project_id, request.chat_id)
    return snapshot


def _base_denial(candidate: CapabilityCandidate) -> str | None:
    if candidate.revoked:
        return "revoked"
    if not candidate.reviewed:
        return "not_reviewed"
    if not candidate.compatible:
        return "incompatible"
    return None


def _scope_state(
    request: ResolutionRequest, candidate: CapabilityCandidate
) -> tuple[bool, str, dict[str, Any]]:
    enabled = True
    provenance = f"account:{request.owner_id}"
    configuration: dict[str, Any] = {}
    targets = (
        (ScopeKind.ACCOUNT, request.owner_id),
        (ScopeKind.PROJECT, request.project_id),
        (ScopeKind.CHAT, request.chat_id),
    )
    for kind, scope_id in targets:
        if not scope_id:
            continue
        matching = sorted(
            (
                item for item in candidate.scopes
                if item.scope_kind is kind and item.scope_id == scope_id
            ),
            key=lambda item: item.scope_id,
        )
        for item in matching:
            configuration.update(item.configuration_patch)
            if item.state is not ScopeState.INHERIT:
                enabled = item.state is ScopeState.ENABLED
                provenance = f"{kind.value}:{scope_id}"
    return enabled, provenance, configuration


def _match_reason(
    manifest: CapabilityManifest, prompt: str, features: tuple[str, ...]
) -> str | None:
    for trigger in sorted(manifest.activation.explicit_triggers):
        if trigger.casefold() in prompt.casefold():
            return f"trigger:{trigger}"
    for tag in sorted(manifest.activation.intent_tags):
        if tag in features:
            return f"intent:{tag}"
    for task in sorted(manifest.supported_tasks):
        if task in features:
            return f"task:{task}"
    return None


def _conflicts(left: CapabilityManifest, right: CapabilityManifest) -> bool:
    return (
        right.capability_id in left.activation.conflicts
        or left.capability_id in right.activation.conflicts
    )


def _decision(candidate: CapabilityCandidate, reason: str) -> ResolutionDecision:
    return ResolutionDecision(
        capability_id=candidate.manifest.capability_id,
        version_id=candidate.version_id,
        reason=reason,
    )
