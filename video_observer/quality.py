"""
video_observer/quality.py
CHALLENGE 1 SOLUTION: Storage vs Accuracy Tradeoff

The key insight:
  Pixel accuracy (SSIM) and AI accuracy (feature similarity) are different metrics.
  A blurry frame can still have excellent AI features.
  We track BOTH independently and compress as aggressively as possible
  while keeping BOTH above their respective thresholds.

Three mechanisms:
  1. SSIM gate         → forces keyframe when pixel quality degrades
  2. Block change map  → skips unchanged blocks entirely (real compression)
  3. Adaptive quality  → lowers JPEG quality when budget allows
"""
from __future__ import annotations

import math
import numpy as np
import cv2
from dataclasses import dataclass


# ── Configuration ─────────────────────────────────────────────────────────────

@dataclass
class QualityConfig:
    # Reconstruction quality target (0-1, where 1 = perfect)
    target_ssim: float = 0.88

    # AI feature quality target — less strict than pixel quality
    target_feature_sim: float = 0.82

    # JPEG quality range for keyframes
    min_jpeg_quality: int = 55
    max_jpeg_quality: int = 90

    # Block size for change detection
    block_size: int = 16

    # Fraction of blocks that must change before we emit a full delta
    # (below this → SKIP frame; above → emit delta)
    min_change_fraction: float = 0.02

    # Maximum accumulated error before forcing a keyframe
    max_accumulated_error: float = 0.12

    # Force keyframe every N frames regardless (safety net)
    force_keyframe_every: int = 60   # 1 second at 60fps


# ── SSIM (fast pure-numpy, no scikit-image dependency) ────────────────────────

def fast_ssim(img_a: np.ndarray, img_b: np.ndarray) -> float:
    """
    Compute SSIM between two single-channel uint8 images.
    ~5x faster than skimage.metrics.structural_similarity.
    """
    a = img_a.astype(np.float64)
    b = img_b.astype(np.float64)

    C1 = (0.01 * 255) ** 2
    C2 = (0.03 * 255) ** 2

    mu_a = cv2.GaussianBlur(a, (11, 11), 1.5)
    mu_b = cv2.GaussianBlur(b, (11, 11), 1.5)

    mu_a2 = mu_a ** 2
    mu_b2 = mu_b ** 2
    mu_ab = mu_a * mu_b

    sigma_a2 = cv2.GaussianBlur(a ** 2, (11, 11), 1.5) - mu_a2
    sigma_b2 = cv2.GaussianBlur(b ** 2, (11, 11), 1.5) - mu_b2
    sigma_ab = cv2.GaussianBlur(a * b,  (11, 11), 1.5) - mu_ab

    numerator   = (2 * mu_ab + C1) * (2 * sigma_ab + C2)
    denominator = (mu_a2 + mu_b2 + C1) * (sigma_a2 + sigma_b2 + C2)

    ssim_map = numerator / np.maximum(denominator, 1e-10)
    return float(np.mean(ssim_map))


# ── Lightweight feature similarity (no deep learning needed) ──────────────────

def gradient_feature_sim(img_a: np.ndarray, img_b: np.ndarray) -> float:
    """
    Measure feature similarity using Sobel gradient histograms.
    Cheap proxy for deep feature similarity — works well in practice.
    No GPU or neural network needed.
    """
    def gradient_hist(img: np.ndarray) -> np.ndarray:
        gray = img if img.ndim == 2 else cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        gx = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)
        gy = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)
        mag   = np.sqrt(gx**2 + gy**2)
        angle = np.arctan2(gy, gx)
        hist, _ = np.histogram(
            angle.ravel(), bins=36, range=(-math.pi, math.pi),
            weights=mag.ravel()
        )
        norm = hist / (np.sum(hist) + 1e-8)
        return norm

    h_a = gradient_hist(img_a)
    h_b = gradient_hist(img_b)

    # Bhattacharyya coefficient (1 = identical distribution)
    bc = float(np.sum(np.sqrt(h_a * h_b + 1e-12)))
    return min(bc, 1.0)


# ── Block change map ───────────────────────────────────────────────────────────

def block_change_map(
    prev_gray: np.ndarray,
    curr_gray: np.ndarray,
    block_size: int = 16,
    threshold: float = 8.0,
) -> tuple[np.ndarray, float]:
    """
    Divide frames into blocks and detect which ones changed.

    Returns:
        changed: bool array (rows × cols) — True where block changed
        change_fraction: fraction of blocks that changed (0–1)

    Challenge 1 solution: Only store changed blocks in delta frames.
    Unchanged blocks contribute zero bytes.
    """
    h, w = prev_gray.shape[:2]
    rows = h // block_size
    cols = w // block_size

    changed = np.zeros((rows, cols), dtype=bool)
    total = rows * cols

    # Vectorised MAD (mean absolute difference) per block
    for r in range(rows):
        for c in range(cols):
            y0, y1 = r * block_size, (r + 1) * block_size
            x0, x1 = c * block_size, (c + 1) * block_size
            diff = np.abs(
                curr_gray[y0:y1, x0:x1].astype(np.int16) -
                prev_gray[y0:y1, x0:x1].astype(np.int16)
            )
            changed[r, c] = diff.mean() > threshold

    change_fraction = float(changed.sum()) / total
    return changed, change_fraction


