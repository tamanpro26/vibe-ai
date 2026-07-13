"""
vibemind/orchestrator.py
Ties brain.py (planning + execution) to db.py (persistence) into the single
entry point the API layer calls: handle a user message in a conversation,
decide whether it needs real action or just a reply, execute it, and record
everything (message, task, action logs, agent status) along the way.
"""
from __future__ import annotations

from pathlib import Path

from loguru import logger

from vibemind.brain import plan_task, dispatch_step, chat_brain, PlannedStep
from vibemind.db import db
from vibemind.fastpath import try_fast_path, try_ai_intent

# A dedicated workspace, NOT "." -- verified live (2026-07-10) that "." meant
# the server's cwd, which is the VibeAI repo root itself: a voice command
# ("create greetings.py") ran VibeAI's coding agent against the live VibeAI
# codebase instead of creating the file anywhere real. File/code steps need
# their own sandboxed directory, same reasoning as ~/.vibemind/vibemind.db.
_DEFAULT_WORKSPACE = Path.home() / "VibeMind" / "workspace"
_DEFAULT_WORKSPACE.mkdir(parents=True, exist_ok=True)


async def handle_message(conversation_id: int, message: str) -> dict:
    """Process one user message end-to-end. Returns
    {"reply": str, "task_id": int | None, "steps": [...]}"""
    await db.add_message(conversation_id, "user", message)

    # Fast path FIRST: unambiguous single-action commands (launch an app,
    # play a song) skip the LLM planner and tool-calling loop entirely --
    # see vibemind/fastpath.py for why this exists (measured ~13s for a bare
    # "open notepad" through the full pipeline; this brings it under ~1-2s).
    # Tier 1 is zero-model regex matching; anything it misses gets ONE shot
    # at tier 2 (a fast local-model classification, still well under the
    # full pipeline's cost) before falling all the way through to the full
    # planner. Regex missing a phrasing is expected and fine when there's a
    # genuinely-AI-driven fallback behind it -- the bad experience was regex
    # missing and THEN dropping straight to the slow, sometimes-wrong full
    # pipeline with no smarter step in between.
    fp = await try_fast_path(message)
    if not fp.handled:
        fp = await try_ai_intent(message)
    if fp.handled:
        task_id = await db.create_task(title=message[:120], description=message,
                                        conversation_id=conversation_id)
        await db.upsert_agent_status("app", status="busy", current_task=fp.action)
        action_id = await db.create_action_log(fp.action, message, agent="app",
                                                task_id=task_id, conversation_id=conversation_id)
        await db.update_action_log_status(action_id, "completed" if fp.ok else "failed")
        await db.upsert_agent_status(
            "app", status="idle", current_task=None,
            tasks_completed=(await _agent_completed_count("app")) + 1,
        )
        await db.update_task(task_id, status="completed" if fp.ok else "failed",
                              result=fp.reply, progress=100)
        await db.add_message(conversation_id, "assistant", fp.reply, agent_label="app",
                              metadata={"task_id": task_id, "fast_path": True})
        await db.touch_conversation(conversation_id)
        step = {"agent": "app", "action": fp.action, "detail": fp.reply,
                "ok": fp.ok, "sub_actions": fp.sub_actions}
        return {"reply": fp.reply, "task_id": task_id, "steps": [step]}

    steps = await plan_task(message)

    # A single "brain"-agent step with no other steps is just conversation --
    # no task record needed, keeps the tasks table meaningful (real multi-step
    # work only) rather than logging every hello as a "task".
    if len(steps) == 1 and steps[0].agent == "brain":
        reply = await chat_brain(message)
        await db.add_message(conversation_id, "assistant", reply, agent_label="brain")
        await db.touch_conversation(conversation_id)
        return {"reply": reply, "task_id": None, "steps": []}

    task_id = await db.create_task(
        title=message[:120], description=message, conversation_id=conversation_id,
    )
    await db.update_task(task_id, status="planning", planned_steps=_steps_json(steps))

    results = []
    await db.update_task(task_id, status="in_progress")
    total = len(steps)
    file_code_already_run = False
    for i, step in enumerate(steps):
        await db.upsert_agent_status(step.agent, status="busy", current_task=step.action)
        action_id = await db.create_action_log(
            step.action, step.details, agent=step.agent,
            task_id=task_id, conversation_id=conversation_id,
        )
        await db.update_action_log_status(action_id, "executing")
        # Gemma 4 sometimes splits one CODE-authoring task into several steps
        # ("write the code", "save the file") -- each is a full run_agent()
        # call with the ORIGINAL command (see dispatch_step's docstring), so
        # running a second one would just redo the first one's work. Skip it.
        # Only applies to "code" now: "file" runs on the desktop agent, whose
        # steps are genuinely distinct actions (list, then open, etc.).
        if step.agent == "code" and file_code_already_run:
            outcome = {"agent": step.agent, "action": step.action,
                       "detail": "(already handled by the earlier code step)", "ok": True}
            await db.update_action_log_status(action_id, "completed")
            results.append(outcome)
            await db.upsert_agent_status(step.agent, status="idle", current_task=None)
            await db.update_task(task_id, progress=int(((i + 1) / total) * 100))
            continue
        try:
            outcome = await dispatch_step(step, workspace=str(_DEFAULT_WORKSPACE), full_command=message)
            await db.update_action_log_status(action_id, "completed" if outcome["ok"] else "failed")
            if step.agent == "code":
                file_code_already_run = True
        except Exception as exc:
            logger.warning(f"[vibemind] step {step.step} ({step.agent}) failed: {str(exc)[:120]}")
            outcome = {"agent": step.agent, "action": step.action, "detail": f"ERROR: {exc}", "ok": False}
            await db.update_action_log_status(action_id, "failed")
        results.append(outcome)
        await db.upsert_agent_status(
            step.agent, status="idle", current_task=None,
            tasks_completed=(await _agent_completed_count(step.agent)) + 1,
        )
        await db.update_task(task_id, progress=int(((i + 1) / total) * 100))

    all_ok = all(r["ok"] for r in results)
    summary = "\n".join(f"- {r['action']}: {r['detail']}" for r in results)
    await db.update_task(
        task_id, status="completed" if all_ok else "failed",
        result=summary, progress=100,
    )
    reply = summary or "Done."
    await db.add_message(conversation_id, "assistant", reply, agent_label="brain",
                          metadata={"task_id": task_id})
    await db.touch_conversation(conversation_id)
    return {"reply": reply, "task_id": task_id, "steps": results}


async def _agent_completed_count(agent_name: str) -> int:
    for row in await db.get_agent_statuses():
        if row["agent_name"] == agent_name:
            return row["tasks_completed"] or 0
    return 0


def _steps_json(steps: list[PlannedStep]) -> str:
    import json
    return json.dumps([s.__dict__ for s in steps])
