"""
teams/vision.py
Vision Team — the full integration of video_observer into VibeAI.

Pipeline for video analysis:
  1. Accept video file path or URL from task payload
  2. Convert video → .frames  (FramesConverter)
  3. Analyze .frames → AIFeaturePacket  (TemporalSummarizer)
  4. Feed key frames + motion context to vision models
  5. Whisper: transcribe audio track if present
  6. Synthesise all model outputs → return to manager

For screenshot / image analysis (no video):
  Skip steps 2-3, feed image directly to vision models.

Models in this team:
  1. gemini_flash_vision    — UI screenshot critic + GUI automation
  2. nemotron_vl    — Long video frame analysis
  3. gemini_20_flash_ocr   — OCR and document understanding
  4. llama4_maverick  — Visual debugging from screen recordings
  5. whisper_large_v3 — Audio transcription from video

Optimisations active:
  • Synthesis pass — multi-model outputs merged into one coherent answer
  • Dynamic frame count — 4/8/12/16 frames scaled by video duration
  • Image pipeline fallback — if UI models fail, video models handle the image
"""
from __future__ import annotations

import asyncio
import base64
import os
import tempfile
from pathlib import Path
from typing import Any

from loguru import logger

from core.imcp import TaskJSON
from models.registry import generate_resilient
from teams.base_team import BaseTeam

try:
    import cv2
    import numpy as np
    from video_observer.converter import FramesConverter, ConvertConfig
    from video_observer.loader import FramesLoader
    from video_observer.analyzer import TemporalSummarizer, AIFeaturePacket, MotionAnalyzer
    _VIDEO_DEPS_OK = True
except ImportError as _e:
    _VIDEO_DEPS_OK = False
    _VIDEO_DEPS_ERR = (
        f"Video analysis unavailable — missing dependency: {_e}. "
        "Install with: pip install opencv-python numpy zstandard"
    )


# ── Dynamic frame count based on video duration ───────────────────────────────

def _frame_count(duration_s: float) -> int:
    """More frames for longer videos, capped to avoid token overflow."""
    if duration_s <= 30:
        return 4
    if duration_s <= 120:
        return 8
    if duration_s <= 300:
        return 12
    return 16   # > 5 minutes


# ── Vision system prompts per model ──────────────────────────────────────────

_SYSTEM_PROMPTS = {
    "gemini_flash_vision": """You are a visual analysis specialist in VibeAI.
For VIDEO FRAMES: describe what is happening over time — scene content, actions,
people, objects, motion, and any notable events across the key frames provided.
For SCREENSHOTS/UI: analyse layout, alignment, visual hierarchy, spacing, contrast,
visible errors, and accessibility concerns. Mention exact elements and severity.
Be specific, structured, and thorough.""",

    "nemotron_vl": """You are a video analysis specialist in VibeAI.
You receive key frames from a video alongside motion event data.
Your job: understand what is happening over time.
Focus on: what changed between frames, actions being performed,
UI state transitions, bugs or unexpected behavior you observe.""",

    "gemini_20_flash_ocr": """You are a detailed visual analyst in VibeAI.
For VIDEO FRAMES: describe the scene, content, people, objects, text visible,
and what is occurring in the sequence of frames. Note any changes between frames.
For SCREENSHOTS/DOCUMENTS: extract all visible text with full accuracy —
code snippets, error messages, labels, UI text, stack traces.
Preserve exact formatting of code and error text.""",

    "llama4_maverick": """You are a visual debugger in VibeAI.
You analyse screenshots and screen recordings to find bugs.
Look for: error dialogs, broken layouts, unexpected states,
console errors, misaligned elements, performance issues.
Report: what is broken, where it is, what likely caused it.""",
}


