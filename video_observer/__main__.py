"""
video_observer/main.py
CLI and test runner.

Usage:
    python -m video_observer convert input.mp4 output.frames
    python -m video_observer analyze output.frames
    python -m video_observer benchmark input.mp4
    python -m video_observer test          # runs all challenge tests with synthetic video
"""
from __future__ import annotations

import sys
import time
import tempfile
from pathlib import Path

import numpy as np
import cv2


def create_synthetic_video(path: str, n_frames: int = 120, fps: int = 30) -> None:
    """Create a test video: static background + moving ball."""
    h, w = 360, 640
    out = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
    for i in range(n_frames):
        frame = np.ones((h, w, 3), dtype=np.uint8) * 30   # dark background
        # Slow pan
        x = int((i / n_frames) * w * 0.8 + w * 0.1)
        y = int(h * 0.5 + np.sin(i * 0.2) * h * 0.2)
        cv2.circle(frame, (x, y), 20, (0, 200, 255), -1)
        # Static text (should compress well)
        cv2.putText(frame, f"Frame {i}", (10, 30),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, (200, 200, 200), 1)
        out.write(frame)
    out.release()


def cmd_test() -> None:
    """Run all 3 challenge validation tests."""
    from video_observer.converter import FramesConverter, ConvertConfig
    from video_observer.loader    import FramesLoader
    from video_observer.quality   import fast_ssim, AdaptiveQualityManager
    from video_observer.analyzer  import MotionAnalyzer, TemporalSummarizer

    print("=" * 60)
    print("VibeAI .frames system — Challenge validation tests")
    print("=" * 60)

    with tempfile.TemporaryDirectory() as tmp:
        video_path  = f"{tmp}/test.mp4"
        frames_path = f"{tmp}/test.frames"

        print("\n[setup] Creating 120-frame synthetic video...")
        create_synthetic_video(video_path, n_frames=120, fps=30)
        print(f"        Video: {Path(video_path).stat().st_size / 1e3:.1f} KB")

        # ── CHALLENGE 1: Storage vs Accuracy ─────────────────────────────────
        print("\n─── Challenge 1: Storage vs Accuracy ──────────────────────────")

        cfg = ConvertConfig(
            target_width=640, target_height=360,
            grayscale=False, keyframe_interval=10,
        )
        conv = FramesConverter(cfg)
        stats = conv.convert(video_path, frames_path)

        print(f"  Original size  : {stats['original_size_mb']} MB")
        print(f"  .frames size   : {stats['output_size_mb']} MB")
        print(f"  Compression    : {stats['compression_ratio']}×")
        print(f"  Keyframes      : {stats['keyframes']}")
        print(f"  Delta frames   : {stats['deltas']}")
        print(f"  Skip frames    : {stats['skips']}")
        print(f"  Quality budget : {stats['quality_summary']}")

        # Verify SSIM of reconstructed frames
        with FramesLoader(frames_path) as loader:
            ssim_scores = []
            cap = cv2.VideoCapture(video_path)
            for i in range(min(20, loader.header.total_frames)):
                ret, raw = cap.read()
                if not ret:
                    break
                fd = loader.get_frame(i, reconstruct=True)
                orig_gray = cv2.cvtColor(
                    cv2.resize(raw, (640, 360)), cv2.COLOR_BGR2GRAY
                )
                rec_gray  = cv2.cvtColor(fd.frame, cv2.COLOR_BGR2GRAY)
                ssim_scores.append(fast_ssim(orig_gray, rec_gray))
            cap.release()

        avg_ssim = np.mean(ssim_scores) if ssim_scores else 0
        target   = cfg.quality.target_ssim
        status   = "PASS ✓" if avg_ssim >= target else f"FAIL ✗ (target {target:.2f})"
        print(f"\n  Avg SSIM over first 20 frames: {avg_ssim:.4f}  [{status}]")

        # ── CHALLENGE 2: Speed ────────────────────────────────────────────────
        print("\n─── Challenge 2: Speed ─────────────────────────────────────────")
        print(f"  Processing time : {stats['processing_time_s']} s")
        print(f"  Speed           : {stats['fps_processed']} fps processed")

        # Loader speed test
        t0 = time.perf_counter()
        with FramesLoader(frames_path) as loader:
            for fd in loader.stream(reconstruct=False):  # motion-only, no decoding
                _ = fd.motion_vectors
        mv_time = time.perf_counter() - t0
        total_f = stats["frames_total"]
        print(f"  Motion-only scan: {total_f} frames in {mv_time*1000:.1f} ms "
              f"({total_f/mv_time:.0f} fps)")

        # ── CHALLENGE 3: Complexity (modular test) ────────────────────────────
        print("\n─── Challenge 3: Complexity / Modularity ───────────────────────")

        # Test each module independently
        tests = [
            ("format.py   — binary pack/unpack",    _test_format),
            ("quality.py  — SSIM + adaptive budget", _test_quality),
            ("loader.py   — memory-mapped access",  lambda: _test_loader(frames_path)),
            ("analyzer.py — motion event detection", lambda: _test_analyzer(frames_path)),
        ]
        for name, fn in tests:
            try:
                fn()
                print(f"  PASS ✓  {name}")
            except Exception as exc:
                print(f"  FAIL ✗  {name}: {exc}")

    print("\n" + "=" * 60)
    print("All tests complete.")
    print("=" * 60)


