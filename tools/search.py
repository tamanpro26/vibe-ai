"""
tools/search.py
Web search backed by two real APIs: Firecrawl (primary — search + JS-rendered
scrape, clean markdown) and Exa (neural/semantic search, used for relevance
ranking). TTL cache: identical query within 1 hour = zero API calls.

Replaced the DuckDuckGo-HTML-scraping stack entirely (2026-07-27). That stack
was keyless and free, but had no official contract — it repeatedly soft-
blocked (HTTP 202) entire query TOPICS under completely normal chat use (not
abuse — a real user asking a follow-up question), because DDG's anti-scraping
heuristic can't distinguish a legitimate assistant from a scraper. A retry and
a second independent DDG endpoint were added as damage control, but the root
problem — scraping HTML with no SLA — can't be fully engineered around.

Both new sources are real, documented APIs with generous free tiers, live-
verified working (2026-07-27) before this rewrite:
  - Firecrawl: 1,000 pages/month, no card required.
  - Exa: $20 signup + $10/month recurring credit (~1,400 searches/month).
Brave was considered and rejected the same day — it now requires a card even
on its free tier, unlike either of these.

Exa's own neural ranking replaces the old LLM-based re-rank step (one fewer
network hop, one fewer point of failure) — its results are already ordered by
relevance when it returns any, so that ordering is kept as the primary sort
signal in the merge.
"""
from __future__ import annotations

import asyncio
import hashlib
import time
from dataclasses import dataclass
from typing import Any

import aiohttp
from loguru import logger


# ── Result schema ─────────────────────────────────────────────────────────────

@dataclass
class SearchResult:
    title:           str
    url:             str
    snippet:         str
    full_text:       str  = ""
    relevance_score: float = 0.0
    source:          str  = "unknown"


# ── TTL cache ─────────────────────────────────────────────────────────────────

class TTLCache:
    """Simple in-memory TTL cache — no Redis required."""

    def __init__(self, ttl_seconds: int = 3600) -> None:
        self._store: dict[str, tuple[Any, float]] = {}
        self._ttl = ttl_seconds

    def _key(self, query: str) -> str:
        return hashlib.md5(query.lower().strip().encode()).hexdigest()

    def get(self, query: str) -> list[SearchResult] | None:
        k = self._key(query)
        if k in self._store:
            data, ts = self._store[k]
            if time.time() - ts < self._ttl:
                logger.debug(f"[search] cache hit for: {query[:50]}")
                return data
            del self._store[k]
        return None

    def set(self, query: str, results: list[SearchResult]) -> None:
        self._store[self._key(query)] = (results, time.time())

    def clear(self) -> None:
        self._store.clear()


# ── Source 1: Firecrawl /search (primary) ──────────────────────────────────────

_FIRECRAWL_URL = "https://api.firecrawl.dev/v2/search"


def _parse_firecrawl_response(data: dict, max_results: int = 8) -> list[SearchResult]:
    """Pure parsing step (offline-testable): Firecrawl's /search JSON ->
    SearchResults. The /search endpoint already returns clean markdown per
    result directly — no separate scrape call needed for basic use."""
    if not data.get("success"):
        return []
    results: list[SearchResult] = []
    for item in (data.get("data", {}) or {}).get("web", []) or []:
        url = item.get("url", "")
        if not url:
            continue
        md = item.get("markdown") or ""
        title = item.get("title") or (item.get("metadata") or {}).get("title", "")
        results.append(SearchResult(
            title=title,
            url=url,
            snippet=item.get("description") or md[:300],
            full_text=md,
            source="firecrawl",
        ))
        if len(results) >= max_results:
            break
    return results


async def _search_firecrawl(query: str, api_key: str, max_results: int = 8) -> list[SearchResult]:
    """Real search API — JS-rendered pages, clean markdown, documented
    contract. Live-verified 2026-07-27 (real Wikipedia content returned for
    a plain query)."""
    if not api_key:
        return []
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    body = {"query": query[:500], "limit": max_results}
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
            async with session.post(_FIRECRAWL_URL, json=body, headers=headers) as resp:
                if resp.status != 200:
                    logger.warning(f"[search/firecrawl] HTTP {resp.status} for: {query[:50]}")
                    return []
                data = await resp.json()
    except Exception as exc:
        logger.warning(f"[search/firecrawl] error: {exc}")
        return []
    return _parse_firecrawl_response(data, max_results)


# ── Source 2: Exa /search (semantic ranking + supplementary results) ──────────

_EXA_URL = "https://api.exa.ai/search"


def _parse_exa_response(data: dict, max_results: int = 8) -> list[SearchResult]:
    """Pure parsing step (offline-testable): Exa's /search JSON ->
    SearchResults. Exa returns results ALREADY ordered by its own neural
    relevance ranking — that ordering is encoded as a descending
    relevance_score so it survives the later merge with Firecrawl."""
    results: list[SearchResult] = []
    items = data.get("results", []) or []
    n = min(len(items), max_results)
    for i, item in enumerate(items[:max_results]):
        url = item.get("url", "")
        if not url:
            continue
        highlights = item.get("highlights") or []
        text = item.get("text") or (highlights[0] if highlights else "")
        results.append(SearchResult(
            title=item.get("title", ""),
            url=url,
            snippet=(text or "")[:300],
            full_text=text or "",
            relevance_score=float(n - i),
            source="exa",
        ))
    return results


