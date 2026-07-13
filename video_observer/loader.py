"""
video_observer/loader.py
.frames → AI-ready data.

Key design decisions for speed (CH2):
  - Memory-mapped file: zero-copy reads, OS handles caching
  - Lazy frame decoding: only decompress what the AI requests
  - Streaming iterator: never loads whole video into RAM
  - Structured output: AI gets motion vectors + frame tensors, not just pixels
"""
from __future__ import annotations

import mmap
import numpy as np
import cv2
import zstandard as zstd
from pathlib import Path
from typing import Iterator, NamedTuple

from video_observer.format import (
    FramesHeader, FrameIndexEntry, FrameType,
    HEADER_SIZE, INDEX_ENTRY_SIZE,
    read_header, read_index,
)


# ── AI-ready frame output ─────────────────────────────────────────────────────

class FrameData(NamedTuple):
    """
    What the AI models actually need — not raw pixels.
    Motion vectors are the primary signal; the frame is secondary.
    """
    index:          int               # frame number
    timestamp_ms:   int               # position in video
    frame_type:     FrameType
    frame:          np.ndarray        # decoded image (H×W or H×W×C)
    motion_vectors: np.ndarray        # shape (N, 4): [x, y, dx, dy] or (0,4) if none
    motion_magnitude: np.ndarray | None  # H×W float32 heatmap of motion intensity
    is_reconstructed: bool           # True = built from keyframe + delta chain


# ── Loader ────────────────────────────────────────────────────────────────────

