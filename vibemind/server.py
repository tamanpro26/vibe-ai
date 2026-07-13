"""
vibemind/server.py
FastAPI backend for VibeMind. Replaces the Manus scaffold's tRPC/Express
server, which had zero real feature routers (server/routers.ts was just
auth/system boilerplate) and whose LLM calls went through a Manus-only
gateway (forge.manus.im) that is unreachable outside their hosted platform.

Auth/CORS mirror api/server.py's existing discipline -- this surface is if
anything MORE sensitive (it can type into and control arbitrary desktop
windows, not just edit files in a workspace), so it gets the same treatment:
mutating routes require a bearer token unless bound to localhost, and CORS
is restricted to the known local Vite dev origins.
"""
from __future__ import annotations

import os
import sys
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, UploadFile, File, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger
from pydantic import BaseModel

# VibeMind reads its own config via plain os.getenv() (SPOTIFY_CLIENT_ID,
# VIBEMIND_API_TOKEN, ...) rather than VibeAI's pydantic Settings class --
# nothing loads .env into the process automatically for that path. Verified
# live (2026-07-10): without this, SPOTIFY_CLIENT_ID in .env was invisible to
# os.getenv() at runtime even though the file had it set.
#
# Path differs frozen vs. dev: a PyInstaller-frozen exe has no "repo root"
# two directories up -- electron-builder ships .env as an extraResource
# sitting next to the exe instead (see jarvis-ai-assistant/package.json's
# build.extraResources and electron/main.cjs). `sys.frozen` is PyInstaller's
# own flag for "am I running from a bundle."
if getattr(sys, "frozen", False):
    _env_path = Path(sys.executable).resolve().parent / ".env"
else:
    _env_path = Path(__file__).resolve().parent.parent / ".env"
load_dotenv(_env_path)

from vibemind.db import db
from vibemind.orchestrator import handle_message


def _is_localhost_bind() -> bool:
    return os.getenv("VIBEMIND_API_HOST", "127.0.0.1") in ("127.0.0.1", "localhost", "::1")


async def require_token(authorization: str | None = Header(default=None)) -> None:
    token = os.getenv("VIBEMIND_API_TOKEN", "")
    if token:
        if authorization != f"Bearer {token}":
            raise HTTPException(status_code=401, detail="Missing or invalid bearer token")
        return
    if not _is_localhost_bind():
        raise HTTPException(
            status_code=403,
            detail="Mutating routes are disabled: server is bound to a non-localhost "
                   "address without VIBEMIND_API_TOKEN set.",
        )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    logger.info("VibeMind starting...")
    await db.init()
    for agent in ("brain", "app", "web", "file", "code"):
        await db.upsert_agent_status(agent, status="idle")
    # "Get comfortable before working" (user request, 2026-07-10): build the
    # system profile (installed apps, drives, home dir) ONCE now, in the
    # background, rather than the assistant discovering its own environment
    # from scratch on every single command. See vibemind/profile.py and the
    # fast path (vibemind/fastpath.py) that relies on this being warm.
    from vibemind.profile import get_profile
    get_profile()
    # Same idea for fastpath.py's tier-2 AI intent classifier: fire the
    # model-load call now, in the background, so it's already warm in
    # Ollama's memory before a real user message needs it. Verified live
    # (2026-07-11) that a cold model's first call costs ~10s vs ~400-500ms
    # warm -- without this, whichever request happens to hit tier 2 first
    # eats that cold-start cost, which looks exactly like the slow-pipeline
    # bug this whole fast-path system exists to eliminate. Backgrounded
    # (not awaited) so it never delays the app becoming ready.
    import asyncio
    from vibemind.fastpath import warm_up_intent_model
    asyncio.create_task(warm_up_intent_model())
    logger.info("VibeMind ready.")
    yield
    logger.info("VibeMind shutting down.")


app = FastAPI(title="VibeMind", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    # Dev browser origins, plus the packaged Electron renderer, which loads over
    # file:// and therefore sends `Origin: null`. The real security boundary
    # for this desktop-controlling API is the localhost bind + optional bearer
    # token (see require_token), not CORS -- CORS only stops a random website
    # in a normal browser, which can't be the case for a file:// Electron shell.
    allow_origins=[
        "http://localhost:5173", "http://127.0.0.1:5173",
        "http://localhost:3000", "http://127.0.0.1:3000",
        "null",
    ],
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type"],
)