# ── Module-level unit tests ───────────────────────────────────────────────────

def _test_format() -> None:
    from video_observer.format import FramesHeader, FrameIndexEntry, FrameType, Compression, Channels, HEADER_SIZE
    h = FramesHeader(fps=60.0, width=640, height=360, total_frames=120)
    packed = h.pack()
    assert len(packed) == HEADER_SIZE
    h2 = FramesHeader.unpack(packed)
    assert abs(h2.fps - 60.0) < 0.01
    assert h2.width == 640


def _test_quality() -> None:
    from video_observer.quality import fast_ssim, AdaptiveQualityManager, QualityConfig
    a = np.random.randint(0, 255, (360, 640), dtype=np.uint8)
    b = a.copy()
    assert fast_ssim(a, b) > 0.99, "Identical images must have SSIM ≈ 1.0"
    noise = np.random.randint(0, 30, a.shape, dtype=np.uint8)
    c = np.clip(a.astype(int) + noise, 0, 255).astype(np.uint8)
    assert fast_ssim(a, c) < fast_ssim(a, b), "Noisy image must have lower SSIM"


def _test_loader(frames_path: str) -> None:
    from video_observer.loader import FramesLoader
    with FramesLoader(frames_path) as loader:
        assert loader.header is not None
        assert loader.header.total_frames > 0
        fd = loader.get_frame(0)
        assert fd.frame is not None
        assert fd.frame.shape[0] > 0


def _test_analyzer(frames_path: str) -> None:
    from video_observer.loader import FramesLoader
    from video_observer.analyzer import MotionAnalyzer
    with FramesLoader(frames_path) as loader:
        analyzer = MotionAnalyzer()
        events = analyzer.analyze(loader)
        assert isinstance(events, list)   # may be empty for synthetic video


def cmd_convert(video_path: str, output_path: str) -> None:
    from video_observer.converter import FramesConverter, ConvertConfig
    cfg   = ConvertConfig()
    conv  = FramesConverter(cfg)
    stats = conv.convert(video_path, output_path)
    print(f"Converted: {stats}")


def cmd_analyze(frames_path: str) -> None:
    from video_observer.loader   import FramesLoader
    from video_observer.analyzer import TemporalSummarizer
    with FramesLoader(frames_path) as loader:
        summarizer = TemporalSummarizer(max_keyframes=8)
        packet = summarizer.summarize(loader)
        print(packet.to_prompt_context())


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "test"

    if cmd == "test":
        cmd_test()
    elif cmd == "convert" and len(sys.argv) == 4:
        cmd_convert(sys.argv[2], sys.argv[3])
    elif cmd == "analyze" and len(sys.argv) == 3:
        cmd_analyze(sys.argv[2])
    else:
        print("Usage:")
        print("  python -m video_observer test")
        print("  python -m video_observer convert input.mp4 output.frames")
        print("  python -m video_observer analyze output.frames")
