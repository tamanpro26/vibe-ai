"""
tools/search.py
Upgrade 1 — Search Intelligence Stack

3 layers:
  1. Multi-source fetch   → DuckDuckGo web search (keyless) + DuckDuckGo
                            Instant Answers + Brave Search API (optional key)
  2. Full-text extract    → BeautifulSoup + trafilatura (readability)
  3. Relevance rank       → gpt_oss_120b_dispatch scores every result
  + TTL cache             → identical query within 1 hour = zero API calls

History note (2026-07-12): until this date the ONLY live source was the
DuckDuckGo Instant Answer API, which is NOT web search — it returns
encyclopedia-style abstracts for Wikipedia-prominent entities and empty
for everything else (verified live: "Albert Einstein" → 968-char abstract,
"Haunted Adeline" → 0/0 despite the web being full of it). Combined with
the Brave key never being wired into the singleton, every real-world query
returned 0 results and it looked like "the AI can't search." The primary
source is now DuckDuckGo's real HTML search endpoint (keyless, verified to
serve aiohttp with a browser User-Agent); Instant Answers is kept as a
supplementary abstract source, and Brave activates when BRAVE_API_KEY is
actually configured.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import time
import urllib.parse
from dataclasses import dataclass, field
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


# ── Source 1: DuckDuckGo web search (no API key needed) ───────────────────────

_DDG_HTML_URL = "https://html.duckduckgo.com/html/"
_BROWSER_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"


def _decode_ddg_redirect(href: str) -> str:
    """DDG's HTML results link through a redirect
    (`//duckduckgo.com/l/?uddg=<urlencoded real url>&rut=...`) — extract the
    real destination. Plain http(s) hrefs pass through; anything else -> ""."""
    if not href:
        return ""
    if "uddg=" in href:
        # Scheme-relative "//duckduckgo.com/..." parses fine with urlparse.
        qs = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
        target = (qs.get("uddg") or [""])[0]
        return target if target.startswith("http") else ""
    if href.startswith("http"):
        return href
    return ""


def _parse_ddg_html(html: str, max_results: int = 8) -> list[SearchResult]:
    """Pure parsing step (offline-testable): DDG HTML page -> SearchResults.
    Skips sponsored blocks and anything whose redirect doesn't decode to a
    real http(s) URL."""
    from bs4 import BeautifulSoup
    results: list[SearchResult] = []
    soup = BeautifulSoup(html, "html.parser")
    for block in soup.select("div.result"):
        # Skip sponsored blocks (class like "result result--ad").
        if any("--ad" in c for c in (block.get("class") or [])):
            continue
        link = block.select_one("a.result__a")
        if link is None:
            continue
        url = _decode_ddg_redirect(link.get("href") or "")
        title = link.get_text(" ", strip=True)
        if not url or not title:
            continue
        snippet_el = block.select_one(".result__snippet")
        snippet = snippet_el.get_text(" ", strip=True) if snippet_el else ""
        results.append(SearchResult(title=title, url=url, snippet=snippet, source="ddg_web"))
        if len(results) >= max_results:
            break
    return results


async def _search_ddg_web(query: str, max_results: int = 8) -> list[SearchResult]:
    """Real web search via DuckDuckGo's keyless HTML endpoint. This is the
    primary source — the Instant Answer API below only covers encyclopedia
    entities. Verified live (2026-07-12) that this endpoint serves aiohttp
    normally when a browser User-Agent is sent (unlike e.g. the Pollinations
    CDN, which TLS-fingerprint-blocks Python clients — always worth checking
    per-endpoint on this project)."""
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
            async with session.get(
                _DDG_HTML_URL, params={"q": query},
                headers={"User-Agent": _BROWSER_UA},
            ) as resp:
                if resp.status != 200:
                    logger.warning(f"[search/ddg-web] HTTP {resp.status} for: {query[:50]}")
                    return []
                html = await resp.text()
        return _parse_ddg_html(html, max_results)
    except Exception as exc:
        logger.warning(f"[search/ddg-web] error: {exc}")
        return []


# ── Source 2: DuckDuckGo Instant Answers (abstracts only, no key) ─────────────

async def _search_ddg_instant(query: str, max_results: int = 8) -> list[SearchResult]:
    """DuckDuckGo Instant Answer API — free, no key, but NOT web search:
    only returns abstracts/related topics for encyclopedia-prominent
    entities, empty for everything else. Kept as a supplementary source
    because when it DOES hit, the abstract is clean, factual context."""
    url = "https://api.duckduckgo.com/"
    params = {"q": query, "format": "json", "no_html": "1", "skip_disambig": "1"}
    results: list[SearchResult] = []

    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=8)) as session:
            async with session.get(url, params=params) as resp:
                if resp.status != 200:
                    return results
                data = await resp.json(content_type=None)

        # Abstract
        if data.get("Abstract"):
            results.append(SearchResult(
                title=data.get("Heading", query),
                url=data.get("AbstractURL", ""),
                snippet=data["Abstract"],
                source="duckduckgo",
            ))

        # Related topics
        for topic in data.get("RelatedTopics", [])[:max_results]:
            if isinstance(topic, dict) and topic.get("Text"):
                results.append(SearchResult(
                    title=topic.get("Text", "")[:60],
                    url=topic.get("FirstURL", ""),
                    snippet=topic.get("Text", ""),
                    source="duckduckgo",
                ))

    except Exception as exc:
        logger.warning(f"[search/ddg] error: {exc}")

    return results[:max_results]


# ── Source 3: Brave Search (free tier: 2000 queries/month) ────────────────────

async def _search_brave(
    query: str,
    api_key: str,
    max_results: int = 8,
) -> list[SearchResult]:
    """Brave Search API — sign up at brave.com/search/api for free key."""
    if not api_key:
        return []

    url = "https://api.search.brave.com/res/v1/web/search"
    headers = {"Accept": "application/json", "X-Subscription-Token": api_key}
    params  = {"q": query, "count": max_results, "safesearch": "moderate"}
    results: list[SearchResult] = []

    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=8)) as session:
            async with session.get(url, headers=headers, params=params) as resp:
                if resp.status != 200:
                    return results
                data = await resp.json()

        for item in data.get("web", {}).get("results", []):
            results.append(SearchResult(
                title=item.get("title", ""),
                url=item.get("url", ""),
                snippet=item.get("description", ""),
                source="brave",
            ))
    except Exception as exc:
        logger.warning(f"[search/brave] error: {exc}")

    return results


# ── Full-text extractor ───────────────────────────────────────────────────────

async def _extract_full_text(url: str) -> str:
    """Fetch and extract readable article text using trafilatura."""
    if not url or not url.startswith("http"):
        return ""
    try:
        import trafilatura
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=10)) as session:
            async with session.get(url, headers={"User-Agent": "Mozilla/5.0"}) as resp:
                if resp.status != 200:
                    return ""
                html = await resp.text()
        text = trafilatura.extract(html, include_comments=False, include_tables=False)
        return (text or "")[:3000]
    except Exception as exc:
        logger.debug(f"[search/extract] {url}: {exc}")
        return ""


# ── Relevance ranker ──────────────────────────────────────────────────────────

async def _rank_results(
    query: str,
    results: list[SearchResult],
    top_k: int = 5,
) -> list[SearchResult]:
    """
    gpt_oss_120b_dispatch scores each result for relevance (docstring
    previously claimed GLM-4.7-Flash — stale; honest-naming rule applies to
    comments too). This is the key layer — reduces noise so downstream
    models work on signal.
    """
    if not results:
        return []

    try:
        from models.registry import registry
        connector = registry.get("gpt_oss_120b_dispatch")

        items = "\n".join(
            f"[{i}] {r.title}: {r.snippet[:150]}"
            for i, r in enumerate(results)
        )
        prompt = (
            f"Query: {query}\n\nResults:\n{items}\n\n"
            f"Score each result 0-10 for relevance to the query. "
            f"Return ONLY a JSON array of scores, e.g. [8,3,9,2,7]"
        )
        raw = await connector.generate(prompt=prompt, max_tokens=100, temperature=0.0)

        import re
        match = re.search(r"\[[\d,\s]+\]", raw)
        if match:
            scores = json.loads(match.group(0))
            for i, score in enumerate(scores):
                if i < len(results):
                    results[i].relevance_score = float(score)
            results.sort(key=lambda r: r.relevance_score, reverse=True)

    except Exception as exc:
        logger.warning(f"[search/rank] error: {exc}")

    return results[:top_k]


# ── Main public class ─────────────────────────────────────────────────────────

class SearchIntelligenceStack:
    """
    Drop-in search tool for any team in VibeAI.

    Usage:
        stack = SearchIntelligenceStack(brave_api_key="...")
        results = await stack.search("React animation best practices")
        context = stack.format_for_prompt(results)
    """

    def __init__(
        self,
        brave_api_key:   str  = "",
        ttl_seconds:     int  = 3600,
        max_raw_results: int  = 10,
        top_k:           int  = 5,
        extract_full:    bool = True,
    ) -> None:
        self._brave_key   = brave_api_key
        self._cache       = TTLCache(ttl_seconds)
        self._max_raw     = max_raw_results
        self._top_k       = top_k
        self._extract     = extract_full

    async def search(self, query: str) -> list[SearchResult]:
        """Full 3-layer search pipeline with caching."""
        # Cache check
        cached = self._cache.get(query)
        if cached:
            return cached

        logger.info(f"[search] query: {query[:60]}")

        # Fetch from all sources concurrently. ddg_web is the real web
        # search; instant answers add a clean abstract when one exists;
        # brave contributes only when a key is configured.
        web_res, ia_res, brave_res = await asyncio.gather(
            _search_ddg_web(query, self._max_raw),
            _search_ddg_instant(query, self._max_raw),
            _search_brave(query, self._brave_key, self._max_raw),
        )

        # Merge, deduplicate by URL. IA abstract first (highest signal when
        # present), then real web results, then brave.
        seen: set[str] = set()
        merged: list[SearchResult] = []
        for r in ia_res + web_res + brave_res:
            if r.url not in seen and r.url:
                seen.add(r.url)
                merged.append(r)

        # Rank with GLM-4.7-Flash
        ranked = await _rank_results(query, merged, self._top_k)

        # Extract full text for top results (async)
        if self._extract:
            tasks = [_extract_full_text(r.url) for r in ranked[:3]]
            texts = await asyncio.gather(*tasks, return_exceptions=True)
            for r, txt in zip(ranked[:3], texts):
                if isinstance(txt, str):
                    r.full_text = txt

        self._cache.set(query, ranked)
        logger.info(f"[search] {len(ranked)} ranked results for: {query[:40]}")
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


# Singleton. The Brave key comes from Settings (reads .env) — before
# 2026-07-12 this was constructed with no key at all, so the Brave source
# had never once contributed a result.
def _brave_key_from_settings() -> str:
    try:
        from config.settings import settings
        return settings.brave_api_key
    except Exception:
        return ""


search_stack = SearchIntelligenceStack(brave_api_key=_brave_key_from_settings())
