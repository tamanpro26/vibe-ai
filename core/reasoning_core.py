"""
core/reasoning_core.py — "VibeMind": the unified reasoning brain

Makes the whole model fleet behave like ONE deep reasoning network instead of a
loose committee. The problem flows through layers of models exactly the way
activations flow through a neural net:

    input
      │
      ▼
  ┌─────────────────────────────────────────────────────────┐
  │  LAYER 1 — proposers     N diverse models reason          │
  │                          INDEPENDENTLY (parallel)         │
  ├─────────────────────────────────────────────────────────┤
  │  LAYER 2 — aggregators   each model reads ALL proposals,  │
  │            critiques, builds on BEST reasoning chain      │
  ├─────────────────────────────────────────────────────────┤
  │  OUTPUT  — finalizer     one model writes the single,     │
  │                          coherent answer                  │
  └─────────────────────────────────────────────────────────┘
      │
      ▼
   answer (+ self-consistency vote)

This is the **Mixture-of-Agents** architecture (Wang et al., 2024).

Active optimisations (v4):
  • Structured proposal format     — 30-40% fewer proposer tokens
  • Proposal deduplication         — 30-50% fewer aggregator input tokens
  • Single aggregator @ ≥80%       — 50% aggregation cost when consensus exists
  • Dynamic token budget           — 30-50% saved on short problems
  • Adaptive budget for complex    — 2× tokens for complex tasks
  • Auto depth=2 for complex       — extra aggregation pass on hard problems
  • Temperature routing            — 0.1 for computational, 0.6 for open-ended
  • Selective debate               — only divergent units cross-examine
  • Lazy verifier                  — skip code execution at 100% numeric consensus
  • Skip finalizer on exec-certain — 3500 tokens saved when verifier ≥95%
  • Skip finalizer for internal    — 3500 tokens saved on non-user-facing calls
  • Session-level cache            — 100% savings on in-session repeats
  • Semantic cache (≥0.90)         — 100% savings on near-identical past problems
  • Compressed memory injection    — 1500 tokens saved per call

Instruction following v4 (NEW):
  • Format detection               — JSON/code/list/table/YAML/length constraints detected
  • Format-aware proposers         — IF_PROPOSER_SYS forces direct format output (no THINKING wrapper)
  • Best-of-N judge selection      — judge picks best-formatted candidate; no blending
  • Format-locked finalizer        — finalizer injected with hard format requirement
  • Format polish pass             — light correction of structural violations

Complex task handling v4 (NEW):
  • Complexity detection           — multi-part tasks auto-detected via patterns + word count
  • Task decomposer                — 2-4 ordered sub-tasks, each solved independently
  • Sub-task integration           — Gemini 2.5 Flash merges sub-results into one answer
  • Chain-of-thought carry-through — aggregator explicitly builds on BEST reasoning chain
"""
from __future__ import annotations

import asyncio
import re
import time
from collections import Counter
from dataclasses import dataclass, field

from loguru import logger

from models.registry import generate_resilient


# ── Shared working memory ──────────────────────────────────────────────────────

@dataclass
class Blackboard:
    """The evolving thought, passed through every layer of the network."""
    problem:    str
    layers:     list[list[tuple[str, str]]] = field(default_factory=list)
    final:      str = ""
    consensus:  str = ""
    agreement:  float = 0.0
    verdict:    object = None


# ── Network units ──────────────────────────────────────────────────────────────

_PROPOSERS   = [
    "qwen36_27b_verifier",      # Qwen3-32B     (Groq)      — reasoning
    "glm_47_cerebras",     # GLM 4.7       (Cerebras)  — reasoning
    "llama33_70b_memory",    # Llama 3.3 70B (Groq)      — Meta family
    "gpt_oss_120b_coord",     # Qwen3-32B     (Groq)      — Alibaba family
    "gemini_flash",          # Gemini 2.5    (Google)    — Google family
]
_AGGREGATORS = ["qwen36_27b_verifier", "gemini_flash"]
_FINALIZER   = "qwen36_27b_verifier"


# ── Format spec ────────────────────────────────────────────────────────────────

@dataclass
class FormatSpec:
    """Describes the output format constraint extracted from the user's request."""
    is_constrained:  bool = False
    fmt_type:        str  = "prose"   # json, yaml, csv, xml, code, list, table, markdown, prose
    language:        str  = ""        # python, javascript, … (for code tasks)
    length_hint:     str  = ""        # "3 sentences", "100 words", …
    raw_requirement: str  = ""        # human-readable constraint injected into prompts


# ── System prompts ─────────────────────────────────────────────────────────────

_PROPOSER_SYS = """You are one independent reasoning unit inside VibeMind.
Solve the problem with rigorous step-by-step reasoning. Question your own
assumptions, consider edge cases, and check your work.

If PAST MEMORY FROM COLLECTIVE is provided before the problem, treat it as a
hint — verify it independently and build on it, do not copy it blindly.

Structure your response EXACTLY as:
THINKING:
• <key reasoning step 1>
• <key reasoning step 2>
• <key reasoning step 3>
(3-5 bullets — dense, no padding or restating the question)

ANSWER: <your final answer in 1-3 sentences>"""

_IF_PROPOSER_SYS = """\
You are one expert unit in VibeMind solving a format-constrained task.

MANDATORY FORMAT REQUIREMENT: {format_requirement}

Think briefly, then produce:
ANSWER:
<your complete output in the required format>

Rules:
- ANSWER must be the COMPLETE output — not a description or summary of it
- Do NOT wrap the output in prose explanations
- Preserve exact syntax for code/JSON/YAML/CSV
- If a length constraint is given, respect it exactly
- Code must be fully working, not pseudocode"""

