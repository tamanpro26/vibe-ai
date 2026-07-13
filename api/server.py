"""
api/server.py  (v2 — video upload + full team wiring)
FastAPI server with:
  POST /api/prompt        — text prompt
  POST /api/video         — video file upload → .frames pipeline
  POST /api/screenshot    — screenshot → vision team
  GET  /api/health
  WS   /ws/{client_id}    — real-time updates for VS Code GUI
"""
from __future__ import annotations

import asyncio
import os
import shutil
import tempfile
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import (
    FastAPI, WebSocket, WebSocketDisconnect, HTTPException, UploadFile, File,
    Form, Depends, Header,
)
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger
from pydantic import BaseModel

from config.settings import settings
from core.bus import bus
from core.state import state
from manager.claude_manager import manager

UPLOAD_DIR = Path("./uploads")
UPLOAD_DIR.mkdir(exist_ok=True)


# ── Auth ───────────────────────────────────────────────────────────────────────
# This API can create files and execute shell commands via the agent — it is a
# remote-code-execution surface by design and MUST NOT be reachable without a
# token except on localhost. When VIBE_API_TOKEN is set, every mutating route
# requires "Authorization: Bearer <token>". When it is NOT set, mutating routes
# are refused outright unless the server is bound to localhost (dev mode).

def _is_localhost_bind() -> bool:
    return settings.api_host in ("127.0.0.1", "localhost", "::1")


async def require_token(authorization: str | None = Header(default=None)) -> None:
    token = settings.vibe_api_token
    if token:
        if authorization != f"Bearer {token}":
            raise HTTPException(status_code=401, detail="Missing or invalid bearer token")
        return
    if not _is_localhost_bind():
        raise HTTPException(
            status_code=403,
            detail="Mutating routes are disabled: server is bound to a non-localhost "
                   "address without VIBE_API_TOKEN set. Set VIBE_API_TOKEN in .env.",
        )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    logger.info("VibeAI v2 starting...")
    await state.init()
    logger.info("VibeAI ready ✓  (multi-provider orchestration · video_observer integrated)")
    yield
    logger.info("VibeAI shutting down.")
    shutil.rmtree(UPLOAD_DIR, ignore_errors=True)


app = FastAPI(title="VibeAI", version="2.0.0", lifespan=lifespan)
# CORS restricted to local GUI origins only — "*" on an API that executes shell
# commands means any web page the user visits can drive the agent.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000", "http://127.0.0.1:3000",   # web GUI dev server
        "http://localhost:5173", "http://127.0.0.1:5173",   # vite previews
        "vscode-webview://*",                                # VS Code extension
    ],
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type"],
)


# ── WebSocket manager ─────────────────────────────────────────────────────────

class ConnectionManager:
    def __init__(self) -> None:
        self._connections: dict[str, WebSocket] = {}

    async def connect(self, cid: str, ws: WebSocket) -> None:
        await ws.accept()
        self._connections[cid] = ws

    def disconnect(self, cid: str) -> None:
        self._connections.pop(cid, None)

    async def send(self, cid: str, data: dict) -> None:
        ws = self._connections.get(cid)
        if ws:
            try:
                await ws.send_json(data)
            except Exception:
                self.disconnect(cid)

    async def broadcast(self, data: dict) -> None:
        for cid, ws in list(self._connections.items()):
            try:
                await ws.send_json(data)
            except Exception:
                self.disconnect(cid)


ws_mgr = ConnectionManager()


# ── REST: text prompt ─────────────────────────────────────────────────────────

class PromptRequest(BaseModel):
    prompt: str
    session_id: str | None = None


class PromptResponse(BaseModel):
    session_id: str
    response: str


@app.post("/api/prompt", response_model=PromptResponse, dependencies=[Depends(require_token)])
async def handle_prompt(req: PromptRequest) -> PromptResponse:
    if not req.prompt.strip():
        raise HTTPException(400, "Prompt cannot be empty")
    sid = req.session_id or f"s_{uuid.uuid4().hex[:8]}"
    response = await manager.handle_user_request(req.prompt)
    return PromptResponse(session_id=sid, response=response)


# ── REST: standalone image generation ─────────────────────────────────────────
# Distinct from the design_asset tool the coding agent uses internally for
# website assets (teams/design.py's DesignTeam only fires for
# TaskType.UI_DESIGN, inside the full manager+critic+review pipeline built
# for iterative site-building). This is the direct "generate me a picture"
# path with none of that overhead. See tools/image_gen.py.

class ImageRequest(BaseModel):
    prompt: str
    style:  str = ""


class ImageResponse(BaseModel):
    ok:     bool
    path:   str = ""
    url:    str = ""
    error:  str = ""


@app.post("/api/image", response_model=ImageResponse, dependencies=[Depends(require_token)])
async def handle_image(req: ImageRequest) -> ImageResponse:
    if not req.prompt.strip():
        raise HTTPException(400, "Prompt cannot be empty")
    from tools.image_gen import generate_image
    result = await generate_image(req.prompt, req.style)
    return ImageResponse(ok=result.ok, path=result.path, url=result.url, error=result.error)


