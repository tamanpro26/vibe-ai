"""
models/circuit_breaker.py
Tracks models that have failed with a quota/rate-limit error, so the next
call skips straight past the wasted retry-with-backoff cycle in
models/base.py instead of re-discovering the same failure on every single
agent iteration.

Distinguishes two failure shapes by inspecting the error text:
  - Hard daily quota exhaustion (e.g. Gemini free tier's 20 req/day cap,
    signaled by "PerDay" in the quotaId) — these don't recover until the
    provider's daily reset, so a single retry attempt this session is
    pointless. Long cooldown.
  - Generic/transient rate limiting (no daily-quota signal) — likely to
    clear on its own soon. Short cooldown.

This is purely an optimization: every caller already has fallback logic
that catches a raised exception and moves to the next model, so skipping
straight to "broken" just removes the wasted wait, it doesn't change
correctness.
"""
from __future__ import annotations

import re
import time

from loguru import logger

# model_id -> monotonic timestamp when the breaker resets
_BROKEN_UNTIL: dict[str, float] = {}

# Substrings that indicate a PER-DAY quota, not a transient rate limit. Used only
# as a fallback label/cooldown when the provider doesn't give an explicit retry
# delay (see _parse_retry_delay below, which is preferred when present — e.g.
# Groq's "tokens per day (TPD)" wording doesn't match these substrings, but its
# error text DOES include an exact "try again in 1h3m45s" delay we can parse
# directly instead of needing to enumerate every provider's daily-quota phrasing).
_DAILY_QUOTA_SIGNALS = (
    "PerDay", "per day", "requests per day", "tokens per day", "TPD",
    "daily limit", "daily quota",
)

_DAILY_COOLDOWN_SEC = 6 * 60 * 60   # 6h — long enough to skip the rest of a session
_SHORT_COOLDOWN_SEC = 60            # generic 429, likely to clear soon
_MAX_PARSED_DELAY_SEC = 12 * 60 * 60  # sanity cap in case a provider sends something absurd

# Matches "retry in 21s", "retry in 24.65s", "try again in 1h3m45.792s", etc. —
# covers both Gemini's ("Please retry in Ns") and Groq's ("Please try again in
# XhYmZs") phrasing, which is far more accurate than guessing a fixed cooldown.
_RETRY_DELAY_RE = re.compile(
    r"(?:retry|try again)[^0-9]{0,15}?(?:(\d+)h)?(?:(\d+)m)?(\d+(?:\.\d+)?)s",
    re.IGNORECASE,
)


def _parse_retry_delay(text: str) -> float | None:
    """Extract a provider-stated retry delay (seconds) from error text, if present."""
    m = _RETRY_DELAY_RE.search(text)
    if not m:
        return None
    hours, minutes, seconds = m.groups()
    total = float(seconds)
    if minutes:
        total += int(minutes) * 60
    if hours:
        total += int(hours) * 3600
    return min(total, _MAX_PARSED_DELAY_SEC)


def is_broken(model_id: str) -> float | None:
    """Return seconds remaining on the breaker, or None if not broken."""
    until = _BROKEN_UNTIL.get(model_id)
    if until is None:
        return None
    remaining = until - time.monotonic()
    if remaining <= 0:
        _BROKEN_UNTIL.pop(model_id, None)
        return None
    return remaining


def record_failure(model_id: str, exc: BaseException) -> None:
    """Inspect a failure; set a cooldown if it looks like a quota/rate-limit error."""
    text = str(exc)
    code = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    is_rate_limited = code == 429 or "429" in text or "RESOURCE_EXHAUSTED" in text
    if not is_rate_limited:
        return  # not this kind of failure — don't trip the breaker on e.g. a 500 or timeout

    is_daily = any(sig in text for sig in _DAILY_QUOTA_SIGNALS)
    parsed = _parse_retry_delay(text)
    if parsed is not None:
        # Trust the provider's own stated delay over any heuristic — most accurate
        # signal available, and naturally handles both short and daily cases.
        cooldown = parsed
        label = f"provider-stated {cooldown:.0f}s"
    else:
        cooldown = _DAILY_COOLDOWN_SEC if is_daily else _SHORT_COOLDOWN_SEC
        label = "daily quota" if is_daily else "rate limit"
    _BROKEN_UNTIL[model_id] = time.monotonic() + cooldown
    logger.warning(
        f"[circuit-breaker] {model_id} tripped ({label}) — skipping retries for {cooldown:.0f}s"
    )


def reset(model_id: str) -> None:
    """Manually clear a breaker (e.g. for tests, or a known-recovered model)."""
    _BROKEN_UNTIL.pop(model_id, None)


def broken_models() -> dict[str, float]:
    """
    All currently-broken endpoints -> seconds remaining on their cooldown.
    Used by the CLI to tell the user up front which quotas are exhausted
    instead of letting them discover it through degraded output.
    """
    now = time.monotonic()
    out = {}
    for key, until in list(_BROKEN_UNTIL.items()):
        remaining = until - now
        if remaining <= 0:
            _BROKEN_UNTIL.pop(key, None)
        else:
            out[key] = remaining
    return out
