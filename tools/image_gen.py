"""
tools/image_gen.py
Standalone "generate me an image" feature — distinct from teams/design.py's
DesignTeam, which only fires for TaskType.UI_DESIGN inside the full manager
pipeline (brief_pipeline enrichment, adversarial critic, multi-round review)
built for iterative WEBSITE asset generation. There was no path for a user
to just ask for a picture and get one back; this is that path — CLI `/image`
and `POST /api/image`, both calling straight into this module.

Reuses flux_asset (models/connectors/pollinations.py) rather than adding a
new provider: verified live (2026-07-13) that Pollinations' `model=`
parameter is currently COLLAPSED — flux/turbo/sana all return the
byte-identical image for the same prompt right now, only `seedream` differs
(and it's broken, 500, already documented). So there is exactly ONE real
working image source today, not five; flux_asset is that one, used honestly
as one model rather than pretending style selection does anything.

Downloads go through curl, not aiohttp — same reason as
core/agent_loop.py::_localize_remote_images: Pollinations' CDN serves
aiohttp an empty 200 (TLS-fingerprint bot filtering, verified directly),
curl gets the real bytes.
"""
from __future__ import annotations

import asyncio
import hashlib
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

from tools.agent_tools import DEFAULT_WORKSPACE

# Found in code review (2026-07-13): this used to live at ~/.vibeai/images,
# following the same convention as routing_memory.json -- but that's outside
# DEFAULT_WORKSPACE, the sandboxed root every agent file tool operates
# within (ToolExecutor._safe_path resolves against it). An image generated
# here was invisible to a later same-session agent task ("use that image I
# just generated"), since the agent has no path into the user's home
# directory. Living inside the workspace, in its own clearly-separated
# subdirectory, gives the agent real visibility without mixing generated
# images into arbitrary project files at the workspace root.
IMAGES_DIR = DEFAULT_WORKSPACE / "generated_images"

_QUALITY_SUFFIX = "high quality, professional, sharp, detailed, well-composed"


@dataclass
class GeneratedImage:
    ok:     bool
    path:   str = ""
    url:    str = ""
    prompt: str = ""
    error:  str = ""


def _slug(prompt: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", prompt.lower()).strip("-")[:40]
    return s or "image"


async def generate_image(prompt: str, style: str = "") -> GeneratedImage:
    """Generate one image from a prompt and save it locally. Fast path, no
    heavy LLM prompt-expansion round trip — this is a quick single-shot
    feature, not the iterative web-asset pipeline DesignTeam already covers.
    """
    prompt = (prompt or "").strip()
    if not prompt:
        return GeneratedImage(ok=False, error="empty prompt")

    full_prompt = f"{prompt}, {style}, {_QUALITY_SUFFIX}" if style else f"{prompt}, {_QUALITY_SUFFIX}"

    try:
        from models.registry import registry
        connector = registry.get("flux_asset")
        url = await connector.generate(prompt=full_prompt, max_tokens=100)
        if not url or not url.startswith("http"):
            return GeneratedImage(ok=False, error=f"no image URL returned ({url[:80]!r})")
    except Exception as exc:
        return GeneratedImage(ok=False, error=f"generation call failed: {str(exc)[:150]}")

    curl = shutil.which("curl")
    if not curl:
        # aiohttp would silently get 0 bytes from Pollinations' CDN (see
        # module docstring) -- without curl there is no working download
        # path, so say so rather than saving a broken/empty file.
        return GeneratedImage(ok=False, url=url, prompt=prompt, error="curl not found on PATH — cannot download image")

    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    filename = f"{_slug(prompt)}-{hashlib.sha1(url.encode()).hexdigest()[:8]}.jpg"
    target = IMAGES_DIR / filename

    try:
        proc = await asyncio.create_subprocess_exec(
            curl, "-sL", "--max-time", "90", "-o", str(target), url,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(proc.communicate(), timeout=100)
        if proc.returncode != 0 or not target.exists() or target.stat().st_size < 1024:
            target.unlink(missing_ok=True)
            return GeneratedImage(ok=False, url=url, prompt=prompt, error="download failed or image too small")
    except Exception as exc:
        target.unlink(missing_ok=True)
        return GeneratedImage(ok=False, url=url, prompt=prompt, error=f"download error: {str(exc)[:150]}")

    logger.info(f"[image_gen] saved {target} ({target.stat().st_size} bytes)")
    return GeneratedImage(ok=True, path=str(target), url=url, prompt=prompt)
