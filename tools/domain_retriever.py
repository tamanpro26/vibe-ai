"""
tools/domain_retriever.py — Domain-aware knowledge injection for VibeMind

Free models have knowledge gaps in specialised domains — recent medical
guidelines, niche law, cutting-edge physics, new frameworks post-cutoff.
The insight: the gap isn't that free models can't reason over these things —
it's that they're reasoning from stale/incomplete weights. Give them current
accurate information and their reasoning quality equalises with Opus.

Two steps:
  1. Detect domain from vocabulary (12 categories)
  2. Run 2–3 targeted search queries in parallel (domain-specific templates)
     and return a formatted knowledge block for injection into VibeMind proposers

All searches use the existing SearchIntelligenceStack — no new dependencies.
"""
from __future__ import annotations

import asyncio
import re
from loguru import logger


# ── Domain vocabulary ─────────────────────────────────────────────────────────

_DOMAIN_VOCAB: dict[str, list[str]] = {
    "science": [
        "quantum", "physics", "chemistry", "biology", "astronomy",
        "molecule", "atom", "photon", "energy", "force", "particle",
        "genetic", "evolution", "cell", "protein", "dna", "rna",
        "relativity", "entropy", "thermodynamics",
    ],
    "medicine": [
        "clinical", "diagnosis", "treatment", "drug", "patient",
        "symptom", "disease", "therapy", "surgery", "medication",
        "cancer", "diabetes", "vaccine", "antibiotic", "dosage",
        "trial", "guideline", "prognosis", "chronic", "acute",
    ],
    "law": [
        "legal", "court", "statute", "ruling", "jurisdiction",
        "precedent", "contract", "liability", "plaintiff", "defendant",
        "amendment", "regulation", "compliance", "constitution", "tort",
        "criminal", "civil", "appeal", "counsel", "verdict",
    ],
    "finance": [
        "stock", "portfolio", "investment", "equity", "bond",
        "derivative", "hedge", "return", "volatility", "margin",
        "dividend", "asset", "market cap", "p/e", "liquidity",
        "interest rate", "inflation", "gdp", "fiscal", "monetary",
    ],
    "history": [
        "century", "war", "empire", "revolution", "civilization",
        "ancient", "medieval", "colonial", "treaty", "dynasty",
        "period", "era", "historical", "battle", "conquest",
    ],
    "technology": [
        "algorithm", "framework", "architecture", "api", "database",
        "machine learning", "neural network", "kubernetes", "docker",
        "microservice", "latency", "throughput", "benchmark", "compiler",
        "protocol", "llm", "transformer", "embedding", "inference",
    ],
    "philosophy": [
        "ethics", "epistemology", "ontology", "metaphysics", "logic",
        "consciousness", "moral", "virtue", "existential", "phenomenology",
        "rationalism", "empiricism", "determinism", "free will",
    ],
    "mathematics": [
        "theorem", "proof", "conjecture", "topology", "calculus",
        "differential", "integral", "matrix", "eigenvalue", "prime",
        "factorial", "combinatorics", "probability", "stochastic",
    ],
    "economics": [
        "supply", "demand", "elasticity", "equilibrium", "monopoly",
        "oligopoly", "externality", "gdp", "macroeconomics", "microeconomics",
        "keynesian", "fiscal policy", "trade", "tariff",
    ],
    "literature": [
        "novel", "poetry", "prose", "narrative", "metaphor",
        "symbolism", "allegory", "genre", "author", "literary",
        "character", "plot", "theme", "setting", "style",
    ],
    "politics": [
        "government", "policy", "election", "democracy", "legislation",
        "senate", "parliament", "constitution", "diplomacy", "sovereignty",
        "ideology", "party", "vote", "campaign", "geopolitics",
    ],
    "environment": [
        "climate", "carbon", "emission", "renewable", "fossil fuel",
        "ecosystem", "biodiversity", "deforestation", "pollution",
        "sustainability", "greenhouse", "ozone", "global warming",
    ],
}

# ── Search query templates per domain ─────────────────────────────────────────

_DOMAIN_QUERIES: dict[str, list[str]] = {
    "science":      ["{topic} scientific research 2024 2025", "{topic} scientific evidence findings"],
    "medicine":     ["{topic} clinical guidelines 2025", "{topic} treatment evidence diagnosis"],
    "law":          ["{topic} legal definition precedent case law", "{topic} court ruling legislation"],
    "finance":      ["{topic} financial analysis market 2025", "{topic} investment risk return"],
    "history":      ["{topic} historical facts timeline", "{topic} historical context significance"],
    "technology":   ["{topic} technical documentation best practices", "{topic} implementation guide 2025"],
    "philosophy":   ["{topic} philosophical argument analysis", "{topic} philosophical perspective"],
    "mathematics":  ["{topic} mathematical proof formula", "{topic} mathematical definition examples"],
    "economics":    ["{topic} economic analysis data", "{topic} economic theory evidence"],
    "literature":   ["{topic} literary analysis interpretation", "{topic} literary context author"],
    "politics":     ["{topic} political analysis policy", "{topic} government policy 2025"],
    "environment":  ["{topic} environmental data research 2025", "{topic} climate evidence impact"],
}


