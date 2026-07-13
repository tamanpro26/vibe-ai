"""
tools/bug_references.py — web references for stuck bug-fix cycles

What a human developer does when a fix attempt fails: google the error
message. The models in the agent loop rarely think to do this themselves
(and the coding path keeps the tool schema lean), so the HARNESS does it
for them — "indirect tool use": when a build/test error has already
survived one blind fix attempt, search the web for the error signature and
inject the top references into the fix context alongside the
comparison-judge strategy that already fires at the same point
(core/agent_loop.py, `_build_fail_cycles >= 2`).

Cost discipline: exactly ONE search per stuck cycle (each search costs one
free-tier LLM call for relevance ranking — see tools/search.py), and only
after a fix has already failed once, since first-attempt errors are fixed
fine without outside help. Everything here is fail-soft: any failure
returns "" and the fix cycle proceeds exactly as before this existed.
"""
from __future__ import annotations

import re

from loguru import logger

# Lines worth searching for, roughly in order of how identifiable they are.
# Scanned from the END of the output — real build/test failures put the
# decisive error near the bottom (vite/rollup, pytest, node all do).
_ERROR_LINE_RE = re.compile(
    r"(?:\berror\b|\berr!\b|exception\b|traceback\b|failed to\b|cannot\b"
    r"|unexpected\b|not found\b|is not defined\b|no module named\b)",
    re.IGNORECASE,
)

# Noise that makes a query TOO specific to this machine/project to match
# anyone else's reports of the same error. Matches absolute AND relative
# multi-segment paths (src/components/Header.tsx) — replaced by basename.
_PATH_RE = re.compile(r"(?:[A-Za-z]:)?[\w.\-]*(?:[\\/][\w.\-]+)+")
_LINE_COL_RE = re.compile(r":\d+(?::\d+)?")
_HEX_RE = re.compile(r"0x[0-9a-fA-F]+")

_MAX_SIGNATURE_CHARS = 140
_MAX_BLOCK_CHARS = 1400


def extract_error_signature(build_output: str) -> str:
    """Pull the most searchable error line out of raw build/test output.

    Pure logic (offline-testable): pick the LAST line that looks like an
    actual error statement, strip machine-specific noise (absolute paths ->
    basenames, line:col suffixes, hex addresses) so the query matches other
    people's reports of the same error, and cap the length. Falls back to
    the last non-empty line; "" for empty input.
    """
    if not build_output or not build_output.strip():
        return ""
    lines = [ln.strip() for ln in build_output.strip().splitlines() if ln.strip()]
    candidates = [ln for ln in lines if _ERROR_LINE_RE.search(ln)]
    raw = candidates[-1] if candidates else lines[-1]

    cleaned = _PATH_RE.sub(                      # keep only the basename of paths
        lambda m: m.group(0).replace("\\", "/").rsplit("/", 1)[-1], raw
    )
    cleaned = _LINE_COL_RE.sub("", cleaned)     # drop :line:col
    cleaned = _HEX_RE.sub("", cleaned)          # drop addresses
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned[:_MAX_SIGNATURE_CHARS]


def _simplify_signature(signature: str) -> str:
    """Second-attempt query when the full signature finds nothing: strip the
    parts that make it unique to THIS run rather than this error class.
    Found live (2026-07-12): '[vite]: Rollup failed to resolve import
    "react-router-dom" from "App.jsx".' -> 0 results verbatim, but the core
    phrase without the [tool] prefix / quotes / trailing 'from X' clause is
    exactly what humans post and search."""
    s = re.sub(r"^\[[^\]]*\]:?\s*", "", signature)      # [vite]: prefixes
    s = re.sub(r"\s+from\s+\S+\s*$", "", s)             # trailing 'from App.jsx.'
    s = s.replace('"', " ").replace("'", " ")
    s = re.sub(r"[.!]+$", "", s)
    return re.sub(r"\s+", " ", s).strip()


def _format_references(results: list) -> str:
    """SearchResults -> compact injectable block. Pure (offline-testable)."""
    if not results:
        return ""
    lines = [
        "WEB REFERENCES for this error (found by searching the error message "
        "— treat as hints from similar reports, verify against the actual code):"
    ]
    for i, r in enumerate(results, 1):
        body = (r.full_text or r.snippet or "").strip().replace("\n", " ")
        lines.append(f"[{i}] {r.title[:80]} — {r.url}")
        if body:
            lines.append(f"    {body[:300]}")
    return "\n".join(lines)[:_MAX_BLOCK_CHARS]


async def fetch_bug_references(error_output: str, max_refs: int = 3) -> str:
    """Search the web for this error and return an injectable reference
    block, or "" if there's nothing useful (never raises)."""
    signature = extract_error_signature(error_output)
    if not signature:
        return ""
    try:
        from tools.search import search_stack
        results = await search_stack.search(signature)
        if not results:
            # One simplified retry (and only one — each search spends a
            # ranking-model call): the verbatim signature is often too
            # specific to match anyone else's report of the same error.
            simplified = _simplify_signature(signature)
            if simplified and simplified != signature:
                results = await search_stack.search(simplified)
        block = _format_references(results[:max_refs])
        if block:
            logger.info(
                f"[bug-refs] {min(len(results), max_refs)} reference(s) for: "
                f"{signature[:60]}"
            )
        return block
    except Exception as exc:
        logger.warning(f"[bug-refs] lookup skipped: {str(exc)[:80]}")
        return ""