# ── REST: video upload → .frames pipeline ─────────────────────────────────────

@app.post("/api/video", dependencies=[Depends(require_token)])
async def handle_video(
    file: UploadFile = File(...),
    prompt: str = Form(default="Analyse this video"),
) -> dict:
    """
    Upload a video file. The system:
    1. Saves it temporarily
    2. Converts to .frames using video_observer
    3. Runs vision team analysis
    4. Returns the analysis
    """
    allowed = {".mp4", ".webm", ".mov", ".avi", ".mkv"}
    ext = Path(file.filename or "video.mp4").suffix.lower()
    if ext not in allowed:
        raise HTTPException(400, f"Unsupported format {ext}. Use: {allowed}")

    # Save upload
    upload_id   = uuid.uuid4().hex[:8]
    video_path  = UPLOAD_DIR / f"{upload_id}{ext}"
    frames_path = UPLOAD_DIR / f"{upload_id}.frames"

    with open(video_path, "wb") as f:
        shutil.copyfileobj(file.file, f)

    file_mb = video_path.stat().st_size / 1e6
    logger.info(f"[api/video] saved {video_path.name} ({file_mb:.1f} MB)")

    try:
        response = await manager.handle_user_request(
            user_prompt=prompt,
            extra={
                "video_path": str(video_path),
                "frames_path": None,   # manager will convert
            },
        )
        return {
            "status": "ok",
            "upload_id": upload_id,
            "video_size_mb": round(file_mb, 2),
            "frames_path": str(frames_path),
            "response": response,
        }
    finally:
        # Clean up video (keep .frames for caching)
        video_path.unlink(missing_ok=True)


# ── REST: screenshot → vision team ───────────────────────────────────────────

@app.post("/api/screenshot", dependencies=[Depends(require_token)])
async def handle_screenshot(
    file: UploadFile = File(...),
    prompt: str = Form(default="Analyse this screenshot"),
) -> dict:
    """Upload a screenshot for UI analysis."""
    import base64

    data = await file.read()
    image_b64 = base64.b64encode(data).decode()

    response = await manager.handle_user_request(
        user_prompt=prompt,
        extra={"image_b64": image_b64},
    )
    return {"status": "ok", "response": response}


# ── REST: utilities ───────────────────────────────────────────────────────────

@app.get("/api/health")
async def health() -> dict:
    # Report what actually runs, computed from the registry — not a marketing
    # string. "manager" reflects the real active tier (the Free Council is the
    # primary unless a valid Anthropic key is configured).
    from config.models_config import MODEL_REGISTRY
    from tools.manager_fallback import fallback_chain
    endpoints = {(m.provider, m.api_model) for m in MODEL_REGISTRY.values()}
    return {
        "status": "ok",
        "version": "2.0.0",
        "manager": fallback_chain.status_report().get("active_manager", "free-council"),
        "registry_entries": len(MODEL_REGISTRY),
        "distinct_endpoints": len(endpoints),
        "video_observer": "integrated",
        "frames_format": "v1",
    }

@app.get("/api/bus/stats")
async def bus_stats() -> dict:
    return bus.stats()


@app.get("/api/history")
async def change_history(limit: int = 50) -> dict:
    """Read-only view of the agent's change journal for the default workspace
    (core/change_history.py): what files it touched, when, and for which task."""
    from core.change_history import get_history
    from tools.agent_tools import DEFAULT_WORKSPACE
    entries = get_history(DEFAULT_WORKSPACE, limit=max(1, min(limit, 500)))
    return {"workspace": str(DEFAULT_WORKSPACE), "count": len(entries), "entries": entries}


# ── WebSocket ─────────────────────────────────────────────────────────────────

@app.websocket("/ws/{client_id}")
async def websocket_endpoint(ws: WebSocket, client_id: str) -> None:
    await ws_mgr.connect(client_id, ws)
    try:
        while True:
            data = await ws.receive_json()
            msg_type = data.get("type")

            if msg_type == "prompt":
                asyncio.create_task(
                    _ws_pipeline(client_id, data.get("content", ""), {})
                )
            elif msg_type == "video_path":
                # VS Code GUI can send a local file path directly
                asyncio.create_task(
                    _ws_pipeline(
                        client_id,
                        data.get("prompt", "Analyse this video"),
                        {"video_path": data.get("path")},
                    )
                )

    except WebSocketDisconnect:
        ws_mgr.disconnect(client_id)


async def _ws_pipeline(cid: str, prompt: str, extra: dict) -> None:
    try:
        await ws_mgr.send(cid, {"type": "status", "stage": "refining", "msg": "Refining prompt..."})
        response = await manager.handle_user_request(prompt, extra=extra)
        await ws_mgr.send(cid, {"type": "final", "response": response})
    except Exception as exc:
        await ws_mgr.send(cid, {"type": "error", "message": str(exc)})


# ── Manager status & control endpoints ───────────────────────────────────────