# Leading instruction/question phrases carrying no search value. Applied in a
# loop by _extract_topic since they stack ("get me info about ...").
_LEAD_STRIP_RE = re.compile(
    r"^(?:please\s+|what|how|why|when|who|which|is|are|can|could|would|does|do"
    r"|tell\s+me(?:\s+about)?|explain|describe|summari[sz]e|give\s+me"
    r"|get\s+me|find(?:\s+me)?|search(?:\s+for)?|look\s+up"
    r"|info(?:rmation)?\s+(?:about|on))\s+",
    re.IGNORECASE,
)
# Connectives left dangling at the front after the keep-the-tail slice
# ("...and themes of the novel X" -> "themes of the novel X").
_DANGLING_LEADERS = {"and", "or", "of", "the", "a", "an", "in", "on", "for", "to", "about"}


class DomainRetriever:
    """Retrieves domain-specific current knowledge and formats it for VibeMind."""

    def detect_domain(self, text: str) -> str:
        """Return the best-matching domain, or 'general'."""
        low = text.lower()
        best_domain = "general"
        best_count  = 0
        for domain, vocab in _DOMAIN_VOCAB.items():
            count = sum(1 for word in vocab if word in low)
            if count > best_count:
                best_count  = count
                best_domain = domain
        if best_count < 2:
            return "general"
        return best_domain

    async def retrieve(self, problem: str, topic_override: str = "") -> str:
        """
        Detect domain, run 2 targeted searches in parallel, return a formatted
        knowledge block ready to inject into VibeMind proposer prompts.
        Returns "" when no domain is detected or searches fail.
        """
        domain = self.detect_domain(problem)
        if domain == "general":
            logger.debug("[domain_retriever] no specific domain detected — skipping")
            return ""

        topic  = topic_override or self._extract_topic(problem)
        templates = _DOMAIN_QUERIES.get(domain, ["{topic}"])[:2]  # max 2 queries
        queries   = [t.replace("{topic}", topic) for t in templates]

        logger.info(f"[domain_retriever] domain={domain} | queries: {queries[0][:50]}")

        try:
            from tools.search import search_stack
            results_list = await asyncio.gather(
                *(search_stack.search(q) for q in queries),
                return_exceptions=True,
            )
        except Exception as exc:
            logger.warning(f"[domain_retriever] search failed: {exc}")
            return ""

        snippets: list[str] = []
        for results in results_list:
            if isinstance(results, Exception) or not results:
                continue
            for r in results[:2]:
                text = r.full_text or r.snippet
                if text and len(text) > 50:
                    snippets.append(f"[{r.title[:60]}]\n{text[:400]}")

        if not snippets:
            return ""

        block = (
            f"[DOMAIN KNOWLEDGE — {domain.upper()} — retrieved from current sources]\n\n"
            + "\n\n".join(snippets)
            + "\n[END DOMAIN KNOWLEDGE]\n\n"
        )
        logger.info(
            f"[domain_retriever] injecting {len(snippets)} snippets "
            f"({len(block)} chars) for domain={domain}"
        )
        return block

    @staticmethod
    def _extract_topic(problem: str) -> str:
        """Pull the core noun phrase from the problem for search queries.

        Found live (2026-07-12), first day this module ever ran against a
        working search backend: the old version took the FIRST 7 words, but
        the subject of an English request lives at the END ("summarize the
        plot of <X>", "get me info about <X>") -- so for "summarize the plot
        and themes of the novel Haunting Adeline" it produced the query
        'summarize the plot and themes of the', dropping the actual topic
        entirely and injecting generic filler pages as "domain knowledge".
        Also, instruction verbs like "summarize"/"give me" weren't in the
        strip list at all. Now: strip leading instruction/question phrases
        (looped, since they stack: "get me info about..."), then keep the
        LAST 7 words, then drop any dangling leading connectives."""
        cleaned = problem.strip().rstrip("?.!").lower()
        prev = None
        while prev != cleaned:
            prev = cleaned
            cleaned = _LEAD_STRIP_RE.sub("", cleaned).strip()
        words = cleaned.split()
        if len(words) > 7:
            words = words[-7:]
        while words and words[0] in _DANGLING_LEADERS:
            words.pop(0)
        return " ".join(words)[:60]


# Singleton
domain_retriever = DomainRetriever()
