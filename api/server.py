"""
api/server.py  (v2 — video upload + full team wiring)
FastAPI server with:
  POST /api/prompt        — text prompt
  POST /api/sensor        — edge-triggered hardware alerts (school heat monitor)
  POST /api/video         — video file upload → .frames pipeline
  POST /api/screenshot    — screenshot → vision team
  GET  /api/health
  WS   /ws/{client_id}    — real-time updates for VS Code GUI
"""
from __future__ import annotations

import asyncio
import hmac
import os
import shutil
import tempfile
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import (
    FastAPI, WebSocket, WebSocketDisconnect, WebSocketException, HTTPException,
    UploadFile, File, Form, Depends, Header, Query, status,
)
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger
from pydantic import BaseModel, Field

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
        # Constant-time compare -- a plain `!=` leaks timing information about
        # how many leading characters of the guess matched the real token.
        if not (authorization and hmac.compare_digest(authorization, f"Bearer {token}")):
            raise HTTPException(status_code=401, detail="Missing or invalid bearer token")
        return
    if not _is_localhost_bind():
        raise HTTPException(
            status_code=403,
            detail="Mutating routes are disabled: server is bound to a non-localhost "
                   "address without VIBE_API_TOKEN set. Set VIBE_API_TOKEN in .env.",
        )


# WebSocket connections can't set an Authorization header the way REST calls
# can -- the browser WebSocket API doesn't allow custom headers on the
# upgrade request. Found in code review (2026-07-13): both WS routes below
# had NO auth check at all -- neither require_token's Header-based nor
# anything else -- while every REST route uses Depends(require_token). Since
# these routes drive the exact same manager/agent pipelines as their gated
# REST twins (including arbitrary file writes and shell execution via
# AgentLoop), an unauthenticated WS connection is as dangerous as an
# unauthenticated POST /api/agent. Token travels as a query parameter
# instead (?token=...), which a WS client CAN set, checked with the same
# constant-time comparison. WebSocketException (not HTTPException) is the
# correct way to reject during a WS handshake -- FastAPI translates it into
# a proper WS close with the given code instead of an HTTP-only response
# that has no defined meaning on an upgrade request.
async def require_ws_token(token: str | None = Query(default=None)) -> None:
    expected = settings.vibe_api_token
    if expected:
        if not (token and hmac.compare_digest(token, expected)):
            raise WebSocketException(
                code=status.WS_1008_POLICY_VIOLATION, reason="Missing or invalid token"
            )
        return
    if not _is_localhost_bind():
        raise WebSocketException(
            code=status.WS_1008_POLICY_VIOLATION,
            reason="Mutating routes are disabled: server is bound to a non-localhost "
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


# ── REST: school heat-monitor sensor alerts ───────────────────────────────────
# Receives the two edge-triggered messages from hardware/controller.py (an
# ESP32 running MicroPython): "HIGH TEMPERATURE DETECTED" when the sensor
# crosses above the threshold, "Temperature normalized" when it crosses back
# below. The device sends each message exactly once per crossing, not on
# every 1s poll, so this endpoint fires once per real event -- not once per
# second.

class SensorRequest(BaseModel):
    message: str
    temperature: float | None = None
    device_id: str | None = None


class SensorResponse(BaseModel):
    received: str
    response: str


@app.post("/api/sensor", response_model=SensorResponse, dependencies=[Depends(require_token)])
async def handle_sensor(req: SensorRequest) -> SensorResponse:
    if not req.message.strip():
        raise HTTPException(400, "message cannot be empty")
    context = req.message.strip()
    if req.temperature is not None:
        context += f" (reading: {req.temperature:.1f}°C"
        if req.device_id:
            context += f", device: {req.device_id}"
        context += ")"
    logger.info(f"[sensor] {context}")
    response = await manager.handle_user_request(context)
    return SensorResponse(received=context, response=response)


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

class VideoResponse(BaseModel):
    status:        str
    upload_id:     str
    video_size_mb: float
    frames_path:   str
    response:      str


@app.post("/api/video", response_model=VideoResponse, dependencies=[Depends(require_token)])
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

class ScreenshotResponse(BaseModel):
    status:   str
    response: str


@app.post("/api/screenshot", response_model=ScreenshotResponse, dependencies=[Depends(require_token)])
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

@app.websocket("/ws/{client_id}", dependencies=[Depends(require_ws_token)])
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


class RecoverResponse(BaseModel):
    recovered:    bool
    active:       str
    using_backup: bool


@app.post("/api/manager/recover", response_model=RecoverResponse, dependencies=[Depends(require_token)])
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
    # Found in code review (2026-07-13): cli.py always passes context (the
    # /context command's "existing site grounding" text) and history (prior
    # conversation turns) to loop.run() — this route silently dropped both,
    # so API/VS-Code-driven agent runs got a materially worse result than
    # identical CLI-driven runs of the same backend (context is what stops
    # the agent from inventing an unrelated theme for an existing project).
    context:    str = ""
    history:    list[dict] = Field(default_factory=list)


class AgentResponse(BaseModel):
    status:         str
    final_response: str
    files_created:  list[str]
    files_edited:   list[str]
    commands_run:   list[str]
    iterations:     int
    total_ms:       float
    workspace:      str


@app.post("/api/agent", response_model=AgentResponse, dependencies=[Depends(require_token)])
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
        context=req.context,
        history=req.history,
    )

    return {
        # "ok" to match every other status-bearing route (/api/video,
        # /api/screenshot) -- found in code review (2026-07-13) that this
        # was the one route using "complete" instead.
        "status":          "ok",
        "final_response":  result.final_response,
        "files_created":   result.files_created,
        "files_edited":    result.files_edited,
        "commands_run":    result.commands_run,
        "iterations":      result.iterations,
        "total_ms":        round(result.total_ms),
        "workspace":       result.workspace,
    }


@app.websocket("/ws/agent/{client_id}", dependencies=[Depends(require_ws_token)])
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
    from pydantic import ValidationError

    # Validate against the same AgentRequest model the REST route enforces --
    # found in code review (2026-07-13): this path built task/model_id off
    # bare dict .get() calls with silent defaults (task="" instead of a
    # validation error), and dropped context/history entirely, unlike its
    # REST twin. Both gaps are closed by routing through the same model.
    try:
        req = AgentRequest(**data)
    except ValidationError as exc:
        await ws_mgr.send(cid, {"type": "agent_error", "message": f"invalid agent_task payload: {exc}"})
        return

    await ws_mgr.send(cid, {"type": "agent_start", "task": req.task})
    try:
        ws_path = Path(req.workspace) if req.workspace else Path("./workspace")
        loop   = AgentLoop(workspace=ws_path)
        result = await loop.run(
            task=req.task,
            model_id=req.model_id,
            task_type=req.task_type,
            context=req.context,
            history=req.history,
        )
        await ws_mgr.send(cid, {
            "type":          "agent_done",
            "response":      result.final_response,
            "files_created": result.files_created,
            "commands_run":  result.commands_run,
            "iterations":    result.iterations,
        })
    except Exception as exc:
        await ws_mgr.send(cid, {"type": "agent_error", "message": str(exc)})
