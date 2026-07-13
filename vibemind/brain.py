"""
vibemind/brain.py
The brain plans; sub-agents (Ollama locals, falling back to the brain model
if Ollama isn't reachable) execute. Reuses VibeAI's own proven infrastructure
rather than reinventing it:
  - models/registry.py for all model access.
  - core/agent_loop.py's run_agent() for file/code tasks -- that's exactly
    what it already does well (create/edit files, run bash, verify builds).
  - vibemind/automation.py (NEW) for the two things nothing in this repo did
    before: real app launching and real keystroke/window control.

Only the desktop/web tool-calling loop below is genuinely new machinery --
kept deliberately small (a handful of short actions, not a 20-iteration
coding session) rather than bolted onto agent_loop.py's coding-specific
machinery (build gates, repo maps, golden scaffolds -- none of which apply
to "open notepad and type this").

BRAIN_MODEL was gemma_4 (google/gemma-4-31b-it:free via OpenRouter) until
2026-07-13: found live, while hardening the Free Manager Council (which
also used this exact model_id as its Synthesizer), that it fails 100% of
calls -- OpenRouter's free-tier routing for it 429s on one backend then
404s on its own fallback backend, a real live outage on OpenRouter's side,
not a wrong slug (verified against OpenRouter's current catalog). Every
plan_task/chat_brain call was paying a guaranteed-fail round trip before
generate_resilient() fell through to a substitute -- and run_desktop_agent
below calls registry.get(BRAIN_MODEL) directly with NO resilient wrapper,
so with Ollama unreachable it would have failed outright with no fallback
at all. Swapped to glm_47_flash_zai (Z.AI), already verified working in
production (see config/models_config.py and DECISIONS.md).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from loguru import logger

from models.registry import registry
from models.connectors.ollama import is_reachable as ollama_reachable

BRAIN_MODEL = "glm_47_flash_zai"
LOCAL_AGENT_MODEL = "qwen25_3b_ollama"

AgentName = str   # "app", "web", "file", "code", "brain"

_PLANNER_SYSTEM = """You are the planning brain of VibeMind, a JARVIS-style desktop assistant.
Break the user's command into an ordered list of concrete steps. Each step is
handled by exactly one agent:
  - "app"   : ANYTHING involving the real PC -- launching/controlling apps,
              typing into windows, pressing keys, AND browsing folders, opening
              files, reading/writing/searching files anywhere on disk, opening
              a folder in Explorer. Use this for "open my Downloads",
              "list files in X", "open report.pdf", "launch Spotify".
  - "web"   : opening URLs, web search
  - "code"  : writing, running, or testing NEW code/programs in a project
              (e.g. "write a Python script that...", "build a small app").
  - "brain" : the step is just a conversational reply, no action needed

Prefer "app" for interacting with files/folders/programs that already exist on
the machine; use "code" only when the user wants you to author a program.

Return ONLY a JSON object: {"steps": [{"step": 1, "action": "...", "agent": "...", "details": "..."}]}
"action" is a short human-readable label. "details" is the exact instruction
for that agent to execute (specific enough to act on without more context).
"""

_DESKTOP_SYSTEM = """You are the desktop agent for VibeMind. You control the user's real PC
via tools -- every tool call you make actually happens on their machine. You can:
  - launch apps (open_installed_app for named programs like Spotify/Blender,
    launch_app for basics like notepad/cmd), open URLs, focus/close windows,
    type text and press keys;
  - browse the filesystem (list_directory), read/write files anywhere
    (read_file/write_file), open files or folders with their default app
    (open_path), and search for files (search_files).