@app.get("/api/manager/status")
async def manager_status() -> dict:
    """
    Show which manager tier is currently active and the health of all tiers.
    Useful when debugging credit exhaustion or rate limiting.
    """
    from tools.manager_fallback import fallback_chain
    return fallback_chain.status_report()


@app.post("/api/manager/recover", dependencies=[Depends(require_token)])
async def manager_recover() -> dict:
    """
    Attempt to bring Claude Sonnet 4.6 (primary) back online.
    Call this after topping up Anthropic credits.
    """
    from tools.manager_fallback import fallback_chain
    ok = await fallback_chain.force_primary()
    return {
        "recovered": ok,
        "active": fallback_chain.active_name,
        "using_backup": fallback_chain.using_backup,
    }


@app.get("/api/manager/chain")
async def manager_chain() -> dict:
    """
    The REAL manager architecture: a 2-tier design. Tier 1 is the optional paid
    Claude manager (only if a valid key is configured); Tier 2 is the Free
    Council — 5 free models collaborating per task. All data here comes from
    live status, not hardcoded strings. (This endpoint previously served a
    fictional 6-tier chain with invented benchmark numbers and also raised
    KeyError on every call — both removed.)
    """
    from tools.manager_fallback import fallback_chain
    report = fallback_chain.status_report()
    return {
        "design": "2-tier: optional paid manager -> Free Council (5 collaborating free models)",
        "active_manager": report.get("active_manager"),
        "using_free_council": report.get("using_free_team"),
        "claude_tier": report.get("claude"),
        "free_council": report.get("free_team"),
        "recovery_interval_s": report.get("recovery_interval"),
    }


@app.get("/api/manager/free-team")
async def free_team_status() -> dict:
    """
    Show the Free Manager Council composition and activity.
    The Council activates automatically when Claude Sonnet 4.6 is unavailable.
    """
    from manager.free_manager import free_manager_team
    status = free_manager_team.status()
    status["triggered_by"] = "Activates when Claude Sonnet 4.6 is unavailable"
    status["how_it_works"] = (
        "5 free models collaborating on every task via a Plan → Draft → Critique "
        "→ Refine → Polish pipeline. Review tasks use a 2-model consensus instead "
        "(Critic + Refiner roles, scores merged)."
    )
    return status


# ── Agent endpoint — autonomous coding with file creation + bash ──────────────

class AgentRequest(BaseModel):
    task:       str
    model_id:   str = "gemini_flash"      # Gemini 2.5 Flash — best free model for tool calling
    task_type:  str = "coding"       # coding | reasoning | creative
    workspace:  str | None = None    # custom workspace path (default: ./workspace)


@app.post("/api/agent", dependencies=[Depends(require_token)])
async def run_agent(req: AgentRequest) -> dict:
    """
    Run an autonomous agent that creates files and runs commands by itself.

    The agent will:
      1. Plan what needs to be done
      2. Create multiple files simultaneously
      3. Run terminal commands to install deps, execute code, run tests
      4. Fix any errors automatically
      5. Return when the task is fully complete

    Example: {"task": "Build a FastAPI app with a /users endpoint, SQLite DB, and pytest tests"}
    """
    from core.agent_loop import AgentLoop
    from pathlib import Path

    ws = Path(req.workspace) if req.workspace else None
    loop = AgentLoop(workspace=ws or Path("./workspace"))

    result = await loop.run(
        task=req.task,
        model_id=req.model_id,
        task_type=req.task_type,
    )

    return {
        "status":          "complete",
        "final_response":  result.final_response,
        "files_created":   result.files_created,
        "files_edited":    result.files_edited,
        "commands_run":    result.commands_run,
        "iterations":      result.iterations,
        "total_ms":        round(result.total_ms),
        "workspace":       result.workspace,
    }


@app.websocket("/ws/agent/{client_id}")
async def agent_websocket(ws: WebSocket, client_id: str) -> None:
    """WebSocket endpoint for real-time agent progress streaming."""
    await ws_mgr.connect(client_id, ws)
    try:
        while True:
            data = await ws.receive_json()
            if data.get("type") == "agent_task":
                asyncio.create_task(
                    _run_agent_ws(client_id, data)
                )
    except WebSocketDisconnect:
        ws_mgr.disconnect(client_id)


async def _run_agent_ws(cid: str, data: dict) -> None:
    from core.agent_loop import AgentLoop
    from pathlib import Path

    task     = data.get("task", "")
    model_id = data.get("model_id", "gemini_flash")

    await ws_mgr.send(cid, {"type": "agent_start", "task": task})
    try:
        loop   = AgentLoop(workspace=Path("./workspace"))
        result = await loop.run(task=task, model_id=model_id)
        await ws_mgr.send(cid, {
            "type":          "agent_done",
            "response":      result.final_response,
            "files_created": result.files_created,
            "commands_run":  result.commands_run,
            "iterations":    result.iterations,
        })
    except Exception as exc:
        await ws_mgr.send(cid, {"type": "agent_error", "message": str(exc)})