# ── Schemas ──────────────────────────────────────────────────────────────

class NewConversation(BaseModel):
    title: str | None = None
    agent_mode: str = "brain"


class ChatRequest(BaseModel):
    conversation_id: int
    message: str


# ── Health ───────────────────────────────────────────────────────────────

@app.get("/api/health")
async def health():
    return {"status": "ok", "product": "VibeMind"}


# ── Conversations ────────────────────────────────────────────────────────

@app.post("/api/conversations", dependencies=[Depends(require_token)])
async def create_conversation(body: NewConversation):
    conv_id = await db.create_conversation(body.title, body.agent_mode)
    return {"id": conv_id}


@app.get("/api/conversations")
async def list_conversations():
    return await db.get_conversations()


@app.get("/api/conversations/{conversation_id}/messages")
async def get_messages(conversation_id: int):
    return await db.get_messages(conversation_id)


# ── Chat (the main entry point: plan + execute + reply) ────────────────

@app.post("/api/chat", dependencies=[Depends(require_token)])
async def chat(body: ChatRequest):
    try:
        return await handle_message(body.conversation_id, body.message)
    except Exception as exc:
        logger.error(f"[vibemind-api] chat failed: {exc}")
        raise HTTPException(status_code=500, detail=str(exc))


# ── Tasks ────────────────────────────────────────────────────────────────

@app.get("/api/tasks")
async def list_tasks(status: str | None = None):
    return await db.get_tasks(status)


# ── Action logs ──────────────────────────────────────────────────────────

@app.get("/api/action-logs")
async def list_action_logs(limit: int = 100):
    return await db.get_action_logs(limit)


# ── Agent status ─────────────────────────────────────────────────────────

@app.get("/api/agents/status")
async def agents_status():
    return await db.get_agent_statuses()


# ── Voice ────────────────────────────────────────────────────────────────

@app.post("/api/voice", dependencies=[Depends(require_token)])
async def voice(conversation_id: int, file: UploadFile = File(...)):
    """Transcribe uploaded audio (via VibeAI's existing Groq Whisper tool)
    then run it through the same handle_message() pipeline as typed chat."""
    from tools.voice_input import transcribe_file

    suffix = Path(file.filename or "audio.wav").suffix or ".wav"
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(await file.read())
        tmp_path = tmp.name
    try:
        transcription = await transcribe_file(tmp_path)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"transcription failed: {exc}")
    finally:
        Path(tmp_path).unlink(missing_ok=True)

    await db.add_voice_transcription(conversation_id, transcription)
    result = await handle_message(conversation_id, transcription)
    return {"transcription": transcription, **result}


# ── System / filesystem / apps ───────────────────────────────────────────
# The "fully connected to PC storage and all the apps" surface. GET routes are
# read-only (browse, list apps, system info). Mutating/OS-affecting routes
# (write, open) go through require_token like every other side-effecting route.

@app.get("/api/system/info")
async def system_info():
    from vibemind import system
    return system.system_info()


@app.get("/api/system/drives")
async def system_drives():
    from vibemind import system
    return {"drives": system.list_drives(), "home": system.home_dir()}


@app.get("/api/fs/list")
async def fs_list(path: str | None = None):
    from vibemind import system
    return system.list_directory(path)


@app.get("/api/fs/read")
async def fs_read(path: str):
    from vibemind import system
    return system.read_text_file(path)


@app.get("/api/fs/search")
async def fs_search(root: str, query: str, limit: int = 100):
    from vibemind import system
    return system.search_files(root, query, limit)


@app.get("/api/apps")
async def apps():
    from vibemind import system
    return {"apps": system.list_installed_apps()}


class WriteFileBody(BaseModel):
    path: str
    content: str


@app.post("/api/fs/write", dependencies=[Depends(require_token)])
async def fs_write(body: WriteFileBody):
    from vibemind import system
    return system.write_text_file(body.path, body.content)


class OpenPathBody(BaseModel):
    path: str


@app.post("/api/fs/open", dependencies=[Depends(require_token)])
async def fs_open(body: OpenPathBody):
    from vibemind import system
    return system.open_path(body.path)
