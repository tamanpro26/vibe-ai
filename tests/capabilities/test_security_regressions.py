from __future__ import annotations

import inspect

import pytest

from capabilities.actions import ProposedAction
from capabilities.integrations.github import GITHUB_API, GITHUB_API_VERSION
from capabilities.policy import ActionPolicy
from capabilities.worker import ActionWorker
from tests.capabilities.test_action_policy import proposal


def test_capability_runtime_has_no_generic_shell_http_or_github_tool_path():
    source = inspect.getsource(ActionWorker)
    assert "ToolExecutor" not in source
    assert "github_tool" not in source
    assert "subprocess" not in source
    assert "httpx" not in source


def test_action_destination_is_numeric_pinned_and_exact():
    assert GITHUB_API == "https://api.github.com"
    assert GITHUB_API_VERSION == "2026-03-10"
    with pytest.raises(ValueError):
        ActionPolicy().validate_shape(
            proposal(mutable_resources=["repository:456:issue:13:comments"])
        )
    with pytest.raises(ValueError):
        ProposedAction.model_validate({
            **proposal().model_dump(mode="json"),
            "arguments": {"repository_id": "owner/repo", "issue_number": 12, "body": "x"},
        })


def test_external_actions_are_release_gated_off_by_default(monkeypatch):
    monkeypatch.delenv("CAPABILITY_ACTIONS_ENABLED", raising=False)
    from config.settings import Settings

    assert Settings(_env_file=None).capability_actions_enabled is False
