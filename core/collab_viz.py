"""
core/collab_viz.py
Live "AI team collaboration" event stream — the structured flow view of who
is doing what during a manager-pipeline run:

    ● User request received
          ↓
    ● Task classified: CODE + BRAIN
          ↓
    ● CODE team working…
        └ glm_47_cerebras (full-stack) ✓ 2.1s
    ● CODE team ✓ completed
          ↓
    ● Manager combining results…

Design: a tiny synchronous pub/sub. The pipeline emits events; the CLI (or
any other frontend — the API's WebSocket could subscribe too) renders them.
Two hard rules, both load-bearing:

  - emit() with no subscribers is a no-op — instrumentation costs nothing
    when nobody is watching (API server runs, eval harness, tests).
  - A subscriber that raises must NEVER break the pipeline: rendering is
    strictly less important than the work being rendered. Exceptions are
    swallowed per-subscriber, per-event.

Stage events come from explicit instrumentation in manager/claude_manager.py.
Model events come for free from core/activity_log.log_model — the single
choke point every model call in the system already passes through — so the
view shows every real model call without touching every team.
"""
from __future__ import annotations

from typing import Callable

_subscribers: list[Callable[[dict], None]] = []


def subscribe(fn: Callable[[dict], None]) -> None:
    if fn not in _subscribers:
        _subscribers.append(fn)


def unsubscribe(fn: Callable[[dict], None]) -> None:
    try:
        _subscribers.remove(fn)
    except ValueError:
        pass


def emit(kind: str, label: str, *, status: str = "info",
         model: str = "", role: str = "", duration_ms: float = 0) -> None:
    """kind: "stage" (pipeline step) | "model" (a single model call).
    status: "start" | "done" | "fail" | "info"."""
    if not _subscribers:
        return
    event = {"kind": kind, "label": label, "status": status,
             "model": model, "role": role, "duration_ms": duration_ms}
    for fn in list(_subscribers):
        try:
            fn(event)
        except Exception:
            pass  # a broken renderer must never break the pipeline


# ── Formatting (pure, testable — renderers add their own colors) ──────────────

_ICON = {"start": "●", "info": "●", "done": "✓", "fail": "✗"}


def format_event(event: dict) -> tuple[str, str] | None:
    """Render an event to (text, style) where style is one of
    "stage" | "stage_done" | "stage_fail" | "model" | "model_fail".
    Returns None for events a flow view shouldn't display."""
    kind, status = event.get("kind"), event.get("status", "info")
    label = str(event.get("label", "")).strip()
    if not label:
        return None

    if kind == "stage":
        icon = _ICON.get(status, "●")
        style = {"done": "stage_done", "fail": "stage_fail"}.get(status, "stage")
        return f"{icon} {label}", style

    if kind == "model":
        role = str(event.get("role", "")).strip()
        dur  = event.get("duration_ms") or 0
        mark = "✓" if status != "fail" else "✗"
        parts = [f"    └ {label}"]
        if role:
            parts.append(f"({role})")
        parts.append(mark)
        if dur:
            parts.append(f"{dur/1000:.1f}s")
        return " ".join(parts), ("model_fail" if status == "fail" else "model")

    return None


CONNECTOR = "      ↓"
