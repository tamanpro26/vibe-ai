from __future__ import annotations

import pytest

from capabilities.worker import ActionWorker
from tests.capabilities.test_action_broker import FakeAdapter, setup_broker


@pytest.mark.asyncio
async def test_action_checkpoint_suspends_and_becomes_resume_ready(tmp_path):
    store, broker, data = await setup_broker(tmp_path)
    action = await broker.propose("user-1", data, workflow_id="workflow-1")
    checkpoint = await store.get_workflow_checkpoint("user-1", "workflow-1")
    assert checkpoint.status == "awaiting_confirmation"

    await broker.approve("user-1", action.id, action.request_digest)
    await ActionWorker(store, {"github.issue.comment": FakeAdapter()}).run_once("worker")
    resumed = await store.get_workflow_checkpoint("user-1", "workflow-1")
    assert resumed.status == "resume_ready"
    assert "credential" not in str(resumed.state).lower()
