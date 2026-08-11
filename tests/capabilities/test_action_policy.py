from __future__ import annotations

import pytest
from pydantic import ValidationError

from capabilities.actions import GitHubIssueComment, ProposedAction
from capabilities.policy import ActionPolicy


def proposal(**changes) -> ProposedAction:
    values = {
        "capability_version_id": "version-1",
        "capability_digest": "sha256:" + "a" * 64,
        "connection_id": "connection-1",
        "project_id": "project-1",
        "chat_id": "chat-1",
        "operation": "github.issue.comment",
        "arguments": {
            "repository_id": "456",
            "issue_number": 12,
            "body": "Verified update",
        },
        "shared_data": ["comment_body"],
        "mutable_resources": ["repository:456:issue:12:comments"],
        "idempotency_key": "comment-1",
    }
    values.update(changes)
    return ProposedAction.model_validate(values)


def test_only_typed_append_only_github_comment_is_allowed():
    action = proposal()
    ActionPolicy().validate_shape(action)
    assert isinstance(action.arguments, GitHubIssueComment)

    with pytest.raises(ValidationError):
        proposal(operation="github.issue.update")
    with pytest.raises(ValidationError):
        proposal(arguments={"repository_id": "owner/repo", "issue_number": 1, "body": "x"})
    with pytest.raises(ValidationError):
        proposal(arguments={"repository_id": "456", "issue_number": 1, "body": "${TOKEN}"})


def test_policy_requires_exact_disclosure_and_target():
    with pytest.raises(ValueError):
        ActionPolicy().validate_shape(proposal(shared_data=[]))
    with pytest.raises(ValueError):
        ActionPolicy().validate_shape(
            proposal(mutable_resources=["repository:999:issue:12:comments"])
        )