Work step by step: launch or focus the right window BEFORE typing into it,
wait_for_window after launching something that takes a moment to appear, and
finish with a short text summary once the task is done. Do not call more tools
once the task is complete."""

_DESKTOP_TOOL_SCHEMAS: list[dict] = [
    {"type": "function", "function": {
        "name": "launch_app", "description": "Launch a desktop application by common name (e.g. 'vscode', 'notepad', 'chrome', 'terminal') or executable name.",
        "parameters": {"type": "object", "properties": {
            "name": {"type": "string", "description": "App name or executable"}},
            "required": ["name"]},
    }},
    {"type": "function", "function": {
        "name": "open_url", "description": "Open a URL in the default web browser.",
        "parameters": {"type": "object", "properties": {
            "url": {"type": "string"}}, "required": ["url"]},
    }},
    {"type": "function", "function": {
        "name": "wait_for_window", "description": "Wait until a window whose title contains the given text appears (poll up to timeout seconds). Call this right after launch_app for anything that takes a moment to open.",
        "parameters": {"type": "object", "properties": {
            "title_substr": {"type": "string"},
            "timeout": {"type": "number", "description": "seconds, default 10"}},
            "required": ["title_substr"]},
    }},
    {"type": "function", "function": {
        "name": "focus_window", "description": "Bring a window whose title contains the given text to the foreground so keystrokes reach it.",
        "parameters": {"type": "object", "properties": {
            "title_substr": {"type": "string"}}, "required": ["title_substr"]},
    }},
    {"type": "function", "function": {
        "name": "type_text", "description": "Type text into whichever window currently has focus.",
        "parameters": {"type": "object", "properties": {
            "text": {"type": "string"}}, "required": ["text"]},
    }},
    {"type": "function", "function": {
        "name": "press_enter", "description": "Press the Enter key.",
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "press_hotkey", "description": "Press a keyboard shortcut, e.g. ['ctrl','s'] for Ctrl+S.",
        "parameters": {"type": "object", "properties": {
            "keys": {"type": "array", "items": {"type": "string"}}}, "required": ["keys"]},
    }},
    {"type": "function", "function": {
        "name": "list_windows", "description": "List titles of all currently open windows.",
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "close_app", "description": "Close a window whose title contains the given text.",
        "parameters": {"type": "object", "properties": {
            "title_substr": {"type": "string"}}, "required": ["title_substr"]},
    }},
    # ── PC storage + installed-apps reach ────────────────────────────────
    {"type": "function", "function": {
        "name": "open_installed_app", "description": "Launch any installed application by its display name (e.g. 'Spotify', 'Blender', 'Visual Studio Code'). Matches against the machine's Start Menu, so it works for apps launch_app doesn't know. Use this for named programs; use launch_app only for basic ones like notepad/cmd.",
        "parameters": {"type": "object", "properties": {
            "name": {"type": "string", "description": "App display name, full or partial"}},
            "required": ["name"]},
    }},
    {"type": "function", "function": {
        "name": "play_on_spotify", "description": "Open Spotify and search for a song/artist/album so the user can play it -- use whenever the user wants to play music. Opens directly to the search results (fast, no typing needed); the user presses play on the result themselves.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "song/artist/album to search for"}},
            "required": ["query"]},
    }},
    {"type": "function", "function": {
        "name": "list_installed_apps", "description": "List the applications installed on this PC (name + launch path). Use to discover what's available before launching.",
        "parameters": {"type": "object", "properties": {}},
    }},
    {"type": "function", "function": {
        "name": "list_directory", "description": "List the files and folders in a directory. Omit path to start at the user's home folder.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string", "description": "Absolute folder path; optional"}}},
    }},
    {"type": "function", "function": {
        "name": "read_file", "description": "Read the text content of a file anywhere on disk (size-capped).",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"}}, "required": ["path"]},
    }},
    {"type": "function", "function": {
        "name": "write_file", "description": "Create or overwrite a text file anywhere on disk (parent folders auto-created).",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"}, "content": {"type": "string"}},
            "required": ["path", "content"]},
    }},
    {"type": "function", "function": {
        "name": "open_path", "description": "Open a file or folder with its default application (double-click equivalent). E.g. open a PDF, image, or a folder in Explorer.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"}}, "required": ["path"]},
    }},
    {"type": "function", "function": {
        "name": "search_files", "description": "Search for files by name (case-insensitive substring) under a root folder.",
        "parameters": {"type": "object", "properties": {
            "root": {"type": "string"}, "query": {"type": "string"},
            "limit": {"type": "integer", "description": "max results, default 50"}},
            "required": ["root", "query"]},
    }},
]


def _match_installed_app(name: str) -> tuple[str, str] | None:
    """Resolve a display name to (name, launch_path) against installed apps.
    Exact-ish match first, then substring, so 'code' finds 'Visual Studio
    Code' and 'spotify' finds 'Spotify'."""
    from vibemind import system
    apps = system.list_installed_apps()
    low = name.lower()
    exact = [a for a in apps if a["name"].lower() == low]
    if exact:
        return exact[0]["name"], exact[0]["path"]
    starts = [a for a in apps if a["name"].lower().startswith(low)]
    if starts:
        return starts[0]["name"], starts[0]["path"]
    contains = [a for a in apps if low in a["name"].lower()]
    if contains:
        return contains[0]["name"], contains[0]["path"]
    return None


