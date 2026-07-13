"""
tools/voice_input.py
Voice input for the CLI (feature iv from the user's expansion list): speak a
task instead of typing it.

Transcription backend: Groq Whisper (whisper-large-v3) — chosen over the
originally-requested Wispr Flow because it is FREE, needs no new signup (the
Groq key is already configured), and the connector already exists
(`whisper_large_v3` in the registry, GroqConnector._transcribe). Wispr Flow
does have a real developer API (api-docs.wisprflow.ai, WebSocket + REST) but
access appears sales-gated rather than self-serve, and its endpoint contract
was not verifiable without a key — implementing against a guessed contract
is exactly the fabrication mistake this project got burned by once (see
DECISIONS.md, C4). If/when a WISPR_API_KEY is obtained, implement the branch
in transcribe_file() against the real docs; until then a configured key
logs a warning and falls back to Groq rather than pretending.

Microphone capture uses `sounddevice` (optional dependency — a clear install
hint is shown if missing) and writes a 16 kHz mono WAV via the stdlib.
"""
from __future__ import annotations

import wave
from pathlib import Path

from loguru import logger

_SAMPLE_RATE = 16_000   # whisper models are trained on 16 kHz — higher adds size, not accuracy


async def transcribe_file(audio_path: str | Path) -> str:
    """Transcribe an audio file (wav/mp3/m4a/...) to text. Raises RuntimeError
    with a clear message when the file is missing or the backend fails."""
    p = Path(audio_path)
    if not p.exists():
        raise RuntimeError(f"Audio file not found: {p}")

    from config.settings import settings
    if getattr(settings, "wispr_api_key", ""):
        logger.warning(
            "[voice] WISPR_API_KEY is set but the Wispr Flow integration is not "
            "implemented (endpoint contract unverified without access — see module "
            "docstring). Falling back to Groq Whisper."
        )

    from models.registry import registry
    text = await registry.get("whisper_large_v3").generate(
        prompt="", audio_path=str(p)
    )
    return text.strip()


def record_microphone(seconds: int = 8, out_path: str | Path | None = None) -> Path:
    """Record from the default microphone into a 16 kHz mono WAV and return
    its path. Raises RuntimeError with an install hint if sounddevice is
    missing, so the CLI can show it instead of a traceback."""
    try:
        import sounddevice as sd
    except ImportError:
        raise RuntimeError(
            "Microphone capture needs the 'sounddevice' package:\n"
            "    pip install sounddevice\n"
            "(You can still transcribe existing audio files: /voice <path-to-file>)"
        )

    import numpy as np  # sounddevice depends on numpy, so it's present if sd imports

    out = Path(out_path) if out_path else Path.cwd() / ".vibeai_voice.wav"
    logger.info(f"[voice] recording {seconds}s from default microphone...")
    frames = sd.rec(int(seconds * _SAMPLE_RATE), samplerate=_SAMPLE_RATE,
                    channels=1, dtype="int16")
    sd.wait()
    if not np.any(frames):
        raise RuntimeError(
            "Recorded pure silence — check that a microphone is connected and "
            "not muted (Windows: Settings > Privacy > Microphone)."
        )
    with wave.open(str(out), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)      # int16
        wf.setframerate(_SAMPLE_RATE)
        wf.writeframes(frames.tobytes())
    logger.info(f"[voice] saved {out} ({out.stat().st_size} bytes)")
    return out
