from __future__ import annotations

import asyncio

import pytest


def _principal(subject="user-1"):
    from api.identity import Principal

    return Principal(subject=subject, session_id="session-1", claims={})


def test_prompt_history_is_forwarded_as_bounded_context(monkeypatch):
    import api.server as server

    seen = {}

    async def fake_handle(prompt, **kwargs):
        seen["prompt"] = prompt
        return "ok"

    monkeypatch.setattr(server.manager, "handle_user_request", fake_handle)
    response = asyncio.run(
        server.handle_prompt(
            server.PromptRequest(
                prompt="What stack did I choose?",
                history=[
                    {"role": "user", "content": "Use React."},
                    {"role": "assistant", "content": "Understood."},
                ],
            )
        )
    )

    assert response.response == "ok"
    assert "User: Use React." in seen["prompt"]
    assert "[Current request]\nWhat stack did I choose?" in seen["prompt"]


def test_web_user_gets_an_isolated_memory_namespace(monkeypatch):
    import api.server as server

    seen = {}

    async def fake_handle(prompt, **kwargs):
        seen.update(kwargs)
        return "ok"

    monkeypatch.setattr(server.manager, "handle_user_request", fake_handle)
    asyncio.run(
        server.handle_prompt(server.PromptRequest(prompt="hello", user_id="user_123"))
    )

    assert seen["memory_namespace"].startswith("web:")
    assert "user_123" not in seen["memory_namespace"]


def test_agent_job_moves_from_running_to_completed(monkeypatch):
    import api.server as server

    async def fake_execute(req):
        assert req.context == "existing files"
        return {"status": "ok", "files": [{"path": "app.js", "content": "done"}]}

    monkeypatch.setattr(server, "_execute_agent", fake_execute)
    server._AGENT_JOBS.clear()
    server._AGENT_JOB_TASKS.clear()
    server._ACTIVE_AGENT_WORKSPACES.clear()

    async def scenario():
        started = await server.start_agent_job(
            server.AgentRequest(task="Improve it", context="existing files"),
            principal=_principal(),
        )
        assert started["status"] == "running"
        while server._AGENT_JOBS[started["job_id"]]["status"] == "running":
            await asyncio.sleep(0)
        return await server.get_agent_job(started["job_id"], principal=_principal())

    completed = asyncio.run(scenario())
    assert completed["status"] == "completed"
    assert completed["result"]["files"][0]["path"] == "app.js"


def test_agent_jobs_reject_work_beyond_backend_capacity(monkeypatch):
    import api.server as server

    gate = asyncio.Event()

    async def slow_execute(_req):
        await gate.wait()
        return {"status": "ok", "files": []}

    monkeypatch.setattr(server, "_execute_agent", slow_execute)
    server._AGENT_JOBS.clear()
    server._AGENT_JOB_TASKS.clear()
    server._ACTIVE_AGENT_WORKSPACES.clear()

    async def scenario():
        await server.start_agent_job(
            server.AgentRequest(task="one", workspace="one"), principal=_principal()
        )
        await server.start_agent_job(
            server.AgentRequest(task="two", workspace="two"), principal=_principal()
        )
        with pytest.raises(server.HTTPException) as exc:
            await server.start_agent_job(
                server.AgentRequest(task="three", workspace="three"), principal=_principal()
            )
        assert exc.value.status_code == 429
        gate.set()
        await asyncio.gather(*list(server._AGENT_JOB_TASKS))

    asyncio.run(scenario())


def test_agent_job_timeout_releases_its_workspace(monkeypatch):
    import api.server as server

    async def stuck_execute(_req):
        await asyncio.Event().wait()

    monkeypatch.setattr(server, "_execute_agent", stuck_execute)
    monkeypatch.setattr(server, "_AGENT_JOB_TIMEOUT_SECONDS", 0.001)
    server._AGENT_JOBS.clear()
    server._AGENT_JOB_TASKS.clear()
    server._ACTIVE_AGENT_WORKSPACES.clear()

    async def scenario():
        started = await server.start_agent_job(
            server.AgentRequest(task="hang", workspace="timed-workspace"),
            principal=_principal(),
        )
        await asyncio.gather(*list(server._AGENT_JOB_TASKS))
        return await server.get_agent_job(started["job_id"], principal=_principal())

    failed = asyncio.run(scenario())
    assert failed["status"] == "failed"
    assert failed["error"] == "build timed out after 20 minutes"
    assert "timed-workspace" not in server._ACTIVE_AGENT_WORKSPACES


def test_agent_job_id_cannot_be_copied_across_users(monkeypatch):
    import api.server as server

    async def fake_execute(_req):
        return {"status": "ok", "files": []}

    monkeypatch.setattr(server, "_execute_agent", fake_execute)
    server._AGENT_JOBS.clear()
    server._AGENT_JOB_TASKS.clear()
    server._ACTIVE_AGENT_WORKSPACES.clear()

    async def scenario():
        started = await server.start_agent_job(
            server.AgentRequest(task="owned"), principal=_principal("user-1")
        )
        await asyncio.gather(*list(server._AGENT_JOB_TASKS))
        with pytest.raises(server.HTTPException) as exc:
            await server.get_agent_job(started["job_id"], principal=_principal("user-2"))
        return exc.value.status_code

    assert asyncio.run(scenario()) == 404