async def _execute_desktop_tool(name: str, args: dict) -> str:
    import vibemind.automation as auto
    try:
        if name == "launch_app":
            r = auto.launch_app(args["name"])
        elif name == "play_on_spotify":
            from vibemind.spotify import play_on_spotify
            r = await play_on_spotify(args["query"])
        elif name == "open_url":
            r = auto.open_url(args["url"])
        elif name == "wait_for_window":
            title = auto.wait_for_window(args["title_substr"], timeout=float(args.get("timeout", 10)))
            r = auto.ActionResult(bool(title), title or f"no window matching '{args['title_substr']}' appeared in time")
        elif name == "focus_window":
            r = auto.focus_window(args["title_substr"])
        elif name == "type_text":
            r = auto.type_text(args["text"])
        elif name == "press_enter":
            r = auto.press_enter()
        elif name == "press_hotkey":
            r = auto.press_hotkey(*args.get("keys", []))
        elif name == "list_windows":
            return json.dumps(auto.list_windows())
        elif name == "close_app":
            r = auto.close_app(args["title_substr"])
        elif name in ("open_installed_app", "list_installed_apps", "list_directory",
                      "read_file", "write_file", "open_path", "search_files"):
            return _execute_system_tool(name, args)
        else:
            return f"ERROR: unknown tool '{name}'"
    except Exception as exc:
        return f"ERROR: {exc}"
    return r.detail if r.ok else f"ERROR: {r.detail}"


def _execute_system_tool(name: str, args: dict) -> str:
    """Filesystem + installed-app tools (vibemind/system.py). Kept separate
    from the automation tools above since they return richer JSON, not just
    an ActionResult detail string."""
    from vibemind import system
    if name == "open_installed_app":
        match = _match_installed_app(args["name"])
        if not match:
            return f"ERROR: no installed app matching '{args['name']}'"
        app_name, launch_path = match
        result = system.open_path(launch_path)
        return f"launched {app_name}" if result.get("ok") else f"ERROR: {result.get('error')}"
    if name == "list_installed_apps":
        apps = system.list_installed_apps()
        return json.dumps([a["name"] for a in apps])
    if name == "list_directory":
        listing = system.list_directory(args.get("path"))
        if listing.get("error"):
            return f"ERROR: {listing['error']}"
        names = [("[dir] " if e["is_dir"] else "") + e["name"] for e in listing["entries"][:100]]
        return json.dumps({"path": listing["path"], "entries": names})
    if name == "read_file":
        res = system.read_text_file(args["path"])
        return f"ERROR: {res['error']}" if res.get("error") else res["content"]
    if name == "write_file":
        res = system.write_text_file(args["path"], args["content"])
        return f"wrote {res.get('bytes', 0)} bytes to {res['path']}" if res.get("ok") else f"ERROR: {res.get('error')}"
    if name == "open_path":
        res = system.open_path(args["path"])
        return f"opened {res['path']}" if res.get("ok") else f"ERROR: {res.get('error')}"
    if name == "search_files":
        res = system.search_files(args["root"], args["query"], int(args.get("limit", 50)))
        if res.get("error"):
            return f"ERROR: {res['error']}"
        return json.dumps([m["path"] for m in res["matches"]])
    return f"ERROR: unknown system tool '{name}'"


@dataclass
class DesktopAction:
    name:   str
    args:   dict
    result: str
    ok:     bool


@dataclass
class DesktopAgentResult:
    actions:  list[DesktopAction] = field(default_factory=list)
    final:    str = ""
    model_id: str = ""


async def run_desktop_agent(task: str, max_iterations: int = 8) -> DesktopAgentResult:
    """Short tool-calling loop for app/web tasks. Prefers the local Ollama
    model (per the user's spec: 'ollama local models as the other agents');
    falls back to the brain model if Ollama isn't running, mirroring the
    same graceful-degradation pattern teams/router_team.py already uses."""
    model_id = LOCAL_AGENT_MODEL if await ollama_reachable() else BRAIN_MODEL
    connector = registry.get(model_id)

    messages: list[dict] = [
        {"role": "system", "content": _DESKTOP_SYSTEM},
        {"role": "user", "content": task},
    ]
    result = DesktopAgentResult(model_id=model_id)

    for _ in range(max_iterations):
        response = await connector.generate_with_tools(
            messages=messages, tools=_DESKTOP_TOOL_SCHEMAS,
            max_tokens=1024, temperature=0.2,
        )
        tool_calls = response.get("tool_calls", [])
        if not tool_calls:
            result.final = response.get("content", "") or "Done."
            return result

        messages.append({
            "role": "assistant", "content": None,
            "tool_calls": [
                {"id": tc["id"], "type": "function",
                 "function": {"name": tc["name"], "arguments": json.dumps(tc.get("args", {}))}}
                for tc in tool_calls
            ],
        })
        for tc in tool_calls:
            output = await _execute_desktop_tool(tc["name"], tc.get("args", {}))
            ok = not output.startswith("ERROR")
            result.actions.append(DesktopAction(tc["name"], tc.get("args", {}), output, ok))
            messages.append({"role": "tool", "tool_call_id": tc["id"], "content": output})

    result.final = f"Reached {max_iterations} iterations without finishing."
    return result


