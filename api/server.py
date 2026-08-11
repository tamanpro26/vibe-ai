"""
api/server.py  (v2 — video upload + full team wiring)
FastAPI server with:
  POST /api/prompt        — text prompt
  POST /api/sensor        — edge-triggered hardware alerts + generated device plan
  GET  /api/push/vapid-public-key — Web Push public key
  POST /api/push/subscribe        — register a browser for fire alerts
  GET  /demo, /sw.js      — local-only fire-alert demo page (not deployed)
  POST /api/video         — video file upload → .frames pipeline
  POST /api/screenshot    — screenshot → vision team
  GET  /api/health
  WS   /ws/{client_id}    — real-time updates for VS Code GUI
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import os
import shutil
import tempfile
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Literal

from fastapi import (
    FastAPI, WebSocket, WebSocketDisconnect, WebSocketException, HTTPException,
    UploadFile, File, Form, Depends, Header, Query, status,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from loguru import logger
from pydantic import BaseModel, Field

from api.identity import get_current_principal
from config.settings import settings
from core.bus import bus
from core.state import get_capability_store, state
from core.push_notify import VAPID_PUBLIC_KEY, broadcast_alert, push_store
from core.device_planner import plan_device_response
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
    await get_capability_store().init()
    admin_ids = [item.strip() for item in settings.capability_bootstrap_admin_ids.split(",") if item.strip()]
    reviewer_ids = [item.strip() for item in settings.capability_bootstrap_reviewer_ids.split(",") if item.strip()]
    if admin_ids or reviewer_ids:
        await get_capability_store().bootstrap_roles_once(admin_ids, reviewer_ids)
    await push_store.init()
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

# Capability routers keep service authentication and verified end-user
# authorization as separate, mandatory boundaries.
from api.actions import router as capability_actions_router
from api.capabilities import router as capabilities_router
from api.integrations import router as capability_integrations_router

app.include_router(capabilities_router, dependencies=[Depends(require_token)])
app.include_router(capability_actions_router, dependencies=[Depends(require_token)])
app.include_router(capability_integrations_router, dependencies=[Depends(require_token)])


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
    team: str | None = None  # "brain"|"code"|"vision"|"design" override, or None/"auto"
    reasoning_mode: Literal["fast", "balanced", "deep"] | None = None
    # Base64 image (no data: prefix) sent alongside the prompt. This is the
    # ONLY way a browser can reach the vision team: _detect_media() scans the
    # prompt for a filename and checks whether it exists on the SERVER's
    # disk, which is a CLI assumption -- from a browser the file is on the
    # user's machine, so that check always failed and vision was never
    # activated. Passing the bytes directly is what closes that gap.
    image_b64: str | None = None
    # Server-side path to a video or pre-extracted .frames directory. Only
    # useful to callers that already put a file on this machine (the CLI, the
    # VS Code extension); a browser should POST /api/video instead, which
    # accepts a real upload and runs the frames pipeline.
    video_path: str | None = None
    frames_path: str | None = None
    history: list[dict[str, str]] = Field(default_factory=list)
    user_id: str | None = None
    search_context: str = ""


class PromptResponse(BaseModel):
    session_id: str
    response: str


# Validated here, not in the manager -- a bad value from the caller (e.g. the
# website's own "auto" default, or a typo) should just mean "no override",
# not an error the manager has to know how to reject.
_FORCEABLE_TEAMS = {"brain", "code", "vision", "design"}


@app.post("/api/prompt", response_model=PromptResponse, dependencies=[Depends(require_token)])
async def handle_prompt(req: PromptRequest) -> PromptResponse:
    if not req.prompt.strip():
        raise HTTPException(400, "Prompt cannot be empty")
    sid = req.session_id or f"s_{uuid.uuid4().hex[:8]}"
    forced_team = req.team if req.team in _FORCEABLE_TEAMS else None
    manager_args = {"forced_team": forced_team}
    if req.reasoning_mode:
        manager_args["reasoning_mode"] = req.reasoning_mode
    if req.user_id:
        manager_args["memory_namespace"] = (
            "web:" + hashlib.sha256(req.user_id.encode("utf-8")).hexdigest()[:24]
        )
    if req.search_context:
        manager_args["supplied_search_context"] = req.search_context[:16_000]

    # Media travels as `extra`, which is what _dispatch() checks to
    # force-activate the vision team (claude_manager.py). Without this the
    # team exists, works, and is simply never invoked from the web -- the
    # actual reason an uploaded image or video got a text-only answer.
    #
    # A non-empty extra also deliberately bypasses the fast path: that path
    # answers with a single text model that cannot see, so letting a media
    # request reach it would confidently describe nothing.
    extra: dict[str, Any] = {}
    if req.image_b64:
        extra["image_b64"] = req.image_b64
    if req.video_path:
        extra["video_path"] = req.video_path
    if req.frames_path:
        extra["frames_path"] = req.frames_path
    if extra:
        manager_args["extra"] = extra

    history_lines: list[str] = []
    history_chars = 12_000
    for turn in reversed(req.history[-8:]):
        role = turn.get("role")
        content = turn.get("content")
        if role not in {"user", "assistant"} or not isinstance(content, str):
            continue
        content = content.strip()[:min(6000, history_chars)]
        if content:
            history_lines.insert(0, f"{role.title()}: {content}")
            history_chars -= len(content)
        if history_chars <= 0:
            break

    manager_prompt = req.prompt
    if history_lines:
        manager_prompt = (
            "[Recent conversation — use this for continuity; answer only the current request]\n"
            + "\n\n".join(history_lines)
            + f"\n\n[Current request]\n{req.prompt}"
        )

    response = await manager.handle_user_request(manager_prompt, **manager_args)
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
    device_plan: dict[str, list[dict[str, int]]] | None = None


# Keep this guard aligned with TEMP_THRESHOLD_C in the ESP32 firmware. It is a
# server-side safety net for stale or misconfigured boards: a device's event
# label alone must never produce a physical fire alarm below this temperature.
HEAT_ALERT_THRESHOLD_C = 40.0


def is_actionable_high_temperature(message: str, temperature: float | None) -> bool:
    """Whether a sensor event is allowed to produce a high-temperature alarm."""
    return (
        message.strip() == "HIGH TEMPERATURE DETECTED"
        and (temperature is None or temperature > HEAT_ALERT_THRESHOLD_C)
    )


@app.post("/api/sensor", response_model=SensorResponse, dependencies=[Depends(require_token)])
async def handle_sensor(req: SensorRequest) -> SensorResponse:
    if not req.message.strip():
        raise HTTPException(400, "message cannot be empty")
    raw_message = req.message.strip()
    context = raw_message
    if req.temperature is not None:
        context += f" (reading: {req.temperature:.1f}°C"
        if req.device_id:
            context += f", device: {req.device_id}"
        context += ")"
    logger.info(f"[sensor] {context}")

    # A stale ESP32 build previously used a 30C threshold and reported HIGH
    # events around 34C. Do not send an alarm plan or a high-temperature push
    # notification for an event that contradicts the configured 40C limit.
    if raw_message == "HIGH TEMPERATURE DETECTED" and not is_actionable_high_temperature(
        raw_message, req.temperature
    ):
        logger.warning(
            "[sensor] ignored below-threshold HIGH event: "
            f"{req.temperature:.1f}C <={HEAT_ALERT_THRESHOLD_C:.1f}C"
        )
        return SensorResponse(
            received=context,
            response=(
                f"ignored: high-temperature alerts require a reading above "
                f"{HEAT_ALERT_THRESHOLD_C:.0f}C"
            ),
        )

    # Only an actionable HIGH event may produce a physical alarm plan. A
    # normalized event is informational and must leave every buzzer and LED
    # off, including on boards that still run the older firmware.
    device_plan = None
    if is_actionable_high_temperature(raw_message, req.temperature):
        # The device plan is the ONLY thing the ESP32 waits on -- it cannot
        # sound the alarm until this returns, so it is on the critical path.
        device_plan = await plan_device_response(raw_message, req.temperature)

    # Everything below is operator-facing (log narrative + phone push) and the
    # hardware never reads it, so it runs detached. Previously this was an
    # asyncio.gather() with the Manager call, which meant the device sat
    # waiting for a full multi-agent reasoning chain before it could make a
    # sound: measured live at ~1s to build the plan but ~14s until the HTTP
    # response actually came back. Detaching it takes the alarm's start-up
    # latency from ~15s to ~1-2s without losing any of the reasoning.
    async def _reasoning_and_alert() -> None:
        try:
            response = await manager.handle_user_request(context)
            logger.info(f"[sensor] manager: {response[:200]}")
            if raw_message == "HIGH TEMPERATURE DETECTED":
                temp_note = f" ({req.temperature:.1f}°C)" if req.temperature is not None else ""
                await broadcast_alert(
                    "🔥 High temperature detected",
                    f"Heat sensor tripped{temp_note}. Investigating.",
                )
            elif raw_message == "Temperature normalized":
                await broadcast_alert("✅ All clear", "Temperature is back to normal.")
        except Exception as exc:
            # Must never surface as an unhandled task exception: the alarm has
            # already fired successfully by this point, and a failure here is
            # strictly a degraded log/notification, not a failed alert.
            logger.warning(f"[sensor] background reasoning/alert failed: {exc}")

    asyncio.create_task(_reasoning_and_alert())

    return SensorResponse(
        received=context,
        response="device plan dispatched; reasoning and alerts continue in background",
        device_plan=device_plan,
    )


# ── Manual trigger flag (web-button demo fallback) ────────────────────────────
# The DHT11 sensor is confirmed dead (2026-07-30) -- this lets a web page
# button substitute for it. Only the ESP32 itself can actually run the
# buzzer/LED device plan (that code lives in its own firmware, driven by ITS
# OWN HTTP call to /api/sensor), so a web button hitting the server directly
# can't make the hardware react. Instead: the button sets this flag, the
# ESP32 polls it once per loop tick (alongside its existing DHT poll) and,
# when set, calls its own existing sendMessage() exactly like the serial
# 'h'/'n' trigger already does -- this only replaces the fragile serial link
# used to ask "please fire a test event now", not any of the working
# AI/device-plan/hardware pipeline downstream of that.
_pending_manual_trigger: str | None = None


class ManualTriggerRequest(BaseModel):
    event: str  # "high" or "normal"


@app.post("/api/sensor/manual-trigger", dependencies=[Depends(require_token)])
async def set_manual_trigger(req: ManualTriggerRequest) -> dict:
    global _pending_manual_trigger
    if req.event not in ("high", "normal"):
        raise HTTPException(400, "event must be 'high' or 'normal'")
    _pending_manual_trigger = req.event
    return {"queued": req.event}


@app.get("/api/sensor/manual-trigger")
async def get_manual_trigger() -> dict:
    """
    Consumed exactly once per poll: returns the pending event (or null) and
    clears it immediately, so the ESP32 fires it exactly once rather than
    repeatedly on every subsequent poll while nothing new has happened.
    """
    global _pending_manual_trigger
    event = _pending_manual_trigger
    _pending_manual_trigger = None
    return {"event": event}


# ── REST: Web Push subscriptions (fire-alert broadcast) ───────────────────────
# Deliberately NOT behind require_token: this is a safety opt-in, not a
# mutating agent action, and gating a fire-alert signup behind login would
# be a real usability regression for the one thing that most wants zero
# friction. VAPID public key has its own GET so the frontend never needs a
# copy of it baked into a separate secret channel.

class PushSubscribeRequest(BaseModel):
    subscription: dict


@app.get("/api/push/vapid-public-key")
async def push_vapid_public_key() -> dict:
    return {"publicKey": VAPID_PUBLIC_KEY}


@app.post("/api/push/subscribe")
async def push_subscribe(req: PushSubscribeRequest) -> dict:
    if not req.subscription.get("endpoint"):
        raise HTTPException(400, "subscription.endpoint is required")
    await push_store.add(req.subscription)
    return {"ok": True}


# ── Local-only fire-alert demo page ────────────────────────────────────────
# Standalone, not part of the deployed showcase site: this is what actually
# gets demoed for hardware/controller.py, served same-origin by this exact
# server so the demo needs no separate dev server, no CORS config, and no
# public deployment -- it only needs `uvicorn api.server:app` running.
DEMO_DIR = Path(__file__).resolve().parent.parent / "hardware" / "demo"


@app.get("/demo")
async def fire_alert_demo() -> FileResponse:
    return FileResponse(DEMO_DIR / "index.html")


@app.get("/sw.js")
async def demo_service_worker() -> FileResponse:
    return FileResponse(DEMO_DIR / "sw.js", media_type="application/javascript")


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


@app.post("/api/image/file", response_class=FileResponse, dependencies=[Depends(require_token)])
async def handle_image_file(req: ImageRequest) -> FileResponse:
    """Generate and return verified image bytes for web clients.

    The normal JSON endpoint remains useful to CLI callers. Browsers use this
    binary route so the generated JPEG is not inflated into base64 and copied
    through multiple JSON/localStorage layers.
    """
    if not req.prompt.strip():
        raise HTTPException(400, "Prompt cannot be empty")
    from tools.image_gen import generate_image

    result = await generate_image(req.prompt, req.style)
    if not result.ok or not result.path:
        raise HTTPException(502, result.error or "Image generation failed")
    return FileResponse(
        result.path,
        media_type="image/jpeg",
        headers={"X-Image-Source": result.url},
    )


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

@app.get("/api/registry")
async def registry_listing() -> dict:
    """The real model roster, for the web app's Registry surface.

    Read straight off MODEL_REGISTRY rather than from a copied list in the
    frontend: the site's registry/provider counts are claims about this
    system, and a hand-maintained duplicate is exactly how they go stale. The
    frontend previously carried its own hardcoded provider array for this.

    Unauthenticated on purpose -- this is the same public capability inventory
    the landing page already advertises (model ids, providers, roles, context
    windows). It exposes no keys, no prompts and no user data, and gating it
    would just mean the marketing numbers could not be sourced from the truth.
    """
    from config.models_config import MODEL_REGISTRY

    entries = [
        {
            "model_id": m.model_id,
            "provider": m.provider,
            "api_model": m.api_model,
            "team": m.team,
            "role": m.role,
            "context_window": m.context_window,
            "capabilities": list(m.capabilities or []),
        }
        for m in MODEL_REGISTRY.values()
    ]
    providers = sorted({m["provider"] for m in entries})
    return {
        "entries": entries,
        "count": len(entries),
        # Distinct endpoints, not entry count: several entries deliberately
        # point at the same upstream model under different roles, and
        # reporting only the entry count overstates how many distinct models
        # actually back the system.
        "distinct_endpoints": len({(m["provider"], m["api_model"]) for m in entries}),
        "providers": providers,
        "provider_count": len(providers),
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
    # When true, the response carries each written file's actual TEXT, not just
    # its path. The web Projects surface needs this: AgentResult reports paths
    # only, and a browser cannot read the server's disk, so without it there is
    # nothing to put in a downloadable zip.
    include_files: bool = False


class ProjectFile(BaseModel):
    path:    str
    content: str


class AgentResponse(BaseModel):
    status:         str
    final_response: str
    files_created:  list[str]
    files_edited:   list[str]
    commands_run:   list[str]
    iterations:     int
    total_ms:       float
    workspace:      str
    files:          list[ProjectFile] = Field(default_factory=list)
    files_truncated: bool = False


# Caps exist because this returns file bodies over HTTP to a browser. A runaway
# agent that writes a 400 MB log, or a task that npm-installs before anyone can
# stop it, must not become a 400 MB JSON response.
_MAX_PROJECT_FILES = 60
_MAX_PROJECT_BYTES = 2_000_000       # 2 MB of text in total
_MAX_ONE_FILE_BYTES = 256_000        # skip any single file larger than this
_SKIP_PROJECT_DIRS = {"node_modules", ".git", "dist", "build", "__pycache__", ".venv"}


def _collect_project_files(workspace: str, paths: list[str]) -> tuple[list[dict], bool]:
    """Read back the files an agent run wrote, for the downloadable zip.

    Only reads paths the agent itself reported, and only when they resolve
    INSIDE the run's own workspace -- the reported path is agent-controlled
    text, so it is treated as untrusted input and containment-checked rather
    than opened directly. Returns (files, truncated).
    """
    root = Path(workspace).resolve()
    out: list[dict] = []
    total = 0
    truncated = False

    for rel in dict.fromkeys(paths):          # dedupe, preserve order
        if len(out) >= _MAX_PROJECT_FILES:
            truncated = True
            break
        candidate = Path(rel)
        target = (candidate if candidate.is_absolute() else root / candidate).resolve()
        # Containment: a reported path of "../../.env" or an absolute path
        # elsewhere on disk must never be served.
        if not target.is_relative_to(root) or not target.is_file():
            continue
        if _SKIP_PROJECT_DIRS.intersection(target.relative_to(root).parts):
            continue
        try:
            if target.stat().st_size > _MAX_ONE_FILE_BYTES:
                truncated = True
                continue
            text = target.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue                          # binary or unreadable: not zip content
        total += len(text.encode("utf-8"))
        if total > _MAX_PROJECT_BYTES:
            truncated = True
            break
        out.append({"path": target.relative_to(root).as_posix(), "content": text})

    return out, truncated


async def _execute_agent(req: AgentRequest) -> dict:
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

    files: list[dict] = []
    files_truncated = False
    if req.include_files:
        files, files_truncated = _collect_project_files(
            result.workspace, result.files_created + result.files_edited
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
        "files":           files,
        "files_truncated": files_truncated,
    }


@app.post("/api/agent", response_model=AgentResponse, dependencies=[Depends(require_token)])
async def run_agent(req: AgentRequest) -> dict:
    return await _execute_agent(req)


# Long browser builds cannot safely hold a Vercel request open. The job lives
# on this persistent backend; the serverless proxy starts it quickly and polls
# status with short requests. Results are intentionally memory-backed: project
# files themselves are already written to the workspace, while this record is
# only the delivery envelope for the current deployment process.
_AGENT_JOBS: dict[str, dict[str, Any]] = {}
_AGENT_JOB_TASKS: set[asyncio.Task] = set()
_MAX_AGENT_JOBS = 100
_MAX_RUNNING_AGENT_JOBS = 2
_AGENT_JOB_TIMEOUT_SECONDS = 20 * 60
_ACTIVE_AGENT_WORKSPACES: set[str] = set()


def _trim_agent_jobs() -> None:
    finished = [
        (job_id, job.get("finished_at", 0.0))
        for job_id, job in _AGENT_JOBS.items()
        if job.get("status") in {"completed", "failed"}
    ]
    for job_id, _ in sorted(finished, key=lambda item: item[1])[:max(0, len(_AGENT_JOBS) - _MAX_AGENT_JOBS)]:
        _AGENT_JOBS.pop(job_id, None)


async def _run_agent_job(job_id: str, req: AgentRequest) -> None:
    owner_id = _AGENT_JOBS.get(job_id, {}).get("owner_id")
    try:
        result = await asyncio.wait_for(
            _execute_agent(req), timeout=_AGENT_JOB_TIMEOUT_SECONDS
        )
        _AGENT_JOBS[job_id] = {
            "job_id": job_id,
            "status": "completed",
            "result": result,
            "finished_at": time.time(),
            "owner_id": owner_id,
        }
    except TimeoutError:
        _AGENT_JOBS[job_id] = {
            "job_id": job_id,
            "status": "failed",
            "error": "build timed out after 20 minutes",
            "finished_at": time.time(),
            "owner_id": owner_id,
        }
    except asyncio.CancelledError:
        _AGENT_JOBS[job_id] = {
            "job_id": job_id,
            "status": "failed",
            "error": "build cancelled",
            "finished_at": time.time(),
            "owner_id": owner_id,
        }
        raise
    except Exception as exc:
        logger.exception("Agent job {} failed", job_id)
        _AGENT_JOBS[job_id] = {
            "job_id": job_id,
            "status": "failed",
            "error": str(exc),
            "finished_at": time.time(),
            "owner_id": owner_id,
        }
    finally:
        _ACTIVE_AGENT_WORKSPACES.discard(req.workspace or "./workspace")
        _trim_agent_jobs()


@app.post("/api/agent/jobs", status_code=202, dependencies=[Depends(require_token)])
async def start_agent_job(
    req: AgentRequest,
    principal=Depends(get_current_principal),
) -> dict:
    if len(_ACTIVE_AGENT_WORKSPACES) >= _MAX_RUNNING_AGENT_JOBS:
        raise HTTPException(429, "agent build capacity reached; try again shortly")
    workspace_key = req.workspace or "./workspace"
    if workspace_key in _ACTIVE_AGENT_WORKSPACES:
        raise HTTPException(409, "a build is already running for this workspace")
    _ACTIVE_AGENT_WORKSPACES.add(workspace_key)
    job_id = uuid.uuid4().hex
    _AGENT_JOBS[job_id] = {
        "job_id": job_id,
        "status": "running",
        "started_at": time.time(),
        "owner_id": principal.subject,
    }
    task = asyncio.create_task(_run_agent_job(job_id, req))
    _AGENT_JOB_TASKS.add(task)
    task.add_done_callback(_AGENT_JOB_TASKS.discard)
    _trim_agent_jobs()
    return {"job_id": job_id, "status": "running"}


@app.get("/api/agent/jobs/{job_id}", dependencies=[Depends(require_token)])
async def get_agent_job(
    job_id: str,
    principal=Depends(get_current_principal),
) -> dict:
    job = _AGENT_JOBS.get(job_id)
    if not job or job.get("owner_id") != principal.subject:
        raise HTTPException(404, "agent job not found")
    return job


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
