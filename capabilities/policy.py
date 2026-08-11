"""Fail-closed policy for approved external actions."""

from __future__ import annotations

from capabilities.actions import ProposedAction
from capabilities.manifests import CapabilityKind, CapabilityManifest, TrustState
from capabilities.models import ReviewState, ScopeKind, ScopeState


class ActionPolicy:
    def validate_shape(self, action: ProposedAction) -> None:
        expected_target = (
            f"repository:{action.arguments.repository_id}:"
            f"issue:{action.arguments.issue_number}:comments"
        )
        if action.shared_data != ["comment_body"]:
            raise ValueError("approval must disclose exactly the comment body")
        if action.mutable_resources != [expected_target]:
            raise ValueError("approval target does not match the immutable repository and issue")

    async def validate_runtime(self, store, owner_id: str, action) -> object:
        proposal = ProposedAction.model_validate(
            {
                "capability_version_id": action.capability_version_id,
                "capability_digest": action.capability_digest,
                "connection_id": action.connection_id,
                "project_id": action.project_id,
                "chat_id": action.chat_id,
                "operation": action.operation,
                "arguments": action.arguments,
                "shared_data": action.shared_data,
                "mutable_resources": action.mutable_resources,
                "idempotency_key": action.idempotency_key,
            }
        )
        self.validate_shape(proposal)
        version = await store.get_version(proposal.capability_version_id)
        if version is None or version.content_digest != proposal.capability_digest:
            raise ValueError("capability version changed or is unavailable")
        if version.archived_at or version.revoked_at or version.review_state is not ReviewState.REVIEWED:
            raise ValueError("capability is archived, revoked, or unreviewed")
        manifest = CapabilityManifest.model_validate(version.manifest)
        if manifest.kind is not CapabilityKind.APPROVED_ACTION:
            raise ValueError("instruction-only capabilities cannot acquire external actions")
        if manifest.trust not in {TrustState.VIBEAI_BUILTIN, TrustState.VIBEAI_REVIEWED}:
            raise ValueError("external actions require VibeAI-reviewed authority")
        declaration = next(
            (item for item in manifest.permissions if item.name == proposal.operation), None
        )
        if declaration is None or declaration.shared_data != ["comment_body"]:
            raise ValueError("capability does not declare this exact external action")
        service = next((item for item in manifest.services if item.provider == "github"), None)
        if service is None or not {"issues:read", "issues:comment"}.issubset(service.operations):
            raise ValueError("capability lacks the required GitHub service declaration")
        if await store.get_active_installation(owner_id, version.id) is None:
            raise ValueError("capability is not installed")
        if proposal.project_id and not await store.owns_scope(
            owner_id, ScopeKind.PROJECT, proposal.project_id
        ):
            raise PermissionError("project scope not found for owner")
        if proposal.chat_id and not await store.owns_scope(
            owner_id, ScopeKind.CHAT, proposal.chat_id
        ):
            raise PermissionError("chat scope not found for owner")
        candidates = await store.list_resolution_candidates(owner_id)
        candidate = next((item for item in candidates if item.version_id == version.id), None)
        if candidate is None or not candidate.installed:
            raise ValueError("capability is not resolver-eligible")
        for scope in candidate.scopes:
            if scope.state is not ScopeState.DISABLED:
                continue
            if scope.scope_kind is ScopeKind.ACCOUNT:
                raise ValueError("capability is disabled for this account")
            if scope.scope_kind is ScopeKind.PROJECT and scope.scope_id == proposal.project_id:
                raise ValueError("capability is disabled for this project")
            if scope.scope_kind is ScopeKind.CHAT and scope.scope_id == proposal.chat_id:
                raise ValueError("capability is disabled for this chat")
        connection = await store.get_service_connection(owner_id, proposal.connection_id)
        if connection.provider != "github":
            raise ValueError("action requires a GitHub connection")
        if not {"issues:read", "issues:comment"}.issubset(connection.allowed_operations):
            raise ValueError("connection lacks required operations")
        repository_target = f"repository:{proposal.arguments.repository_id}"
        if repository_target not in connection.immutable_targets:
            raise ValueError("connection is not bound to the requested repository")
        return connection