@dataclass
class PlannedStep:
    step:    int
    action:  str
    agent:   AgentName
    details: str


async def plan_task(command: str) -> list[PlannedStep]:
    """BRAIN_MODEL decomposes the command into agent-routed steps.

    Uses generate_resilient() rather than a raw single-model call -- verified
    live (2026-07-10) that OpenRouter free tiers rate-limit fast, and a bare
    call left the whole brain dead for the 60s circuit-breaker window on the
    very first real test. generate_resilient() fails over to BRAIN_MODEL's
    cross-provider siblings before giving up -- same resilience every other
    team in this repo already gets."""
    from models.registry import generate_resilient
    prompt = f"{_PLANNER_SYSTEM}\n\nCommand: {command}"
    try:
        raw = await generate_resilient(BRAIN_MODEL, prompt=prompt, max_tokens=800,
                                        temperature=0.2, task_type="planning")
        data = json.loads(_extract_json(raw))
        steps = data.get("steps", [])
        return [PlannedStep(**s) for s in steps] or [PlannedStep(1, "Respond", "brain", command)]
    except Exception as exc:
        logger.warning(f"[vibemind] plan_task fallback ({str(exc)[:80]})")
        return [PlannedStep(1, "Respond", "brain", command)]


def _extract_json(text: str) -> str:
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("no JSON object found in planner response")
    return text[start:end + 1]


async def chat_brain(message: str, history: list[dict] | None = None) -> str:
    """Direct conversational reply from the brain, no task execution."""
    from models.registry import generate_resilient
    convo = "\n".join(f"{h['role']}: {h['content']}" for h in (history or [])[-10:])
    prompt = (
        "You are VibeMind, a JARVIS-style AI assistant. Reply conversationally and concisely.\n\n"
        f"{convo}\nuser: {message}" if convo else message
    )
    return await generate_resilient(BRAIN_MODEL, prompt=prompt, max_tokens=500,
                                     temperature=0.6, task_type="chat")


async def run_file_or_code_step(details: str, workspace: str) -> str:
    """File/code steps reuse VibeAI's own proven agent loop directly --
    this is exactly the kind of task it already handles well."""
    from core.agent_loop import run_agent
    result = await run_agent(task=details, model_id=LOCAL_AGENT_MODEL, task_type="coding", workspace=workspace)
    return result.final_response


async def dispatch_step(step: PlannedStep, workspace: str, full_command: str | None = None) -> dict[str, Any]:
    """Execute one planned step, routed by agent. Returns a dict shaped for
    action-log persistence: {agent, action, detail, ok}.

    full_command, when given, is used INSTEAD of step.details for file/code
    steps -- verified live (2026-07-10) that Gemma 4 sometimes splits one file
    task into separate "write the code" / "save the file" steps, each
    dispatched as an independent run_agent() call with no memory of the
    other's work: one call invented its own (wrong) filename and placeholder
    content because it never saw the first call's actual output. Handing the
    ORIGINAL command to whichever file/code step runs lets VibeAI's agent
    loop do the whole coherent job itself -- which is what it's actually
    good at (see the wordcount.py CLI-tool test in this project's history)."""
    # "app"/"web" AND "file" all go to the desktop agent: it now has the full
    # filesystem + installed-app toolset (vibemind/system.py), so real-PC file
    # browsing/opening/reading is desktop control, not a code-build task. Only
    # "code" (authoring NEW programs) uses VibeAI's sandboxed coding loop.
    if step.agent in ("app", "web", "file"):
        r = await run_desktop_agent(step.details)
        return {"agent": step.agent, "action": step.action, "detail": r.final,
                "ok": all(a.ok for a in r.actions) if r.actions else True,
                "sub_actions": [a.__dict__ for a in r.actions]}
    if step.agent == "code":
        detail = await run_file_or_code_step(full_command or step.details, workspace)
        return {"agent": step.agent, "action": step.action, "detail": detail, "ok": True}
    # "brain" or anything unrecognized: just respond conversationally
    detail = await chat_brain(step.details)
    return {"agent": "brain", "action": step.action, "detail": detail, "ok": True}