async def _search_exa(query: str, api_key: str, max_results: int = 8) -> list[SearchResult]:
    """Neural/semantic search — a trained model extracts relevant excerpts
    directly rather than keyword matching. Live-verified 2026-07-27."""
    if not api_key:
        return []
    headers = {"x-api-key": api_key, "Content-Type": "application/json"}
    body = {
        "query": query[:500],
        "numResults": max_results,
        "contents": {"text": {"maxCharacters": 1000}, "highlights": True},
    }
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as session:
            async with session.post(_EXA_URL, json=body, headers=headers) as resp:
                if resp.status != 200:
                    logger.warning(f"[search/exa] HTTP {resp.status} for: {query[:50]}")
                    return []
                data = await resp.json()
    except Exception as exc:
        logger.warning(f"[search/exa] error: {exc}")
        return []
    return _parse_exa_response(data, max_results)


# ── Main public class ─────────────────────────────────────────────────────────

class SearchIntelligenceStack:
    """
    Drop-in search tool for any team in VibeAI.

    Usage:
        stack = SearchIntelligenceStack(firecrawl_api_key="...", exa_api_key="...")
        results = await stack.search("React animation best practices")
        context = stack.format_for_prompt(results)
    """

    def __init__(
        self,
        firecrawl_api_key: str  = "",
        exa_api_key:       str  = "",
        ttl_seconds:       int  = 3600,
        max_raw_results:   int  = 10,
        top_k:             int  = 5,
        extract_full:      bool = True,
    ) -> None:
        self._firecrawl_key = firecrawl_api_key
        self._exa_key       = exa_api_key
        self._cache         = TTLCache(ttl_seconds)
        self._max_raw       = max_raw_results
        self._top_k         = top_k
        self._extract       = extract_full

    async def search(self, query: str, extract_full: bool | None = None) -> list[SearchResult]:
        """Two-source search with caching.

        extract_full overrides the instance default for this one call. Both
        Firecrawl and Exa already return full content in the SAME call (no
        separate scrape/fetch hop needed either way, unlike the old DDG
        stack) — extract_full=False just trims full_text down to snippet
        size, keeping a chat-speed lookup's prompt small.
        """
        effective_extract = self._extract if extract_full is None else extract_full
        # Cache key includes the extract mode -- a snippet-only result cached
        # for a fast caller must not be silently handed to a later caller that
        # actually wanted full article text (and vice versa).
        cache_key = f"{'full' if effective_extract else 'snip'}:{query}"
        cached = self._cache.get(cache_key)
        if cached:
            return cached

        logger.info(f"[search] query: {query[:60]}")

        firecrawl_res, exa_res = await asyncio.gather(
            _search_firecrawl(query, self._firecrawl_key, self._max_raw),
            _search_exa(query, self._exa_key, self._max_raw),
        )

        # Merge, deduplicate by URL. Exa's own neural ranking IS the
        # relevance signal now (replacing the old LLM-based re-rank call),
        # so its results go first; Firecrawl fills remaining slots for URLs
        # Exa didn't already surface.
        seen: set[str] = set()
        merged: list[SearchResult] = []
        for r in exa_res + firecrawl_res:
            if r.url and r.url not in seen:
                seen.add(r.url)
                merged.append(r)
        ranked = merged[: self._top_k]

        if not effective_extract:
            for r in ranked:
                if len(r.full_text) > 500:
                    r.full_text = r.full_text[:500]

        self._cache.set(cache_key, ranked)
        logger.info(f"[search] {len(ranked)} results for: {query[:40]}")
        return ranked

    @staticmethod
    def format_for_prompt(results: list[SearchResult]) -> str:
        """Convert results to a clean text block for model context."""
        if not results:
            return "No search results found."
        lines = ["SEARCH RESULTS (ranked by relevance):\n"]
        for i, r in enumerate(results, 1):
            lines.append(f"[{i}] {r.title}")
            lines.append(f"    URL: {r.url}")
            lines.append(f"    {r.snippet}")
            if r.full_text:
                lines.append(f"    Full text: {r.full_text[:500]}...")
            lines.append("")
        return "\n".join(lines)


# Singleton. Keys come from Settings (reads .env).
def _search_keys_from_settings() -> tuple[str, str]:
    try:
        from config.settings import settings
        return settings.firecrawl_api_key, settings.exa_api_key
    except Exception:
        return "", ""


_firecrawl_key, _exa_key = _search_keys_from_settings()
search_stack = SearchIntelligenceStack(firecrawl_api_key=_firecrawl_key, exa_api_key=_exa_key)