_AGGREGATOR_SYS = """\
You are an aggregation unit inside VibeMind. Below is a problem and several
independent solutions. Some may be wrong.

NOTE: Proposals marked "[N units agreed]" show multiple models converging —
treat convergence as strong evidence, but still verify the underlying reasoning.

Your job — in this order:
1. Identify the BEST reasoning chain across all proposals (not just the popular answer)
2. Build upon it: correct errors you spot, fill reasoning gaps, add missing edge cases
3. Synthesise ONE improved answer that surpasses any individual proposal

Preserve: code blocks exactly, structured formats (JSON/YAML/CSV), specific numbers.
End with: ANSWER: <your improved answer>"""

_FINALIZER_SYS = """\
You are the output layer of VibeMind. You are given the problem and the
network's refined solutions. Produce the final answer for the user:
correct, clear, and well-structured. Preserve any code blocks exactly.
Do not mention the internal units or layers — speak with one voice."""

_DEBATE_SYS = """You are a reasoning unit in a structured debate inside VibeMind.
You are given the problem, YOUR previous answer, and the answers of UNITS THAT
DISAGREED with you (units that agreed with you are not shown — focus on the
challenge, not defence of what already agrees with you).

Where their reasoning is genuinely stronger than yours, adopt it. Where yours
is correct, defend and sharpen it. Do NOT cave just to agree.
End with exactly: ANSWER: <answer>"""

_JUDGE_SYS = """\
You are a quality judge evaluating candidate outputs for a format-constrained task.
Select the BEST candidate based on:
1. Correct format and structure (most important)
2. Accuracy and completeness of content
3. Code correctness / JSON validity / list completeness as applicable

Respond with exactly:
SELECTED: <candidate number, e.g. 1>
REASON: <one sentence>"""

_DECOMP_SYS = """\
You are a task decomposition specialist inside VibeMind.
Break the complex task into 2-4 ordered, self-contained sub-tasks.
Each sub-task must be independently solvable and contain enough context to stand alone.
Together the sub-tasks must fully solve the original task.

Format:
SUBTASK 1: <complete description with all needed context>
SUBTASK 2: <complete description with all needed context>
...

If the task is already simple and needs no decomposition, reply only: SINGLE"""

_INTEGRATE_SYS = """\
You are an integration specialist in VibeMind.
You have the original task and completed results for each sub-task.
Merge them into a single, coherent, complete final answer.
- Remove duplication
- Ensure logical flow between sections
- Preserve all important information from every sub-task result
- Maintain any format required by the original task"""

# ── Agentic planner prompt ─────────────────────────────────────────────────────
# Used by plan() — not by reason(). Produces a concrete implementation spec
# for the agent loop, not a reasoning trace.
_AGENTIC_PLANNER_SYS = """\
You are a senior software architect and UX expert inside VibeMind's planning layer.
A coding agent will execute your plan — your job is to make it impossible for the
agent to produce a half-finished result.

Given the task, produce a COMPREHENSIVE IMPLEMENTATION SPEC with:

SECTIONS / COMPONENTS (list every section/page/component that must exist):
  • Name — purpose, exact content (real text, not "lorem ipsum"), child elements
  • If a nav link exists → that section MUST appear in the spec

DESIGN SPEC:
  • Color palette (hex values), typography (font names + weights), spacing
  • Component style: glassmorphism / neumorphism / flat / etc.
  • Animation plan: which elements animate, how (GSAP, CSS, Lenis), timing

IMPLEMENTATION STEPS (ordered, concrete):
  1. Install dependencies: list exact package names
  2. Design assets: list every design_asset call with description + asset_type
  3. Files to create/edit: filename, purpose, key code decisions
     (React: ONE component per file under src/components/ — Header.jsx, Hero.jsx, ... —
      imported by App.jsx; never a single monolithic App.jsx with everything inline)
  4. Build & verify: test command + what success looks like

QUALITY GATES — agent must not finish until ALL are met:
  • Every nav link has a corresponding built section
  • No placeholder text (real content only)
  • All animations are wired (not just installed)
  • Each React section lives in its own src/components/ file
  • Build command exits 0

Be specific. Vague plans produce vague results."""


# ── Complexity detection ────────────────────────────────────────────────────────

_COMPLEX_RE = [re.compile(p, re.IGNORECASE) for p in [
    r"\b(design|architect|implement|build)\b.{0,40}\b(system|platform|architecture|service|api|database|schema|pipeline|framework)\b",
    r"\b(microservice|distributed|scalable|enterprise|full.stack|end.to.end)\b",
    r"\b(complete|comprehensive|full|detailed)\b.{0,20}\b(solution|implementation|guide|plan|roadmap|breakdown)\b",
    r"\b(step.by.step|phase.by.phase|in.stages)\b",
    r"\b(multiple|several|various)\b.{0,20}\b(component|module|layer|service|feature)\b",
]]


def _is_complex(problem: str) -> bool:
    """Detect multi-part / architectural tasks that benefit from decomposition."""
    if len(problem.split()) > 120:
        return True
    return any(p.search(problem) for p in _COMPLEX_RE)


# ── Format detection ────────────────────────────────────────────────────────────

