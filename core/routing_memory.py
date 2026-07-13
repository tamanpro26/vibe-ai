"""
core/routing_memory.py
Adaptive routing memory — VibeAI's native alternative to Ruflo's SONA optimizer.

The idea (studied from ruflo's v3/@claude-flow/cli/src/memory/sona-optimizer.ts):
learn which MODEL actually succeeds for which KIND of task, from the outcome of
every real run, and bias future routing toward what has worked. Crucially this
is NOT model training and NOT prompt evolution — it's a small, bounded,
interpretable confidence table. That is exactly why it's safe where the
"self-improving loop" ideas deferred earlier were not: the update rule can't
run away, and the learning signal is abundant (every run) rather than a tiny
curated eval set.

Why it fits VibeAI specifically:
  - The success signal ALREADY EXISTS and is objective — the agent loop
    computes `_build_verified` (build/tests passed) and `_verifiers_passed`
    (deterministic battery clean) on every run, then throws them away. This
    module catches that label.
  - Routing is currently STATIC: the same primary -> fallback order regardless
    of what has historically worked for a task type. This is the real weakness
    it attacks.

Design choices (deliberately conservative):
  - Learn over a small set of task CATEGORIES (frontend / backend / debug /
    design / …), not raw keyword sets — bounded pattern space
    (categories x models), generalizes across projects, no combinatorial blowup.
  - Confidence starts NEUTRAL (0.5), moves slowly (bounded exponential update
    matching SONA's rule), and a suggestion only fires above a threshold — so
    a few flukes can't hijack routing.
  - Learning is GLOBAL (~/.vibeai/routing_memory.json): "GLM-4.7 tends to
    succeed on React builds" is a cross-project insight, not a per-folder one.
  - Advisory by default: callers get a suggestion; they decide whether to act.
    Nothing here overrides an explicit user `/model` choice.
  - Fail-soft: any disk error degrades to "no memory", never crashes a run.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

# ── Tunables (mirror SONA's conservative shape) ───────────────────────────────
_CONFIDENCE_START     = 0.5
_CONFIDENCE_INCREMENT = 0.10   # success nudges toward 1.0, asymptotically
_CONFIDENCE_DECREMENT = 0.15   # failure bites a little harder than success rewards
_CONFIDENCE_MIN       = 0.05
_CONFIDENCE_MAX       = 0.95
_SUGGEST_THRESHOLD    = 0.60   # don't act on a suggestion below this
_MIN_SAMPLES          = 3      # …and not until we've actually seen a few runs
_MAX_PATTERNS         = 500    # prune cap

_MEMORY_PATH = Path.home() / ".vibeai" / "routing_memory.json"

# Task categories and the keywords that signal them. Kept in sync in spirit
# with core/skills.py's triggers — same "what kind of task is this" question,
# reused here to label routing outcomes.
_CATEGORIES: dict[str, tuple[str, ...]] = {
    "frontend": ("react", "jsx", "vite", "frontend", "component", "landing page",
                 "website", "css", "tailwind", "ui", "webpage", "html"),
    "backend":  ("api", "fastapi", "backend", "endpoint", "database", "sqlite",
                 "server", "flask", "django", "rest", "auth",
                 # generic python/code signals — a bare "write a python module
                 # with a pytest test" should still learn as backend-ish, not
                 # fall through to "no category / never learned"
                 "python", "pytest", "script", "module", ".py"),
    "debug":    ("debug", "fix", "bug", "broken", "error", "crash", "not working",
                 "doesn't work", "failing", "traceback"),
    "design":   ("design", "redesign", "style", "restyle", "animation", "animate",
                 "hero", "palette", "aesthetic", "polish"),
    "data":     ("csv", "dataframe", "pandas", "analyze data", "statistics",
                 "plot", "chart", "dataset"),
    "algo":     ("algorithm", "sort", "complexity", "optimize", "compute",
                 "calculate", "solve", "recursion", "dynamic programming"),
}


def _kw_matches(keyword: str, text: str, tokens: set[str]) -> bool:
    """Word-boundary aware match. A naive substring check mislabels badly:
    'ui' is inside 'build', 'api' is inside 'apiary', etc. — verified live
    (2026-07-10) that 'ui' matched 'bUIld' and tagged a FastAPI task as
    frontend. Multi-word keywords ('landing page') are matched as substrings;
    single words must match a whole token."""
    if " " in keyword or keyword.startswith("."):
        return keyword in text          # phrases / extensions: substring is fine
    return keyword in tokens            # single words: whole-token match only


def categorize(task: str) -> tuple[str, ...]:
    """Small, stable set of category labels for a task. Empty tuple when
    nothing matches (a task we won't learn routing for — that's fine)."""
    low = (task or "").lower()
    tokens = set(re.split(r"[^a-z0-9.]+", low))
    hits = tuple(cat for cat, kws in _CATEGORIES.items()
                 if any(_kw_matches(kw, low, tokens) for kw in kws))
    return hits


def _pattern_key(category: str, model_id: str) -> str:
    return f"{category}::{model_id}"


@dataclass
class _Pattern:
    confidence: float
    success:    int
    failure:    int
    last_used:  float

    @property
    def samples(self) -> int:
        return self.success + self.failure


@dataclass
class RoutingSuggestion:
    model_id:    str
    confidence:  float
    samples:     int
    categories:  tuple[str, ...]


class RoutingMemory:
    def __init__(self, path: Path = _MEMORY_PATH) -> None:
        self.path = path
        self._patterns: dict[str, _Pattern] = {}
        self._load()

    # ── learning ──────────────────────────────────────────────────────────────

    def record(self, task: str, model_id: str, success: bool) -> tuple[str, ...]:
        """Record one real run's outcome. Returns the categories it learned
        for (empty if the task matched no category)."""
        cats = categorize(task)
        if not cats or not model_id:
            return ()
        for cat in cats:
            key = _pattern_key(cat, model_id)
            p = self._patterns.get(key)
            if p is None:
                p = _Pattern(_CONFIDENCE_START, 0, 0, time.time())
                self._patterns[key] = p
            if success:
                p.success += 1
                p.confidence = min(_CONFIDENCE_MAX,
                                   p.confidence + _CONFIDENCE_INCREMENT * (1 - p.confidence))
            else:
                p.failure += 1
                p.confidence = max(_CONFIDENCE_MIN,
                                   p.confidence - _CONFIDENCE_DECREMENT * p.confidence)
            p.last_used = time.time()
        self._prune()
        self._save()
        return cats

    # ── suggesting ────────────────────────────────────────────────────────────

    def suggest(self, task: str) -> RoutingSuggestion | None:
        """Best-supported model for this task's categories, or None when there
        isn't enough evidence to justify overriding the default routing.

        A model must clear BOTH the confidence threshold AND a minimum sample
        count — a single lucky run should never redirect routing."""
        cats = categorize(task)
        if not cats:
            return None
        # Aggregate per model across the task's categories: average confidence
        # weighted toward models with more samples; require min samples total.
        agg: dict[str, list[float]] = {}   # model_id -> [conf_sum, sample_sum]
        for cat in cats:
            for key, p in self._patterns.items():
                c, m = key.split("::", 1)
                if c != cat:
                    continue
                slot = agg.setdefault(m, [0.0, 0.0])
                slot[0] += p.confidence * max(1, p.samples)
                slot[1] += p.samples
        best: RoutingSuggestion | None = None
        for m, (conf_weighted, samples) in agg.items():
            if samples < _MIN_SAMPLES:
                continue
            conf = conf_weighted / samples if samples else 0.0
            if conf < _SUGGEST_THRESHOLD:
                continue
            if best is None or conf > best.confidence:
                best = RoutingSuggestion(model_id=m, confidence=conf,
                                         samples=int(samples), categories=cats)
        return best

    # ── inspection ────────────────────────────────────────────────────────────

    def summary(self) -> list[dict]:
        """Human-readable view of what's been learned, best-confidence first."""
        rows = []
        for key, p in self._patterns.items():
            cat, model = key.split("::", 1)
            rows.append({
                "category":   cat,
                "model_id":   model,
                "confidence": round(p.confidence, 3),
                "success":    p.success,
                "failure":    p.failure,
            })
        rows.sort(key=lambda r: (-r["confidence"], -r["success"]))
        return rows

    # ── persistence (fail-soft) ────────────────────────────────────────────────

    def _prune(self) -> None:
        if len(self._patterns) <= _MAX_PATTERNS:
            return
        # Drop least-recently-used beyond the cap.
        ordered = sorted(self._patterns.items(), key=lambda kv: kv[1].last_used, reverse=True)
        self._patterns = dict(ordered[:_MAX_PATTERNS])

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            for key, d in data.get("patterns", {}).items():
                self._patterns[key] = _Pattern(
                    confidence=float(d.get("confidence", _CONFIDENCE_START)),
                    success=int(d.get("success", 0)),
                    failure=int(d.get("failure", 0)),
                    last_used=float(d.get("last_used", time.time())),
                )
        except (json.JSONDecodeError, OSError, ValueError, TypeError) as exc:
            logger.warning(f"[routing-memory] load failed ({str(exc)[:50]}) — starting empty")
            self._patterns = {}

    def _save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            payload = {"patterns": {
                key: {"confidence": p.confidence, "success": p.success,
                      "failure": p.failure, "last_used": p.last_used}
                for key, p in self._patterns.items()
            }}
            self.path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except Exception as exc:
            logger.warning(f"[routing-memory] save skipped: {str(exc)[:60]}")


# Module-level singleton (lazy) so callers share one memory + one file.
_memory: RoutingMemory | None = None


def get_memory() -> RoutingMemory:
    global _memory
    if _memory is None:
        _memory = RoutingMemory()
    return _memory