class FramesLoader:
    """
    Efficient loader for .frames files.

    Usage:
        with FramesLoader("video.frames") as loader:
            print(loader.header)
            for frame_data in loader.stream():
                # feed frame_data to AI
                pass

        # Or random access:
        with FramesLoader("video.frames") as loader:
            fd = loader.get_frame(150)
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._file = None
        self._mmap: mmap.mmap | None = None
        self._decompressor = zstd.ZstdDecompressor()
        self.header: FramesHeader | None = None
        self.index: list[FrameIndexEntry] = []
        self._last_keyframe: np.ndarray | None = None
        self._last_keyframe_idx: int = -1

    def __enter__(self) -> "FramesLoader":
        self._file = open(self._path, "rb")
        self._mmap = mmap.mmap(self._file.fileno(), 0, access=mmap.ACCESS_READ)
        self.header = read_header(self._file)
        self.index  = read_index(self._file, self.header)
        return self

    def __exit__(self, *_) -> None:
        if self._mmap:
            self._mmap.close()
        if self._file:
            self._file.close()

    # ── Public API ────────────────────────────────────────────────────

    def stream(
        self,
        start: int = 0,
        end: int | None = None,
        reconstruct: bool = True,
    ) -> Iterator[FrameData]:
        """
        Iterate over frames in order.
        If reconstruct=True: delta frames are rebuilt as full images.
        If reconstruct=False: delta frames return only motion vectors.
        """
        end = end or self.header.total_frames  # type: ignore
        for i in range(start, min(end, len(self.index))):
            yield self._decode_frame(i, reconstruct=reconstruct)

    def get_frame(self, idx: int, reconstruct: bool = True) -> FrameData:
        """Random access to any frame."""
        if idx < 0 or idx >= len(self.index):
            raise IndexError(f"Frame {idx} out of range [0, {len(self.index)})")
        return self._decode_frame(idx, reconstruct=reconstruct)

    def get_motion_vectors(self, idx: int) -> np.ndarray:
        """Fast path: only decode motion vectors, skip image reconstruction."""
        entry = self.index[idx]
        if entry.frame_type != FrameType.DELTA or entry.motion_size == 0:
            return np.zeros((0, 4), dtype=np.float32)
        raw = self._read_blob(entry.motion_offset, entry.motion_size)
        flat = np.frombuffer(raw, dtype=np.float32)
        if len(flat) % 4 != 0:
            return np.zeros((0, 4), dtype=np.float32)
        return flat.reshape(-1, 4)

    def seek_to_time(self, time_ms: int) -> int:
        """Binary search for the frame closest to time_ms. Returns frame index."""
        lo, hi = 0, len(self.index) - 1
        while lo < hi:
            mid = (lo + hi) // 2
            if self.index[mid].timestamp_ms < time_ms:
                lo = mid + 1
            else:
                hi = mid
        return lo

    # ── Internal decoding ─────────────────────────────────────────────

    def _decode_frame(self, idx: int, reconstruct: bool) -> FrameData:
        entry = self.index[idx]
        h, w  = self.header.height, self.header.width  # type: ignore

        if entry.frame_type == FrameType.KEYFRAME:
            frame = self._decode_keyframe(entry, h, w)
            self._last_keyframe     = frame.copy()
            self._last_keyframe_idx = idx
            return FrameData(
                index=idx, timestamp_ms=entry.timestamp_ms,
                frame_type=FrameType.KEYFRAME,
                frame=frame,
                motion_vectors=np.zeros((0, 4), dtype=np.float32),
                motion_magnitude=None,
                is_reconstructed=False,
            )

        elif entry.frame_type == FrameType.SKIP:
            # Return the cached previous frame
            base = self._last_keyframe if self._last_keyframe is not None \
                   else np.zeros((h, w, 3), dtype=np.uint8)
            return FrameData(
                index=idx, timestamp_ms=entry.timestamp_ms,
                frame_type=FrameType.SKIP,
                frame=base,
                motion_vectors=np.zeros((0, 4), dtype=np.float32),
                motion_magnitude=None,
                is_reconstructed=True,
            )

        else:   # DELTA
            mvs = self.get_motion_vectors(idx)
            mag = self._motion_heatmap(mvs, h, w) if len(mvs) > 0 else None

            if reconstruct and self._last_keyframe is not None:
                frame = self._reconstruct_delta(entry, h, w)
            else:
                frame = self._last_keyframe if self._last_keyframe is not None \
                        else np.zeros((h, w, 3), dtype=np.uint8)

            return FrameData(
                index=idx, timestamp_ms=entry.timestamp_ms,
                frame_type=FrameType.DELTA,
                frame=frame,
                motion_vectors=mvs,
                motion_magnitude=mag,
                is_reconstructed=reconstruct,
            )

    def _decode_keyframe(self, entry: FrameIndexEntry, h: int, w: int) -> np.ndarray:
        if entry.data_size == 0:
            return np.zeros((h, w, 3), dtype=np.uint8)
        raw = self._read_blob(entry.data_offset, entry.data_size)
        jpeg_bytes = self._decompressor.decompress(raw)
        arr = np.frombuffer(jpeg_bytes, dtype=np.uint8)
        frame = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if frame is None:
            return np.zeros((h, w, 3), dtype=np.uint8)
        return frame

    def _reconstruct_delta(self, entry: FrameIndexEntry, h: int, w: int) -> np.ndarray:
        """Rebuild full frame from last keyframe + residual diff."""
        if entry.data_size == 0 or self._last_keyframe is None:
            return self._last_keyframe or np.zeros((h, w, 3), dtype=np.uint8)

        raw_res = self._read_blob(entry.data_offset, entry.data_size)
        residual_bytes = self._decompressor.decompress(raw_res)
        residual = np.frombuffer(residual_bytes, dtype=np.uint8).reshape(h, w)

        base = cv2.cvtColor(self._last_keyframe, cv2.COLOR_BGR2GRAY)
        delta = residual.astype(np.int16) - 127
        reconstructed_gray = np.clip(
            base.astype(np.int16) + delta, 0, 255
        ).astype(np.uint8)

        return cv2.cvtColor(reconstructed_gray, cv2.COLOR_GRAY2BGR)

    def _read_blob(self, offset: int, size: int) -> bytes:
        """Zero-copy read from memory-mapped file."""
        return self._mmap[offset: offset + size]  # type: ignore

    @staticmethod
    def _motion_heatmap(mvs: np.ndarray, h: int, w: int) -> np.ndarray:
        """Convert sparse motion vectors to a dense H×W magnitude heatmap."""
        heatmap = np.zeros((h, w), dtype=np.float32)
        if len(mvs) == 0:
            return heatmap
        xs = np.clip(mvs[:, 0].astype(int), 0, w - 1)
        ys = np.clip(mvs[:, 1].astype(int), 0, h - 1)
        mags = np.sqrt(mvs[:, 2] ** 2 + mvs[:, 3] ** 2)
        np.add.at(heatmap, (ys, xs), mags)
        # Smooth the sparse heatmap
        return cv2.GaussianBlur(heatmap, (21, 21), 5.0)
