from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import update

from capabilities.models import CapabilityActionRequest
from capabilities.worker import ActionWorker, SimulatedProcessDeath
from tests.capabilities.test_action_broker import FakeAdapter, setup_broker


class CrashBeforeMarker(FakeAdapter):
    async def preflight(self, _action, _connection):
        raise SimulatedProcessDeath("before marker")


class CrashAfterMarker(FakeAdapter):
    async def execute(self, _action, _connection):
        self.execute_calls += 1
        raise SimulatedProcessDeath("ambiguous provider outcome")


class ProviderTimeoutAfterMarker(FakeAdapter):
    async def execute(self, _action, _connection):
        self.execute_calls += 1
        raise TimeoutError("provider response was lost")


@pytest.mark.asyncio
async def test_pre_marker_crash_is_reclaimable_without_duplicate_effect(tmp_path):
    store, broker, data = await setup_broker(tmp_path)
    action = await broker.propose("user-1", data)
    await broker.approve("user-1", action.id, action.request_digest)
    with pytest.raises(SimulatedProcessDeath):
        await ActionWorker(store, {"github.issue.comment": CrashBeforeMarker()}).run_once("dead")

    await store.recover_stale_actions(datetime.now(timezone.utc) + timedelta(minutes=10))
    safe = FakeAdapter()
    await ActionWorker(store, {"github.issue.comment": safe}).run_once("replacement")
    assert (await broker.get("user-1", action.id)).status == "succeeded"
    assert safe.execute_calls == 1


@pytest.mark.asyncio
async def test_post_marker_crash_becomes_unknown_and_is_never_retried(tmp_path):
    store, broker, data = await setup_broker(tmp_path)
    action = await broker.propose("user-1", data)
    await broker.approve("user-1", action.id, action.request_digest)
    crashing = CrashAfterMarker()
    with pytest.raises(SimulatedProcessDeath):
        await ActionWorker(store, {"github.issue.comment": crashing}).run_once("dead")

    await store.recover_stale_actions(datetime.now(timezone.utc) + timedelta(minutes=10))
    safe = FakeAdapter()
    assert await ActionWorker(store, {"github.issue.comment": safe}).run_once("replacement") is None
    assert (await broker.get("user-1", action.id)).status == "outcome_unknown"
    assert crashing.execute_calls == 1 and safe.execute_calls == 0


@pytest.mark.asyncio
async def test_post_marker_provider_error_is_unknown_and_is_never_retried(tmp_path):
    store, broker, data = await setup_broker(tmp_path)
    action = await broker.propose("user-1", data)
    await broker.approve("user-1", action.id, action.request_digest)
    timing_out = ProviderTimeoutAfterMarker()

    completed = await ActionWorker(
        store, {"github.issue.comment": timing_out}
    ).run_once("worker")

    assert completed.status == "outcome_unknown"
    assert timing_out.execute_calls == 1
    assert await ActionWorker(
        store, {"github.issue.comment": FakeAdapter()}
    ).run_once("replacement") is None


@pytest.mark.asyncio
async def test_approved_action_that_expires_during_worker_outage_never_executes(tmp_path):
    store, broker, data = await setup_broker(tmp_path)
    action = await broker.propose("user-1", data)
    await broker.approve("user-1", action.id, action.request_digest)
    async with store._sessions.begin() as session:
        await session.execute(
            update(CapabilityActionRequest)
            .where(CapabilityActionRequest.id == action.id)
            .values(expires_at=datetime.now(timezone.utc) - timedelta(seconds=1))
        )
    adapter = FakeAdapter()

    assert await ActionWorker(
        store, {"github.issue.comment": adapter}
    ).run_once("late-worker") is None
    assert (await broker.get("user-1", action.id)).status == "expired"
    assert adapter.preflight_calls == adapter.execute_calls == 0