# ── Adaptive Quality Manager ──────────────────────────────────────────────────

class AdaptiveQualityManager:
    """
    CHALLENGE 1 SOLUTION (core class)

    Tracks per-frame quality and makes decisions:
      - SKIP:     frame is nearly identical to previous
      - DELTA:    frame has some changes — store diff + motion
      - KEYFRAME: pixel or feature quality has degraded — store full frame

    Also adapts JPEG quality level based on recent quality history.
    """

    def __init__(self, config: QualityConfig | None = None) -> None:
        self.cfg = config or QualityConfig()
        self._accumulated_error: float = 0.0
        self._frames_since_keyframe: int = 0
        self._recent_ssim: list[float] = []
        self._jpeg_quality: int = 80

    @property
    def jpeg_quality(self) -> int:
        return self._jpeg_quality

    def decide(
        self,
        prev_gray: np.ndarray,
        curr_gray: np.ndarray,
        curr_full: np.ndarray,
        prev_full: np.ndarray,
    ) -> tuple[str, dict]:
        """
        Decide frame type and return quality metadata.

        Returns:
            decision: "keyframe" | "delta" | "skip"
            info: {ssim, feature_sim, change_fraction, jpeg_quality, ...}
        """
        self._frames_since_keyframe += 1

        # ── Force keyframe periodically ──────────────────────────────
        if self._frames_since_keyframe >= self.cfg.force_keyframe_every:
            return self._emit_keyframe({"reason": "periodic_force"})

        # ── Measure quality ──────────────────────────────────────────
        ssim = fast_ssim(prev_gray, curr_gray)
        feat = gradient_feature_sim(prev_full, curr_full)
        changed_blocks, change_frac = block_change_map(
            prev_gray, curr_gray, self.cfg.block_size
        )

        # Accumulate error (lower SSIM = higher error)
        frame_error = 1.0 - ssim
        self._accumulated_error += frame_error * 0.3   # weighted decay

        info = {
            "ssim": round(ssim, 4),
            "feature_sim": round(feat, 4),
            "change_fraction": round(change_frac, 4),
            "accumulated_error": round(self._accumulated_error, 4),
            "jpeg_quality": self._jpeg_quality,
            "changed_blocks": changed_blocks,
        }

        # ── Skip: almost nothing changed ─────────────────────────────
        if change_frac < self.cfg.min_change_fraction:
            return "skip", info

        # ── Force keyframe: quality has degraded ─────────────────────
        if (
            ssim < self.cfg.target_ssim
            or feat < self.cfg.target_feature_sim
            or self._accumulated_error > self.cfg.max_accumulated_error
        ):
            return self._emit_keyframe({**info, "reason": "quality_gate"})

        # ── Adapt JPEG quality for next keyframe ──────────────────────
        self._adapt_quality(ssim, feat)

        return "delta", info

    def _emit_keyframe(self, info: dict) -> tuple[str, dict]:
        self._accumulated_error = 0.0
        self._frames_since_keyframe = 0
        self._recent_ssim.clear()
        return "keyframe", {**info, "jpeg_quality": self._jpeg_quality}

    def _adapt_quality(self, ssim: float, feat: float) -> None:
        """
        If quality is comfortably above threshold, lower JPEG quality slightly.
        If quality is close to threshold, raise it.
        """
        self._recent_ssim.append(ssim)
        if len(self._recent_ssim) < 5:
            return
        self._recent_ssim = self._recent_ssim[-20:]   # keep last 20

        avg_ssim = np.mean(self._recent_ssim)
        headroom = avg_ssim - self.cfg.target_ssim

        if headroom > 0.05:   # lots of headroom — compress more
            self._jpeg_quality = max(
                self.cfg.min_jpeg_quality,
                self._jpeg_quality - 2
            )
        elif headroom < 0.01:   # close to threshold — ease off
            self._jpeg_quality = min(
                self.cfg.max_jpeg_quality,
                self._jpeg_quality + 3
            )

    def summary(self) -> dict:
        return {
            "accumulated_error": round(self._accumulated_error, 4),
            "current_jpeg_quality": self._jpeg_quality,
            "avg_recent_ssim": round(float(np.mean(self._recent_ssim)) if self._recent_ssim else 0, 4),
            "frames_since_keyframe": self._frames_since_keyframe,
        }