class VisionTeam(BaseTeam):
    """
    Vision team with full .frames video pipeline integration.
    """
    team_name = "vision"

    # Models used in order of task type
    # Primary video models: Gemini 2.5/2.0 Flash via Google connector (confirmed image-input support).
    # nemotron_vl / llama4_maverick remain in the registry as generate_resilient fallbacks
    # but free-tier OpenRouter endpoints strip image payloads — don't use as primaries.
    _UI_MODELS     = ["gemini_flash_vision", "gemini_20_flash_ocr"]
    _VIDEO_MODELS  = ["gemini_flash_vision", "gemini_20_flash_ocr"]
    _AUDIO_MODEL   = "whisper_large_v3"

    async def _execute(
        self,
        task_json: TaskJSON,
        instruction: str,
        iteration: int,
        extra: dict[str, Any],
    ) -> str:
        """
        Route to video pipeline or image pipeline based on what's in the payload.
        """
        video_path  = extra.get("video_path")
        frames_path = extra.get("frames_path")
        image_b64   = extra.get("image_b64")      # base64 PNG/JPEG
        image_url   = extra.get("image_url")

        if video_path or frames_path:
            if not _VIDEO_DEPS_OK:
                logger.error(f"[vision] {_VIDEO_DEPS_ERR}")
                return f"[ERROR] {_VIDEO_DEPS_ERR}"
            return await self._run_video_pipeline(
                task_json, instruction, video_path, frames_path
            )
        elif image_b64 or image_url:
            return await self._run_image_pipeline(
                task_json, instruction, image_b64, image_url
            )
        else:
            # No media provided — text-only visual reasoning
            return await self._run_text_only(task_json, instruction)

    # ── Video pipeline ────────────────────────────────────────────────

    async def _run_video_pipeline(
        self,
        task_json: TaskJSON,
        instruction: str,
        video_path: str | None,
        frames_path: str | None,
    ) -> str:
        """
        Full video → .frames → AIFeaturePacket → model analysis pipeline.
        """
        logger.info("[vision] video pipeline starting")

        # ── Step 1: Convert to .frames if not already done ────────────
        duration = await self._get_video_duration(video_path or "")
        n_frames = _frame_count(duration)

        if not frames_path:
            frames_path = await self._convert_to_frames(video_path, duration)
            logger.info(f"[vision] converted to .frames: {frames_path} (duration={duration:.0f}s)")

        # ── Step 2: Analyze .frames → AIFeaturePacket ─────────────────
        # Falls back to direct OpenCV extraction when the .frames binary
        # has a buffer alignment issue (e.g. numpy dtype mismatch on read).
        packet = None
        try:
            packet = await asyncio.get_event_loop().run_in_executor(
                None, self._analyze_frames, frames_path, n_frames
            )
            logger.info(
                f"[vision] analysis done | "
                f"events={len(packet.motion_events)} | "
                f"keyframes={len(packet.key_frames)}"
            )
        except Exception as exc:
            logger.warning(
                f"[vision] .frames analysis failed ({exc!r}) — "
                "falling back to direct OpenCV frame extraction"
            )
            if video_path:
                packet = await self._extract_frames_cv2(video_path, n_frames)

        if packet is None or not getattr(packet, "key_frames", None):
            return (
                "VIDEO ANALYSIS FAILED: Could not load frames. "
                "The .frames file may be corrupted — try re-running, "
                "or ensure opencv-python and zstandard are installed."
            )

        # ── Step 3: Run vision models + Whisper concurrently ──────────
        tasks = [
            self._analyze_with_motion_context(
                model_id="nemotron_vl",
                packet=packet,
                instruction=instruction,
                task_json=task_json,
            ),
            self._analyze_with_motion_context(
                model_id="llama4_maverick",
                packet=packet,
                instruction=instruction,
                task_json=task_json,
            ),
            self._transcribe_audio(video_path or ""),
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        vision_results = results[:2]
        transcript     = results[2] if not isinstance(results[2], Exception) else ""

        outputs = []
        for model_id, result in zip(self._VIDEO_MODELS, vision_results):
            if isinstance(result, Exception):
                logger.warning(f"[vision] {model_id} failed: {result}")
            else:
                outputs.append(result)

        if not outputs:
            return (
                "VIDEO ANALYSIS FAILED: all vision models returned errors. "
                "Check that GEMINI_API_KEY is set (free at https://aistudio.google.com/apikey) "
                "and that opencv-python is installed."
            )

        # ── Step 4: Synthesise all outputs into one coherent answer ───
        motion_ctx  = packet.to_prompt_context()
        audio_block = f"\nAUDIO TRANSCRIPT:\n{transcript}\n" if transcript else ""
        context     = f"MOTION ANALYSIS:\n{motion_ctx}\n{audio_block}\n"

        return await self._synthesize(
            outputs, instruction, context=context, media_type="video"
        )

    async def _get_video_duration(self, video_path: str) -> float:
        """Use ffprobe to get video duration. Returns 60.0 on failure."""
        if not video_path or not os.path.exists(video_path):
            return 60.0
        try:
            import json
            proc = await asyncio.create_subprocess_exec(
                "ffprobe", "-v", "quiet", "-print_format", "json",
                "-show_streams", video_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
            data = json.loads(out)
            for stream in data.get("streams", []):
                dur = float(stream.get("duration", 0))
                if dur > 0:
                    return dur
        except Exception:
            pass
        return 60.0

    async def _convert_to_frames(self, video_path: str, duration_s: float = 60.0) -> str:
        """Convert video file to .frames format. Runs in a thread executor."""
        cfg = ConvertConfig(
            target_width=640,
            target_height=360,
            grayscale=False,
            keyframe_interval=10,
        )
        converter   = FramesConverter(cfg)
        frames_path = video_path.rsplit(".", 1)[0] + ".frames"
        stats = await asyncio.get_event_loop().run_in_executor(
            None, converter.convert, video_path, frames_path
        )
        logger.info(
            f"[vision] .frames stats: ratio={stats['compression_ratio']}× | "
            f"keyframes={stats['keyframes']} | "
            f"size={stats['output_size_mb']}MB"
        )
        return frames_path

    def _analyze_frames(self, frames_path: str, max_keyframes: int = 8) -> AIFeaturePacket:
        """Load .frames and extract AIFeaturePacket. Blocking — run in executor."""
        with FramesLoader(frames_path) as loader:
            summarizer = TemporalSummarizer(
                max_keyframes=max_keyframes,
                thumbnail_size=(320, 180),
            )
            return summarizer.summarize(loader)

    async def _extract_frames_cv2(self, video_path: str, n_frames: int):
        """
        Fallback: extract N evenly-spaced frames directly from the original
        video using cv2.VideoCapture. Bypasses video_observer entirely.
        Used when .frames loading throws a numpy buffer alignment error.
        """
        from dataclasses import dataclass, field as _field

        @dataclass
        class _DirectPacket:
            key_frames:    list
            motion_events: list = _field(default_factory=list)
            _count:        int  = 0

            def to_prompt_context(self) -> str:
                return (
                    f"Direct frame extraction: {self._count} key frames "
                    f"sampled evenly from the video. "
                    f"(Note: motion-vector analysis was skipped — .frames "
                    f"load failed due to buffer alignment; frames taken "
                    f"straight from the original video via OpenCV.)"
                )

        def _do_extract():
            cap   = cv2.VideoCapture(video_path)
            total = max(1, int(cap.get(cv2.CAP_PROP_FRAME_COUNT)))
            step  = max(1, total // n_frames)
            frames: list = []
            for i in range(n_frames):
                cap.set(cv2.CAP_PROP_POS_FRAMES, i * step)
                ok, frame = cap.read()
                if ok and frame is not None and frame.size > 0:
                    # Resize to a standard thumbnail size the vision models expect
                    frame = cv2.resize(frame, (640, 360))
                    frames.append(frame)
            cap.release()
            return _DirectPacket(key_frames=frames, _count=len(frames))

        try:
            logger.info(
                f"[vision] cv2 fallback: sampling {n_frames} frames "
                f"from {os.path.basename(video_path)}"
            )
            packet = await asyncio.get_event_loop().run_in_executor(None, _do_extract)
            logger.info(
                f"[vision] cv2 fallback: extracted {len(packet.key_frames)} frames — "
                "proceeding to vision models"
            )
            return packet
        except Exception as exc:
            logger.error(f"[vision] cv2 fallback also failed: {exc}")
            return None

    async def _analyze_with_motion_context(
        self,
        model_id: str,
        packet: AIFeaturePacket,
        instruction: str,
        task_json: TaskJSON,
    ) -> str:
        """
        Feed key frames + motion context to a vision model.
        Motion context replaces the need to process every frame.
        """
        # Convert key frames to base64 (dynamic count already set in packet)
        images_b64 = []
        for kf in packet.key_frames:
            ok, buf = cv2.imencode(".jpg", kf, [cv2.IMWRITE_JPEG_QUALITY, 85])
            if ok:
                images_b64.append(base64.b64encode(buf.tobytes()).decode())

        # Combine instruction with motion context
        prompt = (
            f"{instruction}\n\n"
            f"VIDEO MOTION CONTEXT (pre-analyzed from .frames format):\n"
            f"{packet.to_prompt_context()}\n\n"
            f"Key frames are attached. Analyze based on both the motion data "
            f"and the visual content of the key frames."
        )

        return await generate_resilient(
            model_id,
            prompt=prompt,
            system=_SYSTEM_PROMPTS.get(model_id, ""),
            images=images_b64,
            max_tokens=2000,
            temperature=0.3,
        )

    async def _transcribe_audio(self, video_path: str) -> str:
        """Extract audio from video with ffmpeg and transcribe via Groq Whisper."""
        if not video_path or not os.path.exists(video_path):
            return ""
        import tempfile
        import subprocess as _sp
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as _tmp:
            audio_path = _tmp.name
        try:
            proc = await asyncio.create_subprocess_exec(
                "ffmpeg", "-i", video_path, "-vn", "-acodec", "mp3",
                "-ab", "64k", "-y", audio_path,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await asyncio.wait_for(proc.communicate(), timeout=60)
            if proc.returncode != 0 or not os.path.exists(audio_path):
                return ""
            from config.settings import settings
            import groq as _groq
            client = _groq.AsyncGroq(api_key=settings.groq_api_key)
            logger.info("[vision] transcribing audio via Whisper Large v3")
            with open(audio_path, "rb") as f:
                resp = await client.audio.transcriptions.create(
                    file=(os.path.basename(audio_path), f.read()),
                    model="whisper-large-v3",
                )
            transcript = getattr(resp, "text", "") or ""
            logger.info(f"[vision] audio transcript: {len(transcript)} chars")
            return transcript
        except FileNotFoundError:
            logger.debug("[vision] ffmpeg not found — audio transcription skipped")
            return ""
        except Exception as exc:
            logger.warning(f"[vision] audio transcription failed: {exc}")
            return ""
        finally:
            try:
                os.unlink(audio_path)
            except Exception:
                pass

    # ── Image pipeline ────────────────────────────────────────────────

    async def _run_image_pipeline(
        self,
        task_json: TaskJSON,
        instruction: str,
        image_b64: str | None,
        image_url: str | None,
    ) -> str:
        """
        Process a single screenshot or image through UI-focused models.
        """
        logger.info("[vision] image pipeline starting")

        images = []
        if image_url:
            images.append(image_url)
        if image_b64:
            images.append(image_b64)

        tasks = [
            generate_resilient(
                model_id,
                prompt=instruction or "Analyse this screenshot in detail.",
                system=_SYSTEM_PROMPTS.get(model_id, ""),
                images=images,
                max_tokens=1500,
                temperature=0.3,
            )
            for model_id in self._UI_MODELS
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        outputs = []
        failed  = 0
        for model_id, result in zip(self._UI_MODELS, results):
            if isinstance(result, Exception):
                logger.warning(f"[vision] {model_id} failed in image pipeline: {result}")
                failed += 1
            else:
                outputs.append(result)

        # Fallback to video models when all UI models are down
        if failed == len(self._UI_MODELS) or not outputs:
            logger.warning("[vision] all UI models failed — falling back to video models for image")
            fallback = await asyncio.gather(
                *(
                    generate_resilient(
                        m,
                        prompt=instruction or "Analyse this image in detail.",
                        system=_SYSTEM_PROMPTS.get(m, ""),
                        images=images,
                        max_tokens=1500,
                        temperature=0.3,
                    )
                    for m in self._VIDEO_MODELS
                ),
                return_exceptions=True,
            )
            outputs = [r for r in fallback if not isinstance(r, Exception) and r]

        if not outputs:
            return "IMAGE ANALYSIS FAILED: all vision models unavailable."

        return await self._synthesize(outputs, instruction, media_type="image")

    # ── Synthesis pass ─────────────────────────────────────────────────

    async def _synthesize(
        self,
        outputs: list[str],
        instruction: str,
        context: str = "",
        media_type: str = "image",
    ) -> str:
        """
        Merge multi-model visual analyses into ONE coherent answer.
        Without this step, the manager receives raw '=== model ===...' blocks
        and has to figure out contradictions itself. This pre-merges them.
        Uses a fast text model (no vision needed for the synthesis step).
        """
        if len(outputs) == 1:
            return outputs[0]

        logger.info(f"[vision] synthesising {len(outputs)} model analyses")
        combined = "\n\n".join(
            f"[Analyst {i+1}]:\n{o}" for i, o in enumerate(outputs)
        )
        try:
            return await generate_resilient(
                "glm_47_cerebras",
                prompt=(
                    f"TASK: {instruction}\n\n"
                    f"{context}"
                    f"ANALYSES FROM {len(outputs)} SPECIALIST MODELS:\n{combined}\n\n"
                    f"Merge these into ONE clear, structured {media_type} analysis. "
                    f"Remove duplication, resolve contradictions, surface key findings."
                ),
                system=(
                    "You are a synthesis specialist in VibeAI. Merge multiple visual "
                    "analyses into one clear, accurate report. Be specific and concise."
                ),
                max_tokens=1500,
                temperature=0.2,
            )
        except Exception as exc:
            logger.warning(f"[vision] synthesis failed ({exc}) — returning merged raw output")
            header = "VIDEO ANALYSIS RESULTS" if media_type == "video" else "IMAGE ANALYSIS RESULTS"
            return header + "\n" + "="*50 + "\n\n" + "\n\n---\n\n".join(outputs)

    # ── Text-only fallback ────────────────────────────────────────────

    async def _run_text_only(self, task_json: TaskJSON, instruction: str) -> str:
        return await generate_resilient(
            "gemini_flash_vision",
            prompt=instruction or task_json.refined_prompt,
            system=_SYSTEM_PROMPTS["gemini_flash_vision"],
            max_tokens=1500,
        )


# ── Convenience helper for the manager ───────────────────────────────────────

async def analyze_video(
    video_path: str,
    task_json: TaskJSON,
    instruction: str = "",
) -> str:
    """
    One-call integration: video file → full vision analysis.
    Used by the manager when a video is attached to a request.
    """
    team = VisionTeam()
    return await team.run(
        task_json=task_json,
        instruction=instruction,
        extra={"video_path": video_path},
    )


async def analyze_frames_file(
    frames_path: str,
    task_json: TaskJSON,
    instruction: str = "",
) -> str:
    """
    One-call integration: pre-converted .frames file → full vision analysis.
    Skips the conversion step.
    """
    team = VisionTeam()
    return await team.run(
        task_json=task_json,
        instruction=instruction,
        extra={"frames_path": frames_path},
    )