_FORMAT_ACTION_RE = re.compile(
    r"\b(write|create|generate|give|produce|output|return|make|build|format|convert|show|provide|list)\b",
    re.IGNORECASE,
)
_FORMAT_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\bjson\b", re.IGNORECASE), "json"),
    (re.compile(r"\byaml\b|\byml\b", re.IGNORECASE), "yaml"),
    (re.compile(r"\bcsv\b", re.IGNORECASE), "csv"),
    (re.compile(r"\bxml\b", re.IGNORECASE), "xml"),
    (re.compile(r"\bmarkdown\b", re.IGNORECASE), "markdown"),
    (re.compile(r"\b(numbered|bullet|bulleted)\s+list\b|\blist\s+(of|the)\b", re.IGNORECASE), "list"),
    (re.compile(r"\b(as\s+a\s+|in\s+a?\s*)(table|grid)\b", re.IGNORECASE), "table"),
]
_CODE_RE = re.compile(
    r"\b(write|implement|create|build|code)\b.{0,60}\b(function|class|script|program|module|api|app|component|snippet|method)\b",
    re.IGNORECASE,
)
_LANG_RE = re.compile(
    r"\b(python|javascript|typescript|rust|go|golang|java|html|css|sql|bash|shell|c\+\+|c#|swift|kotlin|php|ruby)\b",
    re.IGNORECASE,
)
_LENGTH_RE = re.compile(
    r"\b(in|within|exactly|at most|no more than)\s+(\d+)\s+(words?|sentences?|lines?|paragraphs?|characters?)\b",
    re.IGNORECASE,
)


def _detect_format(problem: str) -> FormatSpec:
    """
    Fast keyword-based format constraint detection. Zero latency — no model call.
    Covers: JSON, YAML, CSV, XML, Markdown, list, table, code, length constraints.
    """
    has_action = bool(_FORMAT_ACTION_RE.search(problem))

    for pattern, fmt_type in _FORMAT_PATTERNS:
        if pattern.search(problem):
            if has_action or re.search(rf"\b(in|as)\s+{fmt_type}\b", problem, re.IGNORECASE):
                return FormatSpec(
                    is_constrained=True,
                    fmt_type=fmt_type,
                    raw_requirement=f"Output MUST be in valid {fmt_type.upper()} format",
                )

    # Code detection — action verb + code artifact OR action + programming language
    if _CODE_RE.search(problem) or (has_action and _LANG_RE.search(problem)):
        lang_m = _LANG_RE.search(problem)
        lang   = lang_m.group(1).lower() if lang_m else ""
        return FormatSpec(
            is_constrained=True,
            fmt_type="code",
            language=lang,
            raw_requirement=(
                f"Output must be complete, working {'`' + lang + '` ' if lang else ''}code"
            ),
        )

    # Length constraint
    length_m = _LENGTH_RE.search(problem)
    if length_m:
        hint = length_m.group(0)
        return FormatSpec(
            is_constrained=True,
            fmt_type="prose",
            length_hint=hint,
            raw_requirement=f"Response MUST be: {hint}",
        )

    return FormatSpec(is_constrained=False, fmt_type="prose")


# ── Session-level in-memory cache ──────────────────────────────────────────────

_SESSION_CACHE: dict[str, tuple[str, float]] = {}
_SESSION_TTL   = 600


def _session_key(problem: str) -> str:
    return re.sub(r"\s+", " ", problem.strip().lower())[:200]


def _session_get(problem: str) -> str | None:
    key   = _session_key(problem)
    entry = _SESSION_CACHE.get(key)
    if entry and (time.time() - entry[1]) < _SESSION_TTL:
        return entry[0]
    if entry:
        del _SESSION_CACHE[key]
    return None


def _session_put(problem: str, answer: str) -> None:
    _SESSION_CACHE[_session_key(problem)] = (answer, time.time())


# ── Token budget routing ───────────────────────────────────────────────────────

def _proposer_budget(problem: str, is_complex: bool = False) -> int:
    """Scale proposer max_tokens with problem size and complexity."""
    words = len(problem.split())
    if is_complex:
        return min(2048, max(500, words * 8))
    return min(1024, max(150, words * 4))


def _aggregator_budget(is_complex: bool = False) -> int:
    """Aggregator gets 2× budget for complex tasks to preserve reasoning chains."""
    return 2048 if is_complex else 1024


def _proposer_temp(checkable: bool) -> float:
    return 0.1 if checkable else 0.6


# ── Proposal deduplication ─────────────────────────────────────────────────────

