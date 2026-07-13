"""
video_observer/analyzer.py
AI Analysis Engine — the real payoff of the .frames format.

Key insight: Feed motion vectors + structure, NOT raw pixels.
This is 100-1000x less data and often MORE informative for AI tasks.

What this module provides:
  1. MotionAnalyzer   — detects events from motion patterns
  2. TemporalSummary  — condenses a video to key moments
  3. AIFeaturePacket  — structured data ready for Qwen3 VL / InternVL3 / Llama 4
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterator

import numpy as np
import cv2

from video_observer.loader import FramesLoader, FrameData
from video_observer.format import FrameType


# ── Motion event types ────────────────────────────────────────────────────────

@dataclass
class MotionEvent:
    """A detected motion event in the video."""
    event_type: str          # "high_motion" | "scene_cut" | "static" | "pan" | "zoom"
    start_frame: int
    end_frame: int
    start_ms: int
    end_ms: int
    intensity: float         # 0-1 normalised motion magnitude
    description: str = ""
    metadata: dict = field(default_factory=dict)

    @property
    def duration_ms(self) -> int:
        return self.end_ms - self.start_ms


@dataclass
class AIFeaturePacket:
    """
    Structured data packet fed to AI models instead of raw video.
    ~1000x smaller than raw frames; contains what AI actually needs.
    """
    video_duration_ms:   int
    total_frames:        int
    fps:                 float
    motion_events:       list[MotionEvent]
    key_frames:          list[np.ndarray]     # thumbnails at key moments
    key_frame_times_ms:  list[int]
    motion_timeline:     np.ndarray           # (T,) normalised motion per frame
    activity_regions:    list[dict]           # {frame_idx, bbox, intensity}
    summary_text:        str                  # human-readable summary

    def to_prompt_context(self) -> str:
        """
        Convert to a text context block that AI models can read.
        Used instead of feeding raw frames — much cheaper to tokenize.
        """
        lines = [
            f"Video: {self.total_frames} frames at {self.fps:.1f}fps "
            f"({self.video_duration_ms/1000:.1f}s total)",
            "",
            "Motion events detected:",
        ]
        for ev in self.motion_events:
            lines.append(
                f"  [{ev.start_ms/1000:.2f}s – {ev.end_ms/1000:.2f}s] "
                f"{ev.event_type} (intensity={ev.intensity:.2f}) — {ev.description}"
            )
        lines.append("")
        lines.append(self.summary_text)
        return "\n".join(lines)


# ── Motion analyzer ───────────────────────────────────────────────────────────

class MotionAnalyzer:
    """
    Analyzes motion vectors from .frames file to detect events.
    Never decodes pixel data for analysis — only uses motion vectors.

    This directly addresses the user's Phase 4 goal:
        if object.velocity > threshold: print("Running detected")
    """

    def __init__(
        self,
        high_motion_threshold: float = 5.0,   # pixels/frame average
        scene_cut_threshold:   float = 0.8,   # fraction of pixels changing
        static_threshold:      float = 0.5,   # below this = static
        smoothing_window:      int   = 5,
    ) -> None:
        self.high_motion_t  = high_motion_threshold
        self.scene_cut_t    = scene_cut_threshold
        self.static_t       = static_threshold
        self.smooth_win     = smoothing_window

    def analyze(self, loader: FramesLoader) -> list[MotionEvent]:
        """Analyze entire video and return list of motion events."""
        motion_per_frame = self._compute_motion_timeline(loader)
        smoothed = self._smooth(motion_per_frame, self.smooth_win)
        events   = self._detect_events(smoothed, loader)
        return events

    def _compute_motion_timeline(self, loader: FramesLoader) -> np.ndarray:
        """
        Compute mean motion magnitude per frame.
        Uses ONLY motion vectors — no pixel decoding.
        Extremely fast.
        """
        n = loader.header.total_frames  # type: ignore
        timeline = np.zeros(n, dtype=np.float32)

        for i in range(n):
            entry = loader.index[i]
            if entry.frame_type == FrameType.DELTA:
                mvs = loader.get_motion_vectors(i)
                if len(mvs) > 0:
                    mags = np.sqrt(mvs[:, 2]**2 + mvs[:, 3]**2)
                    timeline[i] = float(mags.mean())

        return timeline

    def _smooth(self, signal: np.ndarray, window: int) -> np.ndarray:
        """Simple moving average smoothing."""
        kernel = np.ones(window) / window
        return np.convolve(signal, kernel, mode="same")

    def _detect_events(
        self, motion: np.ndarray, loader: FramesLoader
    ) -> list[MotionEvent]:
        """Rule-based event detection on motion timeline."""
        events: list[MotionEvent] = []
        n = len(motion)
        i = 0

        while i < n:
            mag = motion[i]
            ts  = loader.index[i].timestamp_ms

            # Scene cut: sudden spike
            if i > 0 and mag > motion[i-1] * 3 and mag > self.high_motion_t * 2:
                events.append(MotionEvent(
                    event_type="scene_cut",
                    start_frame=i, end_frame=i,
                    start_ms=ts, end_ms=ts,
                    intensity=min(mag / (self.high_motion_t * 4), 1.0),
                    description="Sudden large motion change — possible scene transition",
                ))
                i += 1
                continue

            # High motion segment
            if mag > self.high_motion_t:
                start = i
                while i < n and motion[i] > self.high_motion_t:
                    i += 1
                end = i - 1
                avg_intensity = float(motion[start:end+1].mean())
                events.append(MotionEvent(
                    event_type="high_motion",
                    start_frame=start, end_frame=end,
                    start_ms=loader.index[start].timestamp_ms,
                    end_ms=loader.index[end].timestamp_ms,
                    intensity=min(avg_intensity / (self.high_motion_t * 3), 1.0),
                    description=f"Active motion for {(end-start)} frames",
                ))
                continue

            # Static segment
            if mag < self.static_t:
                start = i
                while i < n and motion[i] < self.static_t:
                    i += 1
                end = i - 1
                if end - start > 10:   # ignore very short static segments
                    events.append(MotionEvent(
                        event_type="static",
                        start_frame=start, end_frame=end,
                        start_ms=loader.index[start].timestamp_ms,
                        end_ms=loader.index[end].timestamp_ms,
                        intensity=0.0,
                        description=f"No motion for {(end-start)} frames",
                    ))
                continue

            i += 1

        return events


# ── Temporal summarizer ───────────────────────────────────────────────────────

class TemporalSummarizer:
    """
    Selects the most informative key frames from a video.
    Used to give AI models a compact but representative set of thumbnails.
    """

    def __init__(self, max_keyframes: int = 10, thumbnail_size: tuple = (320, 180)) -> None:
        self.max_kf       = max_keyframes
        self.thumb_size   = thumbnail_size

    def summarize(self, loader: FramesLoader) -> AIFeaturePacket:
        """
        Build an AIFeaturePacket from the video.
        Selects key frames at motion peaks + scene cuts.
        """
        analyzer = MotionAnalyzer()
        events   = analyzer.analyze(loader)
        motion_tl = analyzer._compute_motion_timeline(loader)

        # Select key frame indices: peaks in motion + scene cuts
        key_indices = self._select_key_frames(events, motion_tl, loader.header.total_frames)

        key_frames    = []
        key_times_ms  = []
        for idx in key_indices:
            fd = loader.get_frame(idx, reconstruct=True)
            thumb = cv2.resize(fd.frame, self.thumb_size)
            key_frames.append(thumb)
            key_times_ms.append(fd.timestamp_ms)

        activity = self._find_activity_regions(loader, key_indices)
        summary  = self._generate_summary(events, loader.header)

        return AIFeaturePacket(
            video_duration_ms=loader.index[-1].timestamp_ms if loader.index else 0,
            total_frames=loader.header.total_frames,
            fps=loader.header.fps,
            motion_events=events,
            key_frames=key_frames,
            key_frame_times_ms=key_times_ms,
            motion_timeline=motion_tl,
            activity_regions=activity,
            summary_text=summary,
        )

    def _select_key_frames(
        self, events: list[MotionEvent], motion: np.ndarray, total: int
    ) -> list[int]:
        candidates: set[int] = set()

        # Always include first and last
        candidates.add(0)
        candidates.add(total - 1)

        # Include frame at each event boundary
        for ev in events:
            candidates.add(ev.start_frame)
            mid = (ev.start_frame + ev.end_frame) // 2
            candidates.add(mid)

        # If still under budget, add motion peaks
        if len(candidates) < self.max_kf:
            peak_count = self.max_kf - len(candidates)
            peak_indices = np.argsort(motion)[-peak_count:]
            candidates.update(int(i) for i in peak_indices)

        # Sort and limit
        sorted_idxs = sorted(candidates)
        step = max(1, len(sorted_idxs) // self.max_kf)
        return sorted_idxs[::step][:self.max_kf]

    def _find_activity_regions(
        self, loader: FramesLoader, key_indices: list[int]
    ) -> list[dict]:
        regions = []
        for idx in key_indices:
            mvs = loader.get_motion_vectors(idx)
            if len(mvs) == 0:
                continue
            mags = np.sqrt(mvs[:, 2]**2 + mvs[:, 3]**2)
            if mags.max() < 1.0:
                continue
            hot_idx  = np.argmax(mags)
            regions.append({
                "frame_idx": idx,
                "x": float(mvs[hot_idx, 0]),
                "y": float(mvs[hot_idx, 1]),
                "intensity": float(mags[hot_idx]),
                "timestamp_ms": loader.index[idx].timestamp_ms,
            })
        return regions

    def _generate_summary(self, events: list[MotionEvent], header) -> str:
        n_cuts     = sum(1 for e in events if e.event_type == "scene_cut")
        n_active   = sum(1 for e in events if e.event_type == "high_motion")
        n_static   = sum(1 for e in events if e.event_type == "static")
        total_s    = (header.total_frames / max(header.fps, 1))

        return (
            f"Video is {total_s:.1f}s at {header.fps:.0f}fps "
            f"({header.width}×{header.height}). "
            f"Detected {n_cuts} scene cuts, {n_active} active motion segments, "
            f"{n_static} static segments. "
            f"Use the key frames and motion events above for detailed analysis."
        )
