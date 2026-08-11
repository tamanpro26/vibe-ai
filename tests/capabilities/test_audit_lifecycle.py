from __future__ import annotations

from capabilities.worker import ActionWorker
from tests.capabilities.test_action_broker import FakeAdapter, setup_broker


async def test_action_audit_is_complete_append_only_and_redacted(tmp_path):
    store, broker, data = await setup_broker(tmp_path)
    action = await broker.propose("user-1", data)
    await broker.approve("user-1", action.id, action.request_digest)
    await ActionWorker(store, {"github.issue.comment": FakeAdapter()}).run_once("worker")

    events = await store.list_audit_events("user-1")
    assert [item.event_type for item in events] == [
        "action.proposed", "action.approved", "action.succeeded"
    ]
    rendered = str([item.payload for item in events]).lower()
    assert "credential:1" not in rendered
    assert "authorization" not in rendered
    assert all(item.payload_digest.startswith("sha256:") for item in events)

    try:
        await store.update_audit(events[0].id, {"status": "changed"})
    except ValueError as exc:
        assert "append-only" in str(exc)
    else:
        raise AssertionError("audit mutation unexpectedly succeeded")