def _deduplicate(proposals: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """
    Cluster proposals by their ANSWER: line.
    Multiple proposals with the same answer collapse into ONE annotated entry.
    The aggregator reads fewer tokens but sees the same signal: "[N agreed]".
    """
    clusters: dict[str, list[tuple[str, str]]] = {}
    no_answer: list[tuple[str, str]] = []

    for model, text in proposals:
        found = re.findall(r"ANSWER:\s*(.+)", text, re.DOTALL)
        if found:
            key = _normalise(found[-1][:120])
            clusters.setdefault(key, []).append((model, text))
        else:
            no_answer.append((model, text))

    deduped: list[tuple[str, str]] = []
    for _, group in clusters.items():
        best_model, best_text = max(group, key=lambda x: x[1].count("•"))
        if len(group) > 1:
            annotated = f"[{len(group)} units agreed on this answer]\n{best_text}"
            deduped.append((best_model, annotated))
        else:
            deduped.append((best_model, best_text))
    deduped.extend(no_answer)
    return deduped


class ReasoningCore:
    """VibeMind — Mixture-of-Agents reasoning over the model fleet."""

    async def reason(
        self,
        problem:       str,
        depth:         int  = 1,
        context:       str  = "",
        max_tokens:    int  = 3000,
        verify:        bool = True,
        debate:        bool = True,
        debate_rounds: int  = 1,
        user_facing:   bool = True,
        _subtask:      bool = False,
    ) -> Blackboard:
        """
        _subtask=True: called from the decomposer. Suppresses recursive decomposition
        and collective memory writes (sub-task answers are partial, not standalone).
        user_facing=False: internal brain calls — skip finalizer polish.
        """
        from core.verifier import verifier, Verdict
        from core.collective_memory import collective_memory

        bb  = Blackboard(problem=problem)
        ctx = f"\n\nCONTEXT:\n{context}" if context else ""

        # ── 1. Session cache ──────────────────────────────────────────────────
        cached = _session_get(problem)
        if cached:
            logger.info("[vibemind] session cache hit — skipping pipeline")
            bb.final = bb.consensus = cached
            bb.agreement = 1.0
            return bb

        # ── 2. Semantic cache ─────────────────────────────────────────────────
        if collective_memory._ready and collective_memory._col and collective_memory._col.count() > 0:
            try:
                emb = collective_memory._embed(problem)
                res = collective_memory._col.query(
                    query_embeddings=[emb] if emb else None,
                    query_texts=[problem]  if not emb else None,
                    n_results=1,
                )
                docs, dists, metas = (
                    res.get("documents", [[]])[0],
                    res.get("distances",  [[]])[0],
                    res.get("metadatas",  [[]])[0],
                )
                if docs and dists and (1 - dists[0]) >= 0.90:
                    answer = (metas[0] if metas else {}).get("answer", "")
                    if answer:
                        logger.info(
                            f"[vibemind] semantic cache hit "
                            f"(similarity {1 - dists[0]:.2f}) — skipping pipeline"
                        )
                        bb.final = bb.consensus = answer
                        bb.agreement = 1.0
                        _session_put(problem, answer)
                        return bb
            except Exception as exc:
                logger.debug(f"[vibemind] semantic cache check failed: {exc}")

        # ── 3. Parallel enrichment (memory, domain, Socratic probe) ──────────
        mem_ctx, domain_ctx, probe_queries = await asyncio.gather(
            collective_memory.retrieve(problem),
            self._domain_retrieve(problem),
            self._socratic_probe(problem),
            return_exceptions=True,
        )
        mem_ctx       = mem_ctx       if isinstance(mem_ctx, str)       else ""
        domain_ctx    = domain_ctx    if isinstance(domain_ctx, str)    else ""
        probe_queries = probe_queries if isinstance(probe_queries, str) else ""

        probe_ctx = ""
        if probe_queries:
            probe_ctx = await self._fill_probe_gaps(probe_queries)

        enrichment      = "".join(filter(None, [domain_ctx, probe_ctx, mem_ctx]))
        proposer_prompt = (
            f"{enrichment}PROBLEM:\n{problem}{ctx}" if enrichment
            else f"PROBLEM:\n{problem}{ctx}"
        )

        # ── 4. Classification (synchronous — zero latency) ────────────────────
        fmt_spec   = _detect_format(problem)
        is_complex = _is_complex(problem)

        if fmt_spec.is_constrained:
            logger.info(
                f"[vibemind] format-constrained task: {fmt_spec.fmt_type}"
                + (f" ({fmt_spec.language})" if fmt_spec.language else "")
                + f" | {fmt_spec.raw_requirement}"
            )
        if is_complex:
            logger.info("[vibemind] complex task detected — adaptive budget + decomposition")

        # ── 5. Complex task: decompose → solve sub-tasks → integrate ─────────
        if is_complex and user_facing and not _subtask:
            subtasks = await self._decompose(problem)
            if len(subtasks) > 1:
                logger.info(f"[vibemind] running {len(subtasks)} sub-tasks in parallel")
                sub_bbs = await asyncio.gather(
                    *(
                        self.reason(
                            problem=(
                                f"{st}\n\n"
                                f"[Context: this is part of a larger task — {problem[:120]}]"
                            ),
                            depth=depth,
                            context=context,
                            max_tokens=max(1000, max_tokens // len(subtasks)),
                            verify=verify,
                            debate=debate,
                            debate_rounds=debate_rounds,
                            user_facing=False,
                            _subtask=True,
                        )
                        for st in subtasks
                    ),
                    return_exceptions=True,
                )
                valid = [
                    r.final for r in sub_bbs
                    if not isinstance(r, Exception) and hasattr(r, "final") and r.final
                ]
                if valid:
                    logger.info(f"[vibemind] integrating {len(valid)}/{len(subtasks)} sub-task results")
                    integrated = await self._integrate(problem, valid, fmt_spec)
                    if integrated:
                        bb.final = integrated
                        _session_put(problem, bb.final)
                        return bb
                logger.warning("[vibemind] decomposition failed or empty — falling back to single-pass")

        # ── 6. Checkable detection (drives temp + verifier shortcut) ─────────
        checkable   = verifier.is_checkable(problem)
        prop_temp   = _proposer_temp(checkable)
        prop_budget = _proposer_budget(problem, is_complex)
        agg_budget  = _aggregator_budget(is_complex)

        # Auto depth=2 for complex tasks — extra aggregation pass
        if is_complex and not _subtask:
            depth = max(depth, 2)
            logger.info(
                f"[vibemind] complex mode: depth={depth} | "
                f"prop_budget={prop_budget} | agg_budget={agg_budget}"
            )

        # ── 7. Layer 1: independent proposals ─────────────────────────────────
        proposer_sys = (
            _IF_PROPOSER_SYS.format(format_requirement=fmt_spec.raw_requirement)
            if fmt_spec.is_constrained
            else _PROPOSER_SYS
        )
        logger.info(
            f"[vibemind] layer 1: {len(_PROPOSERS)} units reasoning in parallel"
            + (" (format-constrained mode)" if fmt_spec.is_constrained else "")
        )
        try:
            from core.activity_log import activity_log
            mode = " [format-constrained]" if fmt_spec.is_constrained else ""
            activity_log.log_vibemind(
                "proposers",
                f"{len(_PROPOSERS)}x parallel{mode}: {', '.join(_PROPOSERS)}"
            )
        except Exception:
            pass
        proposals = await self._run_layer(
            _PROPOSERS, proposer_sys,
            prompt=proposer_prompt,
            max_tokens=prop_budget,
            temperature=prop_temp,
        )
        bb.layers.append(proposals)
        if not proposals:
            raise RuntimeError("VibeMind: every proposer failed")

        current           = proposals
        initial_agreement = 0.0

        # ── 8. Format-constrained path: best-of-N judge selection ─────────────
        if fmt_spec.is_constrained and not checkable:
            logger.info(
                f"[vibemind] format path → judge selecting best of "
                f"{len(current)} candidates"
            )
            selected = await self._judge_select(current, problem, fmt_spec)
            if selected:
                if user_facing:
                    bb.final = await self._format_polish(selected, problem, fmt_spec, max_tokens)
                else:
                    bb.final = selected
                bb.consensus = bb.final
                bb.agreement = 1.0

                if not _subtask:
                    try:
                        await collective_memory.remember_success(
                            problem=problem,
                            approach="format-constrained best-of-N",
                            answer=bb.final[:200],
                            method="judge",
                            confidence=0.85,
                        )
                    except Exception:
                        pass
                if bb.final:
                    _session_put(problem, bb.final)
                return bb

        # ── 9. Selective debate (reasoning tasks only) ────────────────────────
        if debate and not checkable and not fmt_spec.is_constrained and len(proposals) >= 2:
            _, initial_agreement = self._vote(proposals)
            if initial_agreement < 0.20:
                actual_rounds = 3
            elif initial_agreement < 0.40:
                actual_rounds = 2
            else:
                actual_rounds = debate_rounds
            if actual_rounds > debate_rounds:
                logger.info(
                    f"[vibemind] adaptive debate: {initial_agreement:.0%} initial "
                    f"agreement → {actual_rounds} rounds"
                )
            current = await self._debate(problem, ctx, current, actual_rounds, bb)

        # ── 10. Proposal deduplication ────────────────────────────────────────
        deduped = _deduplicate(current)
        if len(deduped) < len(current):
            logger.info(
                f"[vibemind] deduplication: {len(current)} proposals → "
                f"{len(deduped)} unique answers sent to aggregator"
            )
        current = deduped

        # ── 11. Self-consistency vote ──────────────────────────────────────────
        bb.consensus, bb.agreement = self._vote(current)
        if bb.consensus:
            logger.info(
                f"[vibemind] consensus: '{bb.consensus[:50]}' ({bb.agreement:.0%})"
            )

        # ── 12. Lazy verifier ─────────────────────────────────────────────────
        skip_verify = (
            checkable
            and bb.agreement == 1.0
            and bool(re.search(r"\b\d+\.?\d*\b", bb.consensus))
        )
        if skip_verify:
            logger.info("[vibemind] lazy verifier: 100% numeric consensus — skipping execution")

        # ── 13. Aggregation ───────────────────────────────────────────────────
        agg_models = _AGGREGATORS[:1] if bb.agreement >= 0.80 else _AGGREGATORS
        if len(agg_models) < len(_AGGREGATORS):
            logger.info(f"[vibemind] single aggregator (agreement {bb.agreement:.0%} ≥ 80%)")
        try:
            from core.activity_log import activity_log
            activity_log.log_vibemind(
                "aggregators",
                f"{', '.join(agg_models)}  (consensus={bb.agreement:.0%}, proposals={len(bb.layers[0]) if bb.layers else 0})"
            )
        except Exception:
            pass

        for d in range(max(0, depth)):
            logger.info(
                f"[vibemind] layer {d + 2}: {len(agg_models)} aggregators "
                f"building on {len(current)} proposals (budget={agg_budget})"
            )
            merged = await self._run_layer(
                agg_models, _AGGREGATOR_SYS,
                prompt=f"PROBLEM:\n{problem}{ctx}\n\n{self._format_solutions(current)}",
                max_tokens=agg_budget, temperature=0.3,
            )
            if merged:
                bb.layers.append(merged)
                current = merged

        # Re-vote after aggregation
        bb.consensus, bb.agreement = self._vote(current)

        # ── 14. Verification ──────────────────────────────────────────────────
        vote_hint = (
            f"\n\nThe network's majority answer is: {bb.consensus} "
            f"({bb.agreement:.0%} of units). Use this unless you find it is wrong."
            if bb.consensus and bb.agreement >= 0.5 else ""
        )
        if verify and not skip_verify:
            if checkable:
                bb.verdict = await verifier.verify(problem, candidate=bb.consensus)
            else:
                bb.verdict = Verdict(
                    verified=bb.agreement >= 0.5, method="debate",
                    ground_truth=bb.consensus, confidence=bb.agreement,
                    evidence="converged after cross-examination",
                )
            hint = bb.verdict.as_hint()
            if hint:
                vote_hint += hint
                if bb.verdict.method == "execution" and bb.verdict.ground_truth not in ("", "NONE"):
                    bb.consensus = bb.verdict.ground_truth
        elif skip_verify:
            bb.verdict = Verdict(
                verified=True, method="consensus",
                ground_truth=bb.consensus, confidence=1.0,
                evidence="100% agreement across 5 diverse model families",
            )

        # ── 15. Output layer ──────────────────────────────────────────────────
        execution_certain = (
            bb.verdict is not None
            and getattr(bb.verdict, "method", "") == "execution"
            and getattr(bb.verdict, "confidence", 0.0) >= 0.95
            and getattr(bb.verdict, "ground_truth", "") not in ("", "NONE")
        )

        # Inject format requirement into finalizer when constrained
        finalizer_sys = _FINALIZER_SYS
        if fmt_spec.is_constrained:
            finalizer_sys += (
                f"\n\nCRITICAL FORMAT REQUIREMENT: {fmt_spec.raw_requirement}. "
                f"Your output MUST match this format exactly — no prose wrapping."
            )

        if execution_certain:
            logger.info("[vibemind] execution certain ≥95% — skipping finalizer")
            bb.final = (
                f"{bb.verdict.ground_truth}\n\n"
                f"*(Verified by code execution — {bb.verdict.confidence:.0%} confidence)*"
            )
        elif not user_facing:
            logger.info("[vibemind] internal call — skipping finalizer")
            bb.final = current[-1][1] if current else bb.consensus
        else:
            logger.info("[vibemind] output layer: synthesising final answer")
            try:
                from core.activity_log import activity_log
                activity_log.log_vibemind("finalizer", _FINALIZER)
            except Exception:
                pass
            bb.final = await generate_resilient(
                _FINALIZER,
                prompt=(
                    f"PROBLEM:\n{problem}{ctx}\n\n"
                    f"{self._format_solutions(current)}{vote_hint}"
                ),
                system=finalizer_sys,
                max_tokens=max_tokens,
                temperature=0.3,
            )

        # ── 16. Collective memory + session cache ─────────────────────────────
        if not _subtask:
            await self._write_memory(
                collective_memory, bb, problem,
                current, initial_agreement, debate and not checkable,
            )
        if bb.final:
            _session_put(problem, bb.final)

        return bb

    # ── Agentic planning (creative/build/design tasks) ─────────────────────────

    async def plan(self, task: str, context: str = "") -> str:
        """
        Lightweight parallel planning pass for creative/build/design tasks.
        3 models produce independent implementation specs; the most comprehensive
        one is selected and returned as a concrete brief for the agent loop.

        Unlike reason(), there is no debate or aggregation — we want diverse
        design perspectives, not convergence. The agent follows the best plan.
        """
        ctx_block = f"\n\nCONTEXT:\n{context}" if context else ""
        prompt = f"TASK TO PLAN:\n{task}{ctx_block}"

        # Two fast planners only — removing Cerebras/Qwen from the parallel gather
        # because asyncio.gather waits for the SLOWEST model. Gemini + Groq Qwen3 are
        # consistently the fastest free options; Cerebras GLM can add 30-90s of lag.
        _PLANNERS = ["gemini_flash", "qwen36_27b_verifier"]

        try:
            from core.activity_log import activity_log
            activity_log.log_vibemind(
                "planner",
                f"2x parallel planning for creative task: {task[:80]}"
            )
        except Exception:
            pass

        logger.info(f"[vibemind/plan] 2 planners running for: {task[:60]}")
        results = await asyncio.gather(
            *(
                generate_resilient(
                    m, prompt=prompt, system=_AGENTIC_PLANNER_SYS,
                    max_tokens=1000, temperature=0.5,
                )
                for m in _PLANNERS
            ),
            return_exceptions=True,
        )

        plans = [r for r in results if isinstance(r, str) and r.strip()]
        if not plans:
            logger.warning("[vibemind/plan] all planners failed — no plan injected")
            return ""

        # Pick the longest plan (most comprehensive) and polish with Gemini
        best = max(plans, key=len)

        try:
            merged = await generate_resilient(
                "gemini_flash",
                prompt=(
                    f"TASK: {task}\n\n"
                    f"PLAN FROM PLANNING LAYER:\n{best}\n\n"
                    f"Refine this into the definitive implementation spec. "
                    f"Ensure every nav link has a section, every section has real content, "
                    f"and the quality gates are clear and specific."
                ),
                system=_AGENTIC_PLANNER_SYS,
                max_tokens=1500,
                temperature=0.2,
            )
            return merged or best
        except Exception:
            return best

    # ── Decomposition ──────────────────────────────────────────────────────────

    async def _decompose(self, problem: str) -> list[str]:
        """
        Split a complex task into 2-4 ordered, self-contained sub-tasks.
        Returns [problem] unchanged if the task is already simple.
        """
        try:
            raw = await generate_resilient(
                "glm_47_cerebras",
                prompt=f"Complex task to decompose:\n\n{problem}",
                system=_DECOMP_SYS,
                max_tokens=500,
                temperature=0.2,
            )
            if not raw or "SINGLE" in raw.upper():
                return [problem]
            subtasks = re.findall(
                r"SUBTASK\s*\d+:\s*(.+?)(?=SUBTASK\s*\d+:|$)",
                raw, re.DOTALL | re.IGNORECASE,
            )
            subtasks = [s.strip() for s in subtasks if s.strip()]
            if 2 <= len(subtasks) <= 4:
                logger.info(
                    f"[vibemind] decomposed: {[s[:45] + '...' for s in subtasks]}"
                )
                return subtasks
        except Exception as exc:
            logger.debug(f"[vibemind] decomposition skipped: {exc}")
        return [problem]

    async def _integrate(
        self,
        problem:     str,
        sub_results: list[str],
        fmt_spec:    FormatSpec,
    ) -> str:
        """Merge sub-task results into one coherent final answer via Gemini 2.5 Flash."""
        combined = "\n\n".join(
            f"[Sub-task {i + 1} result]:\n{r}" for i, r in enumerate(sub_results)
        )
        system = _INTEGRATE_SYS
        if fmt_spec.is_constrained:
            system += f"\n\nIMPORTANT: {fmt_spec.raw_requirement}. The merged output must respect this format."
        return await generate_resilient(
            "gemini_flash",
            prompt=f"ORIGINAL TASK:\n{problem}\n\n{combined}",
            system=system,
            max_tokens=4000,
            temperature=0.2,
        )

    # ── Format-constrained selection ───────────────────────────────────────────

    async def _judge_select(
        self,
        proposals: list[tuple[str, str]],
        problem:   str,
        fmt_spec:  FormatSpec,
    ) -> str:
        """
        Best-of-N: Gemini 2.5 Flash picks the best-formatted candidate.
        No blending — avoids the format-corruption problem of MoA aggregation.
        """
        candidates: list[str] = []
        for _, text in proposals:
            found = re.findall(r"ANSWER:\s*(.+)", text, re.DOTALL)
            candidates.append(found[-1].strip() if found else text.strip())

        if len(candidates) == 1:
            return candidates[0]

        cand_block = "\n\n".join(
            f"CANDIDATE {i + 1}:\n{c}" for i, c in enumerate(candidates)
        )
        try:
            verdict = await generate_resilient(
                "gemini_flash",
                prompt=(
                    f"TASK: {problem}\n\n"
                    f"FORMAT REQUIREMENT: {fmt_spec.raw_requirement}\n\n"
                    f"{cand_block}"
                ),
                system=_JUDGE_SYS,
                max_tokens=150,
                temperature=0.1,
            )
            sel_m = re.search(r"SELECTED:\s*(\d+)", verdict)
            if sel_m:
                idx = int(sel_m.group(1)) - 1
                if 0 <= idx < len(candidates):
                    logger.info(f"[vibemind] judge selected candidate {idx + 1}")
                    return candidates[idx]
        except Exception as exc:
            logger.warning(
                f"[vibemind] judge selection failed ({exc!s:.40}) — "
                "falling back to longest candidate"
            )
        return max(candidates, key=len)

    async def _format_polish(
        self,
        output:     str,
        problem:    str,
        fmt_spec:   FormatSpec,
        max_tokens: int,
    ) -> str:
        """
        Light polish pass: validate structure, fix format violations,
        strip prose wrapping around machine-readable output (JSON/code/CSV).
        """
        try:
            return await generate_resilient(
                _FINALIZER,
                prompt=(
                    f"TASK: {problem}\n\n"
                    f"FORMAT REQUIREMENT: {fmt_spec.raw_requirement}\n\n"
                    f"DRAFT OUTPUT:\n{output}\n\n"
                    f"Return this output in the correct format. "
                    f"Fix any format violations. "
                    f"Remove prose wrapping if the task requires raw {fmt_spec.fmt_type}. "
                    f"Do NOT add explanatory text around the {fmt_spec.fmt_type} output."
                ),
                system=_FINALIZER_SYS + f"\nCRITICAL: {fmt_spec.raw_requirement}",
                max_tokens=max_tokens,
                temperature=0.1,
            )
        except Exception:
            return output

    # ── Knowledge enrichment helpers ───────────────────────────────────────────

    @staticmethod
    async def _domain_retrieve(problem: str) -> str:
        try:
            from tools.domain_retriever import domain_retriever
            return await domain_retriever.retrieve(problem)
        except Exception as exc:
            logger.debug(f"[vibemind] domain retrieval skipped: {exc}")
            return ""

    @staticmethod
    async def _socratic_probe(problem: str) -> str:
        _PROBE_SYS = (
            "You are a knowledge-gap analyst. Before solving the problem, "
            "identify 2–3 SPECIFIC things that would help answer it accurately. "
            "Format each as: NEED TO KNOW: <specific fact or data>\n"
            "Be concrete. If the problem is self-contained, reply with NONE."
        )
        try:
            raw = await generate_resilient(
                "glm_47_cerebras",
                prompt=f"Problem: {problem}",
                system=_PROBE_SYS,
                max_tokens=200,
                temperature=0.2,
            )
            needs = re.findall(r"NEED TO KNOW:\s*(.+)", raw)
            if needs:
                logger.info(f"[vibemind] socratic probe: {len(needs)} gaps identified")
            return "\n".join(needs) if needs else ""
        except Exception as exc:
            logger.debug(f"[vibemind] socratic probe skipped: {exc}")
            return ""

    @staticmethod
    async def _fill_probe_gaps(gaps: str) -> str:
        if not gaps:
            return ""
        try:
            from tools.search import search_stack
            queries      = [g.strip() for g in gaps.split("\n") if g.strip()][:2]
            results_list = await asyncio.gather(
                *(search_stack.search(q) for q in queries),
                return_exceptions=True,
            )
            snippets: list[str] = []
            for results in results_list:
                if isinstance(results, Exception) or not results:
                    continue
                for r in results[:1]:
                    text = r.full_text or r.snippet
                    if text and len(text) > 40:
                        snippets.append(f"[{r.title[:50]}]: {text[:350]}")
            if not snippets:
                return ""
            block = (
                "[RETRIEVED KNOWLEDGE — specific gaps filled by web search]\n\n"
                + "\n\n".join(snippets)
                + "\n[END RETRIEVED KNOWLEDGE]\n\n"
            )
            logger.info(f"[vibemind] probe gaps filled: {len(snippets)} snippets")
            return block
        except Exception as exc:
            logger.debug(f"[vibemind] gap fill skipped: {exc}")
            return ""

    async def _write_memory(
        self,
        cm,
        bb:                Blackboard,
        problem:           str,
        final_layer:       list[tuple[str, str]],
        initial_agreement: float,
        debate_ran:        bool,
    ) -> None:
        v = bb.verdict
        if v is None:
            return
        try:
            best_approach = (
                final_layer[-1][1][:600] if final_layer else bb.final[:600]
            )
            if v.method in ("execution", "constraints", "consensus") and v.verified and v.ground_truth:
                await cm.remember_success(
                    problem=problem, approach=best_approach,
                    answer=v.ground_truth, method=v.method,
                    confidence=v.confidence,
                )
            elif v.method in ("execution", "constraints") and not v.verified and v.ground_truth:
                if bb.consensus and _normalise(bb.consensus) != _normalise(v.ground_truth):
                    await cm.remember_failure(
                        problem=problem,
                        wrong_approach=f"Model consensus was: {bb.consensus}",
                        correction=v.ground_truth,
                    )
            elif debate_ran and v.method == "debate" and v.confidence >= 0.6:
                if bb.agreement > initial_agreement + 0.1:
                    await cm.remember_debate(
                        problem=problem,
                        winning_consensus=bb.consensus,
                        agreement_before=initial_agreement,
                        agreement_after=bb.agreement,
                    )
        except Exception as exc:
            logger.warning(f"[vibemind] collective memory write failed: {exc}")

    # ── Layer runner ───────────────────────────────────────────────────────────

    async def _run_layer(
        self, models: list[str], system: str,
        prompt: str, max_tokens: int, temperature: float,
    ) -> list[tuple[str, str]]:
        results = await asyncio.gather(
            *(
                generate_resilient(
                    m, prompt=prompt, system=system,
                    max_tokens=max_tokens, temperature=temperature,
                )
                for m in models
            ),
            return_exceptions=True,
        )
        out: list[tuple[str, str]] = []
        for m, r in zip(models, results):
            if isinstance(r, Exception):
                logger.warning(f"[vibemind] unit {m} failed: {str(r)[:60]}")
            elif r and r.strip():
                out.append((m, r))
        return out

    # ── Selective debate ───────────────────────────────────────────────────────

    async def _debate(
        self, problem: str, ctx: str,
        units: list[tuple[str, str]], rounds: int, bb: Blackboard,
    ) -> list[tuple[str, str]]:
        """
        Selective multi-agent debate: only DIVERGENT units cross-examine.
        Units that already agree with the majority hold their answer and skip
        debate (except one majority representative who defends it).
        Saves 50-60% of debate tokens vs debating all 5 every round.
        """
        current = units
        for r in range(max(1, rounds)):
            _, before       = self._vote(current)
            majority_answer, _ = self._vote(current)
            logger.info(f"[vibemind] debate round {r + 1}: {len(current)} units")

            async def revise(
                idx: int, model: str, mine: str,
                is_majority_rep: bool,
            ) -> tuple[str, str]:
                found    = re.findall(r"ANSWER:\s*(.+)", mine)
                my_ans   = _normalise(found[-1]) if found else ""
                peers    = []
                for j, (_, txt) in enumerate(current):
                    if j == idx:
                        continue
                    other_found = re.findall(r"ANSWER:\s*(.+)", txt)
                    other_ans   = _normalise(other_found[-1]) if other_found else ""
                    if my_ans and other_ans and _normalise(my_ans) == _normalise(other_ans):
                        continue
                    peers.append(f"--- Unit {j + 1} ---\n{txt}")
                if not peers:
                    return (model, mine)
                prompt = (
                    f"PROBLEM:\n{problem}{ctx}\n\n"
                    f"YOUR PREVIOUS ANSWER:\n{mine}\n\n"
                    f"DISAGREEING UNITS' ANSWERS:\n" + "\n\n".join(peers)
                )
                try:
                    new = await generate_resilient(
                        model, prompt=prompt, system=_DEBATE_SYS,
                        max_tokens=800, temperature=0.3,
                    )
                    return (model, new if new and new.strip() else mine)
                except Exception as exc:
                    logger.warning(
                        f"[vibemind] debate: {model} held previous ({str(exc)[:40]})"
                    )
                    return (model, mine)

            majority_rep_idx = None
            tasks_args       = []
            for i, (model, txt) in enumerate(current):
                found  = re.findall(r"ANSWER:\s*(.+)", txt)
                ans    = _normalise(found[-1]) if found else ""
                is_maj = majority_answer and ans == _normalise(majority_answer)
                if is_maj and majority_rep_idx is None:
                    majority_rep_idx = i
                    tasks_args.append((i, model, txt, True))
                elif is_maj:
                    tasks_args.append(None)
                else:
                    tasks_args.append((i, model, txt, False))

            revised: list[tuple[str, str]]   = []
            active_tasks: list               = []
            hold_positions: dict[int, tuple] = {}
            for i, args in enumerate(tasks_args):
                if args is None:
                    hold_positions[i] = current[i]
                else:
                    active_tasks.append((i, asyncio.ensure_future(revise(*args))))

            results = await asyncio.gather(
                *(t for _, t in active_tasks), return_exceptions=True
            )
            task_results = {idx: r for (idx, _), r in zip(active_tasks, results)}

            for i, (model, txt) in enumerate(current):
                if i in hold_positions:
                    revised.append(hold_positions[i])
                elif i in task_results:
                    r = task_results[i]
                    revised.append(r if not isinstance(r, Exception) else (model, txt))
                else:
                    revised.append((model, txt))

            current = revised
            bb.layers.append(list(current))
            _, after = self._vote(current)
            logger.info(
                f"[vibemind] debate round {r + 1}: "
                f"agreement {before:.0%} → {after:.0%}"
            )
            if after >= 0.80:
                break

        return list(current)

    # ── Helpers ────────────────────────────────────────────────────────────────

    @staticmethod
    def _format_solutions(solutions: list[tuple[str, str]]) -> str:
        return "\n\n".join(
            f"=== Solution {i + 1} ===\n{text}"
            for i, (_, text) in enumerate(solutions)
        )

    @staticmethod
    def _vote(solutions: list[tuple[str, str]]) -> tuple[str, float]:
        answers = []
        for _, text in solutions:
            found = re.findall(r"ANSWER:\s*(.+)", text)
            if found:
                answers.append(_normalise(found[-1]))
        if not answers:
            return "", 0.0
        winner, count = Counter(answers).most_common(1)[0]
        return winner, count / len(answers)


def _normalise(answer: str) -> str:
    a = answer.strip()
    a = a.strip("*_`").strip()
    a = a.rstrip(".").strip()
    a = re.sub(r"\s+", " ", a)
    return a[:120]


# Singleton — the one mind
reasoning_core = ReasoningCore()
