"""
video_observer/format.py
.frames binary format specification v1.

Layout:
┌─────────────────────────────────────────────────────┐
│  HEADER   (64 bytes, fixed)                          │
│  FRAME INDEX  (N × 18 bytes, fixed per frame)        │
│  FRAME DATA   (variable, frame blobs packed end-end)  │
└─────────────────────────────────────────────────────┘

Why binary over JSON:
  - JSON: ~10µs to parse one frame entry → unacceptable at 60fps
  - struct: ~0.1µs → 100x faster
  - Memory-mapped access: zero-copy reads for the loader
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from enum import IntEnum
from typing import BinaryIO


# ── Constants ─────────────────────────────────────────────────────────────────

MAGIC = b"FRMS"          # 4-byte file identifier
FORMAT_VERSION = 1

# Frame types
class FrameType(IntEnum):
    KEYFRAME = 0    # full compressed image (JPEG-in-zstd)
    DELTA    = 1    # motion vectors + residual diff
    SKIP     = 2    # frame identical to previous (just a marker, 0 bytes data)

# Compression modes for frame data
class Compression(IntEnum):
    NONE  = 0
    ZSTD  = 1    # best ratio; use for keyframes
    LZ4   = 2    # fastest; use for residuals

# Channel modes
class Channels(IntEnum):
    GRAY  = 1
    RGB   = 3


# ── Struct formats (big-endian / network order for portability) ───────────────

#   magic(4s) version(B) fps(f) width(H) height(H)
#   total_frames(I) keyframe_interval(B) compression(B)
#   channels(B) quality(B) index_offset(Q) reserved(30s)
HEADER_FMT   = "!4sBfHHIBBBBQ35s"
HEADER_SIZE  = struct.calcsize(HEADER_FMT)   # 64 bytes

#   frame_type(B) data_offset(Q) data_size(I) timestamp_ms(I)
#   motion_offset(Q) motion_size(I) flags(B) ← flags reserved for future
INDEX_ENTRY_FMT  = "!BQIIQIB"
INDEX_ENTRY_SIZE = struct.calcsize(INDEX_ENTRY_FMT)   # 27 bytes → padded to 28

assert HEADER_SIZE == 64, f"Header must be 64 bytes, got {HEADER_SIZE}"


# ── Header dataclass ──────────────────────────────────────────────────────────

@dataclass
class FramesHeader:
    fps: float
    width: int
    height: int
    total_frames: int
    keyframe_interval: int = 10
    compression: Compression = Compression.ZSTD
    channels: Channels = Channels.RGB
    quality: int = 80          # JPEG quality for keyframes (1-100)
    index_offset: int = HEADER_SIZE   # byte offset where index starts

    # Derived
    @property
    def index_size(self) -> int:
        return self.total_frames * INDEX_ENTRY_SIZE

    @property
    def data_offset(self) -> int:
        return self.index_offset + self.index_size

    def pack(self) -> bytes:
        return struct.pack(
            HEADER_FMT,
            MAGIC, FORMAT_VERSION,
            self.fps, self.width, self.height,
            self.total_frames, self.keyframe_interval,
            int(self.compression), int(self.channels), self.quality,
            self.index_offset,
            b"\x00" * 35,
        )

    @staticmethod
    def unpack(data: bytes) -> "FramesHeader":
        (magic, version, fps, w, h,
         n_frames, kfi, comp, ch, quality,
         idx_off, _) = struct.unpack(HEADER_FMT, data[:HEADER_SIZE])

        if magic != MAGIC:
            raise ValueError(f"Not a .frames file (magic={magic!r})")
        if version != FORMAT_VERSION:
            raise ValueError(f"Unsupported .frames version {version}")

        return FramesHeader(
            fps=fps, width=w, height=h,
            total_frames=n_frames,
            keyframe_interval=kfi,
            compression=Compression(comp),
            channels=Channels(ch),
            quality=quality,
            index_offset=idx_off,
        )


# ── Index entry dataclass ─────────────────────────────────────────────────────

@dataclass
class FrameIndexEntry:
    frame_type: FrameType
    data_offset: int          # byte offset to compressed image/residual data
    data_size: int            # bytes of image/residual data
    timestamp_ms: int         # milliseconds since video start
    motion_offset: int = 0    # byte offset to motion vector data
    motion_size: int = 0      # bytes of motion vector data
    flags: int = 0

    def pack(self) -> bytes:
        return struct.pack(
            INDEX_ENTRY_FMT,
            int(self.frame_type),
            self.data_offset, self.data_size,
            self.timestamp_ms,
            self.motion_offset, self.motion_size,
            self.flags,
        )

    @staticmethod
    def unpack(data: bytes) -> "FrameIndexEntry":
        (ft, doff, dsz, ts, moff, msz, flags) = struct.unpack(
            INDEX_ENTRY_FMT, data[:INDEX_ENTRY_SIZE]
        )
        return FrameIndexEntry(
            frame_type=FrameType(ft),
            data_offset=doff, data_size=dsz,
            timestamp_ms=ts,
            motion_offset=moff, motion_size=msz,
            flags=flags,
        )


# ── File reader/writer helpers ────────────────────────────────────────────────

def write_header_and_index(
    f: BinaryIO,
    header: FramesHeader,
    index: list[FrameIndexEntry],
) -> None:
    """Write header + index at start of file. Call after all frame data is written."""
    f.seek(0)
    f.write(header.pack())
    for entry in index:
        f.write(entry.pack())


def read_header(f: BinaryIO) -> FramesHeader:
    f.seek(0)
    return FramesHeader.unpack(f.read(HEADER_SIZE))


def read_index(f: BinaryIO, header: FramesHeader) -> list[FrameIndexEntry]:
    f.seek(header.index_offset)
    return [
        FrameIndexEntry.unpack(f.read(INDEX_ENTRY_SIZE))
        for _ in range(header.total_frames)
    ]
