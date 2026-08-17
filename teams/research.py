"""
teams/research.py — Research team.

Research already existed in this codebase, but only as a tool bolted onto
other flows (`tools/search.py`'s SearchIntelligenceStack, and
`tools/domain_retriever.py`'s domain-aware injection feeding brain/code
prompts). There was no team the router could dispatch a research question to,
so "find out X and tell me what's actually true" had no owner: it fell to the
brain team, which answers from weights plus whatever context happened to be
injected.

This team makes that a first-class destination. It does NOT reimplement
search -- it orchestrates the stack that already works:

    search (Exa + Firecrawl, merged/deduped) -> ground -> synthesise -> cite

Both backends verified live 2026-08-09 returning current results.
"""
from __future__ import annotations

import asyncio
from typing import Any

from loguru import logger

from config.scaffold_loader import get_prompt
from config.settings import settings
from core.imcp import TaskJSON
from models.registry import generate_resilient
from teams.base_team import BaseTeam
from tools.search import SearchIntelligenceStack

# Grounding rules are explicit and non-negotiable, because correct retrieved
# context alone does NOT stop a model inventing specifics -- measured on this
# project: given accurate sources, models still fabricated exact numbers,
# dates and version strings that appeared nowhere in the context. The rule has
# to be stated, not implied by the presence of good context.
_RESEARCH_SYSTEM = get_prompt("RESEARCH_SYSTEM", """You are VibeAI's research specialist. You answer from the SOURCES you are
given, not from memory.

Hard rules:
- Every specific claim -- a number, date, version, name, quote, or statistic --
  must appear in the sources. If it is not there, you do not state it.
- Cite the source index for each specific claim, like [2].
- When sources disagree, say so and give both readings. Do not silently pick one.
- When the sources do not answer the question, say exactly what is missing.
  "The sources don't cover X" is a correct and useful answer. Inventing a
  plausible X is not.
- Your own prior knowledge is context for judging sources, never a substitute
  for them. Do not top up a thin answer with remembered facts.

Structure: lead with the direct answer, then the evidence, then anything that
remains genuinely uncertain.""")

# Two models, cheapest-capable first. The synthesiser reads several full
# articles, so context headroom matters more than raw reasoning here.
_SYNTH_MODEL = "llama33_70b_memory"      # 128k context, fast, follows structure
_SYNTH_FALLBACK = "gpt_oss_120b_coder"   # generate_resilient handles the rest

_MAX_QUERIES = 3
_SEARCH_TIMEOUT_S = 45


class ResearchTeam(BaseTeam):
    team_name = "research"

    def __init__(self) -> None:
        self._stack = SearchIntelligenceStack(
            firecrawl_api_key=settings.firecrawl_api_key or "",
            exa_api_key=settings.exa_api_key or "",
            top_k=6,
        )

    async def _execute(
        self,
        task_json: TaskJSON,
        instruction: str,
        iteration: int,
        extra: dict[str, Any],
    ) -> str:
        question = instruction or task_json.refined_prompt or task_json.original_prompt
        queries = await self._plan_queries(question)
        logger.info(f"[research] {len(queries)} quer(ies): {queries}")

        # Same straggler rule the refiner learned the hard way: one slow
        # provider must not gate the whole team. A partial source set beats a
        # request that never returns.
        gathered = await asyncio.gather(
            *(
                asyncio.wait_for(self._stack.search(q), timeout=_SEARCH_TIMEOUT_S)
                for q in queries
            ),
            return_exceptions=True,
        )

        results, seen = [], set()
        for query, outcome in zip(queries, gathered):
            if isinstance(outcome, Exception):
                logger.warning(
                    f"[research] query {query[:40]!r} failed "
                    f"({type(outcome).__name__}) — continuing with the rest"
                )
                continue
            for item in outcome:
                if item.url not in seen:
                    seen.add(item.url)
                    results.append(item)

        if not results:
            # Say so rather than answering from weights and calling it research.
            return (
                "I could not retrieve any sources for this question, so I have "
                "nothing to ground an answer in. Rather than answer from memory "
                "and present it as researched, here is the gap: no search "
                "backend returned results for "
                + ", ".join(repr(q) for q in queries)
                + ". Retry, or ask me to answer from general knowledge instead "
                "and I will label it as such."
            )

        sources = self._format_sources(results)
        answer = await generate_resilient(
            _SYNTH_MODEL,
            prompt=f"QUESTION:\n{question}\n\nSOURCES:\n{sources}\n\nAnswer using only these sources.",
            system=_RESEARCH_SYSTEM,
            max_tokens=3000,
            temperature=0.2,
        )
        return f"{answer}\n\n---\n**Sources**\n{self._format_citations(results)}"

    async def _plan_queries(self, question: str) -> list[str]:
        """Turn one question into a few targeted searches.

        A single verbatim query is a weak search: real questions carry several
        sub-questions. Falls back to the raw question -- a planning hiccup must
        not stop the research.
        """
        try:
            raw = await generate_resilient(
                "llama31_8b_router",
                prompt=f"Question: {question}",
                system=(
                    f"Write up to {_MAX_QUERIES} web search queries that together answer "
                    "the question. One per line, no numbering, no commentary. "
                    "Keep them short and keyword-like."
                ),
                max_tokens=200,
                temperature=0.3,
            )
            queries = [
                line.strip(" -*\t") for line in (raw or "").splitlines() if line.strip()
            ][:_MAX_QUERIES]
            return queries or [question]
        except Exception as exc:
            logger.warning(f"[research] query planning failed ({str(exc)[:60]}) — using the question as-is")
            return [question]

    def _format_sources(self, results: list) -> str:
        blocks = []
        for index, item in enumerate(results, 1):
            # full_text when the stack extracted it, snippet otherwise; capped
            # so a handful of long articles cannot blow the synthesiser's context.
            body = (item.full_text or item.snippet or "")[:2000]
            blocks.append(f"[{index}] {item.title}\nURL: {item.url}\n{body}")
        return "\n\n".join(blocks)

    def _format_citations(self, results: list) -> str:
        return "\n".join(
            f"[{index}] [{item.title}]({item.url})" for index, item in enumerate(results, 1)
        )
