"""
core/agent_loop.py
Agentic execution loop with real-time callback hooks for the CLI.

The CLI registers callbacks that get called at every step:
  on_thinking(text)          — model's reasoning text
  on_tool_call(name, args)   — about to run a tool
  on_tool_result(result)     — tool finished
  on_iteration(n, model)     — starting iteration N
  on_final(AgentResult)      — task complete
"""
from __future__ import annotations
import asyncio, json, re, time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Awaitable
from loguru import logger
from core.compact_style import with_compact_style
from tools.agent_tools import (
    ToolExecutor, TOOL_SCHEMAS, TOOL_SCHEMAS_CORE, TOOL_SCHEMAS_MINIMAL,
    _SSH_SCHEMAS, _GITHUB_SCHEMA, DEFAULT_WORKSPACE, select_tools_for_budget,
    ToolResult,
)


def _compact_tool_budget(connector) -> int:
    """Token budget available for TOOL SCHEMAS on a tight-budget (compact)
    call, derived from the model's real per-minute limit minus reserves for
    the compacted messages and the model's own output. Replaces the old
    fixed 6-tool minimal set — see select_tools_for_budget() for why."""
    provider = str(connector.model_def.provider)
    tpm = 6_000
    try:
        if provider == "groq":
            from models.connectors.groq_conn import GROQ_TPM, GROQ_TPM_DEFAULT
            tpm = GROQ_TPM.get(connector.api_model, GROQ_TPM_DEFAULT)
        elif provider == "cerebras":
            from models.connectors.cerebras_conn import CerebrasConnector
            tpm = CerebrasConnector._DISCOVERED_LIMITS.get(connector.api_model, 8_192)
    except Exception:
        tpm = 6_000
    # Reserve ~2.5k for output + ~1.5k for compacted messages; floor keeps the
    # essential file/bash tools always affordable.
    return max(500, tpm - 4_000)

# Fallback chain when the primary agent model is rate-limited or unavailable.
# Restructured 2026-06-30 (evening) alongside the model-survey swaps:
#
# PRIMARY is now glm_47_cerebras (GLM-4.7 @ Cerebras) — set in cli.py. It earned
# it empirically: 67 logged calls with zero failures, 1M tokens/day free, and
# it single-handedly produced the best build of the session (the Clearwater
# site) after every other tier rate-limited out. The old primary (Gemini 2.5
# Flash) has a 20 req/day free cap — 34 rate-limit failures in the logs — so
# as "primary" it survived ~1 iteration per task before handing off anyway.
# glm_47_cerebras therefore MUST NOT appear in the fallback list below (the tier-
# detection logic compares connector.model_id against these constants, and a
# primary that equals a fallback would be misidentified as already-failed-over).
#
# Tier 1: gpt_oss_120b_debug (openai/gpt-oss-120b @ Groq). Llama 4 Scout was
# deprecated by Groq on 2026-06-17 AND was the weakest performer in live
# testing; gpt-oss-120b has a 0% failure rate in our logs.
#
# Tier 2: llama33_70b_coder (Llama 3.3 70B @ Groq). Good quality, but real
# limits are 1,000 req/day + 100k tokens/day — fine as a mid tier. Its known
# text-format tool-calling regression is actively recovered from in
# groq_conn.py's _parse_failed_generation.
#
# Tier 3: glm_47_flash_zai (GLM-4.7-Flash @ Z.AI) — added 2026-07 specifically
# because tiers 1+2 are BOTH Groq, and the primary is Cerebras: a Groq-wide
# rate-limit window plus a Cerebras context-limit flap (both observed live, in
# the same session) took out every tier at once. Z.AI is an independent
# provider, so it survives that correlated failure.
#
# Tier 4: deepseek_v4_flash_nim (DeepSeek-V4-Flash @ NVIDIA NIM) — deepest,
# thinnest tier (~40 RPM shared across the whole key). Meant to be rarely
# reached; existing purely as one more independent-provider option before the
# run gives up entirely.
#
# _FALLBACK_CHAIN is the SINGLE source of truth for tier order — three
# different call sites used to each hardcode their own tuple-comparison logic
# for "what's the next tier," which is exactly the kind of drift that caused
# the Groq TPM-table bug earlier. Use _next_fallback_tier() everywhere instead.
_FALLBACK_CHAIN = ["gpt_oss_120b_debug", "llama33_70b_coder", "glm_47_flash_zai", "deepseek_v4_flash_nim"]


def _next_fallback_tier(current_model_id: str) -> str | None:
    """Given the currently-active model_id, return the next tier to try, or
    None if there are no more tiers (current is either the primary — start at
    tier 0 — or already the last fallback tier)."""
    if current_model_id not in _FALLBACK_CHAIN:
        return _FALLBACK_CHAIN[0] if _FALLBACK_CHAIN else None
    idx = _FALLBACK_CHAIN.index(current_model_id)
    return _FALLBACK_CHAIN[idx + 1] if idx + 1 < len(_FALLBACK_CHAIN) else None


# ── Per-call watchdog (Phase 0.2, UPGRADE_ROADMAP.md §5) ────────────────────────
# Live-caught incident (2026-07-16): a single model call sat with zero logged
# activity for ~110 minutes mid-run. Every connector already sets a 30s HTTP
# timeout (config/settings.py::default_timeout_ms, added 2026-07-13) and
# generate_with_tools retries up to 3x with backoff -- worst-case legitimate
# duration for one call is bounded well under 3 minutes. That means the
# incident almost certainly wasn't an in-process hang this can catch (most
# likely the host OS suspended the whole process — no asyncio-level watchdog
# can fire if the process itself isn't scheduled). This wrapper is a genuine
# backstop anyway: it bounds ANY coroutine stall, from any cause, at the
# task-cancellation level rather than trusting every call site to have its
# own timeout right. A tripped watchdog raises a plain RuntimeError, so every
# existing except-Exception fallback/escalation path below treats it exactly
# like any other connector failure and moves to the next tier — no new
# control flow, just a hard ceiling on the existing one.
_MODEL_CALL_WATCHDOG_S = 180.0


async def _watchdog(coro, label: str):
    try:
        return await asyncio.wait_for(coro, timeout=_MODEL_CALL_WATCHDOG_S)
    except asyncio.TimeoutError:
        raise RuntimeError(
            f"{label} exceeded the {_MODEL_CALL_WATCHDOG_S:.0f}s call watchdog — "
            f"treating as a failure so the fallback chain can proceed"
        )


# Compact-format decision is PROVIDER-DRIVEN (see _use_compact_for below), not
# hardcoded per tier: Groq free-tier TPM budgets (~6-12k) cannot fit the full
# ~8k-token TOOL_SCHEMAS_CORE — that exact mistake caused a live 413 ("Limit
# 6000, Requested 11013") when a tier previously sent the full schema to a
# Groq model. Z.AI and NVIDIA get compact treatment too (see _use_compact_for).


def _use_compact_for(connector) -> bool:
    """
    Whether this connector needs the compact message/tool format.
    Groq: always (6-12k TPM budgets). NVIDIA: always (~40 RPM shared/key means
    every request should be as cheap as possible). Z.AI: not tight enough by
    itself to need it, but treated as compact anyway since it only gets
    reached after 2+ other tiers have already failed — keep the message small
    so this deep tier's one shot isn't also blown by an oversized request.
    Cerebras: only when the live context limit has been DISCOVERED to be tight
    — their free-tier limit fluctuates (128k-class behavior on 2026-07-02/03,
    then "limit is 8192" on 07-04, live error body). The connector records
    discovered limits; consult them here. Re-evaluated every iteration, so a
    mid-run discovery flips the format.
    """
    provider = str(connector.model_def.provider)
    if provider in ("groq", "nvidia", "zai"):
        return True
    if provider == "cerebras":
        try:
            from models.connectors.cerebras_conn import CerebrasConnector
            limit = CerebrasConnector._DISCOVERED_LIMITS.get(connector.api_model)
            return limit is not None and limit < 16_000
        except Exception:
            return False
    return False

# ── Selective tool loading ─────────────────────────────────────────────────────
# Groq free tier: 12k TPM. Full TOOL_SCHEMAS ≈ 13,400 tokens, CORE ≈ 8,000 tokens —
# both too large. Fallback uses TOOL_SCHEMAS_MINIMAL (~400 tokens) + compact system
# prompt (~80 tokens) to leave ~9,500 tokens of message budget.
_SSH_KW = {"ssh", "remote server", "remote host", "sftp", "deploy to server"}
_GH_KW  = {"github", " repo ", "pull request", " pr ", "issue tracker", "repository", "workflow"}

def _task_tools(task: str) -> list[dict]:
    t = task.lower()
    tools = list(TOOL_SCHEMAS_CORE)
    if any(kw in t for kw in _SSH_KW):
        tools.extend(_SSH_SCHEMAS)
    if any(kw in t for kw in _GH_KW):
        tools.extend(_GITHUB_SCHEMA)
    return tools


def _build_fallback_messages(messages: list[dict]) -> list[dict]:
    """
    Build a compact message list for tight-TPM (Groq) iterations.
    Keeps all user messages (task + reflexion feedback), a compact trace of the
    agent's OWN prior tool calls, a summary of recent tool outputs, and (see
    below) the ACTUAL CONTENT of files it just wrote. Total stays low so the
    TPM budget is almost entirely free for the model's response.

    The action trace is load-bearing, not decorative: without it the model has
    NO memory of its own actions (assistant turns are dropped for token budget),
    so it re-runs the same exploration call forever — observed live as
    gpt-oss-120b calling list_dir('') six times straight through two LOOP
    WARNINGS, because from its perspective it had never called anything.

    The action trace's 80-char args preview is NOT enough for create_file/
    edit_file: it shows the path, not the content, since content is almost
    always far longer than 80 chars. Verified live (2026-07-07) that this
    starves multi-file tasks specifically — llama33_70b_coder re-created the
    same ~8 files 3-4 times each across one run, every rewrite a similarly
    tiny stub, never building on the previous one, because "created App.jsx
    (18 lines)" (the tool RESULT) confirms a write happened but never shows
    what was actually written. `edit_file` then fails repeatedly too, since
    a correct old_str requires knowing current content. Fixed by surfacing
    the real content of the last few create_file/edit_file calls, not just
    their confirmation strings.
    """
    result = [{"role": "system", "content": _AGENT_SYSTEM_COMPACT}]
    for m in messages:
        if m.get("role") == "user":
            result.append(m)

    # Compact trace of the agent's own prior actions (name + args preview)
    action_lines = []
    file_write_calls: list[tuple[str, str]] = []  # (path, content) for create_file/edit_file
    for m in messages:
        if m.get("role") == "assistant" and m.get("tool_calls"):
            for tc in m["tool_calls"]:
                fn = tc.get("function", {})
                name = fn.get("name", "?")
                raw_args = fn.get("arguments", "")
                args_preview = str(raw_args)[:80]
                action_lines.append(f"- {name}({args_preview})")
                if name in ("create_file", "edit_file"):
                    try:
                        args = raw_args if isinstance(raw_args, dict) else json.loads(raw_args)
                        path = args.get("path", "?")
                        content = args.get("content") if name == "create_file" else args.get("new_str")
                        if content:
                            file_write_calls.append((path, str(content)))
                    except Exception:
                        pass
    if action_lines:
        result.append({
            "role": "user",
            "content": (
                "ACTIONS YOU ALREADY TOOK (do NOT repeat these — continue from here):\n"
                + "\n".join(action_lines[-12:])
            ),
        })

    if file_write_calls:
        # Last 3 file writes, most recent LAST (natural reading order), each
        # capped so a handful of files can't themselves blow the TPM budget —
        # this is memory of "what's actually in the file", not a log entry.
        blocks = [
            f"--- {path} (current content) ---\n{content[:1500]}"
            for path, content in file_write_calls[-3:]
        ]
        result.append({
            "role": "user",
            "content": (
                "CURRENT CONTENT of files you recently wrote (do not blindly "
                "rewrite these from scratch — edit/extend them):\n\n"
                + "\n\n".join(blocks)
            ),
        })

    tool_msgs = [m for m in messages if m.get("role") == "tool"]
    if tool_msgs:
        # The LATEST output gets real room — it's the one the model must act on.
        # Truncating it to 300 chars like the others starves the model of the
        # content it just asked for: observed live as gpt-oss-120b re-reading the
        # same file 6 times because each read came back truncated too short to
        # construct an edit_file old_str from, until the loop guard killed the run.
        lines = [f"- {str(m.get('content', ''))[:300]}" for m in tool_msgs[-10:-1]]
        lines.append(f"- {str(tool_msgs[-1].get('content', ''))[:4000]}")
        result.append({"role": "user", "content": "Recent tool outputs (last one in full):\n" + "\n".join(lines)})
    return result


# ── Hard-reasoning detector ───────────────────────────────────────────────────
# When the task matches these patterns, VibeMind runs first to produce a
# verified plan that the agent then executes. Best of both worlds: MoA
# reasoning for the plan, agent loop for execution.
_HARD_RE = [re.compile(p, re.IGNORECASE) for p in [
    r"\d\s*[\+\-\*/\^%]\s*\d",             # arithmetic expressions
    r"\b(prove|derive|theorem|lemma)\b",
    r"\b(algorithm|time complexity|O\(n)",
    r"\b(probability|permutation|combination)\b",
    r"\b(optimiz|minimiz|maximiz)\w*\b",
    r"\b(design system|architect|database schema)\b",
    r"\b(how many|calculate|compute|sum of|prime)\b",
]]

def _is_hard_reasoning(task: str) -> bool:
    return any(p.search(task) for p in _HARD_RE)

# ── Creative/build planning detector ─────────────────────────────────────────
# When the task is a creative or build task (website, app, UI, landing page),
# VibeMind runs plan() first — producing a detailed implementation spec that
# the agent must follow. This prevents half-finished results (missing sections,
# placeholder content, animations installed but not wired).
_NEEDS_PLAN_RE = [re.compile(p, re.IGNORECASE) for p in [
    r"\b(design|redesign|style|restyle|make.+look|improve.+design|better.+design)\b",
    r"\b(build|create|make|generate)\b.{0,40}\b(website|landing page|web app|dashboard|ui|interface|page|site)\b",
    r"\b(add|implement|build|create)\b.{0,30}\b(section|page|feature|navbar|header|footer|hero|pricing|contact)\b",
    r"\b(animation|animate|scroll|gsap|lenis|framer|motion|transition)\b",
    r"\b(full.?stack|frontend|complete.+app|complete.+site|full.+website)\b",
    r"\b(make|make it).{0,25}(better|great|good|impressive|nicer|cleaner|professional|polished)\b",
    r"\b(improve|enhance|upgrade|revamp|overhaul)\b.{0,30}\b(design|look|feel|quality|ui|website|site)\b",
    r"\b(it looks|looks bad|design is|design looks|not.{0,10}impressive|not.{0,10}good)\b",
]]

def _needs_planning(task: str) -> bool:
    """Creative/build tasks where VibeMind plan() improves output quality."""
    return sum(1 for p in _NEEDS_PLAN_RE if p.search(task)) >= 1

# ── Pre-flight workspace scan detector ────────────────────────────────────────
# For any task involving files, fixing, or inspecting — automatically run
# list_dir before the first model call so even weak models have workspace info.
_SCAN_RE = [re.compile(p, re.IGNORECASE) for p in [
    r"\b(check|look at|see|inspect|explore|what.?s in|what is in)\b",
    r"\b(fix|debug|repair|troubleshoot|help with|improve|update|edit|change)\b",
    r"\b(workspace|files?|project|folder|directory|code|repo)\b",
    r"\b(create|build|make|generate|add|implement)\b",
    r"\b(landing page|app|website|server|script|component)\b",
]]

def _needs_workspace_scan(task: str) -> bool:
    return sum(1 for p in _SCAN_RE if p.search(task)) >= 2

# ── Stalled-build detector ────────────────────────────────────────────────────
# A task that asked for an ARTIFACT (create/write/build a file) but ended with
# zero files touched and a reply that merely NARRATES intent ("I'll create...",
# "Let me build...") is a stall, not a completed answer. Caught live
# (2026-07-24): a plain "create a file hi.txt with the text hello world" got
# "I'll create a single, self-contained ...html file ..." — 0 files, and the
# old guard missed it twice: it was planning-tasks-only AND gated on a <60-char
# length that this ~180-char promise sailed past. Both signals below are needed.
_WANTS_ARTIFACT_RE = re.compile(
    r"\b(create|write|build|make|generate|implement|scaffold|code|produce)\b", re.IGNORECASE)
_PROMISE_ONLY_RE = re.compile(
    r"^\s*(?:ok(?:ay)?[,.!\s]+)?(?:sure[,.!\s]+)?"
    r"(?:i'?ll\b|i will\b|i'?m going to\b|i am going to\b|let me\b|let's\b|"
    r"i plan to\b|i'?m about to\b|here'?s (?:what|how) i|first[,\s])",
    re.IGNORECASE)

def _wants_artifact(task: str) -> bool:
    return bool(_WANTS_ARTIFACT_RE.search(task or ""))

def _promised_not_acted(text: str) -> bool:
    """The reply only promises future action instead of being the result."""
    return bool(_PROMISE_ONLY_RE.match((text or "").strip()))

def _build_system_hint(pkg_json: str) -> str:
    """Return build-system-specific rules to inject into agent context."""
    if not pkg_json:
        return ""
    low = pkg_json.lower()
    if '"vite"' in low:
        return (
            "\n\nBUILD SYSTEM: Vite\n"
            "CRITICAL Vite rules:\n"
            "  • index.html MUST be at the project root — NOT in public/\n"
            "  • Entry script in index.html: <script type=\"module\" src=\"/src/main.jsx\"></script>\n"
            "  • vite.config.js must NOT import app packages (gsap, lenis…) — those go in src/\n"
            "  • Build output: outDir: 'dist' (never 'public' — that's your static assets folder)\n"
            "  • Always run 'npm run build' to verify before finishing\n"
        )
    if '"next"' in low:
        return (
            "\n\nBUILD SYSTEM: Next.js\n"
            "  • Pages go in pages/ or app/ directory\n"
            "  • Always run 'npm run build' to verify before finishing\n"
        )
    if '"react-scripts"' in low or '"webpack"' in low:
        return (
            "\n\nBUILD SYSTEM: Webpack / CRA\n"
            "  • index.html goes in public/ (not project root)\n"
            "  • Entry: src/index.js or src/index.jsx\n"
            "  • Always run 'npm run build' to verify before finishing\n"
        )
    return "\n\nAlways run 'npm run build' to verify the project compiles before finishing.\n"


# ── Callbacks ─────────────────────────────────────────────────────────────────
@dataclass
class AgentCallbacks:
    on_thinking:    Callable[[str], Awaitable] | None    = None
    on_tool_call:   Callable[[str, dict], Awaitable] | None = None
    on_tool_result: Callable[[Any], Awaitable] | None    = None
    on_iteration:   Callable[[int, str], Awaitable] | None = None
    on_final:       Callable[[Any], Awaitable] | None    = None

    async def emit_thinking(self, text: str):
        if self.on_thinking and text.strip():
            await self.on_thinking(text)

    async def emit_tool_call(self, name: str, args: dict):
        if self.on_tool_call:
            await self.on_tool_call(name, args)

    async def emit_tool_result(self, result):
        if self.on_tool_result:
            await self.on_tool_result(result)

    async def emit_iteration(self, n: int, model: str):
        if self.on_iteration:
            await self.on_iteration(n, model)

    async def emit_final(self, result):
        if self.on_final:
            await self.on_final(result)

# ── Result ────────────────────────────────────────────────────────────────────
@dataclass
class AgentResult:
    final_response: str       = ""
    files_created:  list[str] = field(default_factory=list)
    files_edited:   list[str] = field(default_factory=list)
    commands_run:   list[str] = field(default_factory=list)
    iterations:     int       = 0
    total_ms:       float     = 0.0
    workspace:      str       = ""
    # Full text of any plain-document (.txt/.md) deliverable this run wrote,
    # so a frontend can show the actual letter/content instead of just a
    # "file created" confirmation. "" for code/web projects (nothing to dump).
    document_preview: str     = ""

# ── Golden scaffold templates ──────────────────────────────────────────────────
# Known-good boilerplate written deterministically by the harness right after a
# vite scaffold succeeds. Models should never GENERATE boilerplate: vite.config.js
# was corrupted or needlessly rewritten in essentially every live run (duplicate
# export blocks, invalid `createApp` imports, wrong outDir). With these in place
# the model only fills in content — the part it's actually good at — and the
# "CSS file never imported" failure class dies outright because styles.css is
# pre-wired into main.jsx.
_GOLDEN_VITE_CONFIG = """import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  build: { outDir: 'dist' },
});
"""

_GOLDEN_INDEX_HTML = """<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>App</title>
  </head>
  <body>
    <div id="root"></div>
    <script type="module" src="/src/main.jsx"></script>
  </body>
</html>
"""

_GOLDEN_MAIN_JSX = """import React from 'react';
import { createRoot } from 'react-dom/client';
import './styles.css';
import App from './App.jsx';

createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
);
"""

_GOLDEN_STYLES_CSS = """/* All site styles live here — this file is already imported in main.jsx. */
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
"""

# The TODO below is intentional bait: if the model never replaces this skeleton,
# the deterministic verifier battery flags the TODO marker and forces a fix cycle.
_GOLDEN_APP_JSX = """// Compose the page from section components (one file per section in ./components/).
function App() {
  return (
    <div className="app">
      {/* TODO: import section components from ./components/ and render them here */}
    </div>
  );
}

export default App;
"""


# ── Agent loop ────────────────────────────────────────────────────────────────
class AgentLoop:
    # Raised 12 → 18 → 22. At 18, two consecutive live runs (Clearwater, Nimbus)
    # ended AT the cap while mid-way through a legitimate reflexion fix cycle:
    # build passes ~iteration 14, the critic finds real gaps, and the 4 remaining
    # iterations aren't enough to investigate + fix + re-verify cleanly. The cap
    # is a safety net, not a target — the safety nets that make a high cap safe
    # (repetition guard with hard stop, build-verification gate, empty-response
    # guard, graceful fallback exhaustion) all still apply.
    MAX_ITERATIONS = 22

    def __init__(self, workspace: Path = DEFAULT_WORKSPACE) -> None:
        self.executor = ToolExecutor(workspace)

    async def run(
        self,
        task:          str,
        model_id:      str,
        task_type:     str = "coding",
        system:        str = "",
        context:       str = "",
        history:       list[dict] | None = None,
        callbacks:     AgentCallbacks | None = None,
        model_explicit: bool = True,
    ) -> AgentResult:
        from models.registry import registry
        from config.model_params import get_params
        cb = callbacks or AgentCallbacks()

        # Adaptive routing memory (core/routing_memory.py): if the caller did
        # NOT explicitly pin a model (model_explicit=False — the CLI's default),
        # and the memory has confidently learned that a different model tends to
        # succeed on this KIND of task, start with that model instead. Never
        # overrides an explicit /model choice; does nothing until the memory has
        # real evidence (>=3 samples above threshold). This is what turns the
        # per-run success signal below into actual routing improvement.
        _routing_note = ""
        if not model_explicit:
            try:
                from core.routing_memory import get_memory
                from config.models_config import MODEL_REGISTRY
                sugg = get_memory().suggest(task)
                if sugg and sugg.model_id != model_id and sugg.model_id in MODEL_REGISTRY:
                    _routing_note = (
                        f"routing memory → {sugg.model_id} "
                        f"({sugg.confidence:.0%} over {sugg.samples} runs, "
                        f"{'/'.join(sugg.categories)})"
                    )
                    logger.info(f"[agent] {_routing_note} — starting there instead of {model_id}")
                    model_id = sugg.model_id
            except Exception as exc:
                logger.warning(f"[agent] routing memory suggest skipped: {str(exc)[:60]}")

        connector = registry.get(model_id)
        _starting_model = model_id   # recorded against the outcome at run end
        # Every model_id actually attempted this run -- used by
        # core/model_escalation.py so a broader-pool reach-out never retries
        # something that already failed/got stuck earlier in the same run.
        _tried_models: set[str] = {model_id}
        params    = get_params(connector.api_model, task_type)
        result    = AgentResult(workspace=str(self.executor.workspace))
        t0        = time.perf_counter()
        # Tag the executor with this run's task so change-history entries
        # record WHY each file was touched (core/change_history.py).
        self.executor.current_task = task

        # Skills: curated, task-triggered guidance modules encoding real defects
        # this project has already shipped and verified live (see core/skills.py
        # and DECISIONS.md) — independent of the workspace-scan gate below, since
        # this is about code-quality lessons, not workspace context.
        skills_ctx = ""
        try:
            from core.skills import select_skills
            skills_ctx = select_skills(task)
            if skills_ctx:
                logger.info(f"[agent] skills injected ({len(skills_ctx)} chars)")
        except Exception as exc:
            logger.warning(f"[agent] skill selection skipped: {str(exc)[:60]}")

        # Pre-flight workspace scan: for any file/code/fix task, automatically
        # run list_dir and inject the result so even weak models start with context.
        workspace_ctx = ""
        if _needs_workspace_scan(task):
            try:
                listing = await self.executor.list_dir(".")
                listing = listing[:4000]  # hard cap — node_modules etc. must not flood context
                workspace_ctx = f"\n\nWORKSPACE CONTENTS (auto-scanned):\n{listing}\n"
                logger.info(f"[agent] pre-flight workspace scan injected ({len(listing)} chars)")
            except Exception as exc:
                logger.warning(f"[agent] workspace scan skipped: {str(exc)[:60]}")
            try:
                from core.repo_map import build_repo_map
                # Compact-provider tiers already run a tight token budget (see
                # _use_compact_for) — give the map less room there rather than
                # skipping it outright; knowing what functions/classes already
                # exist is exactly what keeps a weak/truncated model from
                # redefining or duplicating them.
                map_budget = 1500 if _use_compact_for(connector) else 4000
                repo_map = build_repo_map(self.executor.workspace, max_chars=map_budget)
                if repo_map:
                    workspace_ctx += (
                        f"\n\nEXISTING CODE MAP (signatures only, not full source "
                        f"— read_file before editing):\n{repo_map}\n"
                    )
                    logger.info(f"[agent] repo map injected ({len(repo_map)} chars)")
            except Exception as exc:
                logger.warning(f"[agent] repo map skipped: {str(exc)[:60]}")
            try:
                pkg = await self.executor.read_file("package.json")
                # read_file returns an "ERROR: ..." string on a missing file rather
                # than raising — must check for that before treating it as real JSON.
                if not pkg.startswith("ERROR"):
                    build_hint = _build_system_hint(pkg)
                    if build_hint:
                        workspace_ctx += build_hint
                        logger.info("[agent] build-system hint injected")
            except Exception:
                pass  # no package.json — not a Node project

        # Ground the planner in what ALREADY EXISTS before it invents a spec.
        # Without this, VibeMind's planner only ever saw a bare filename listing
        # (e.g. "App.jsx", "Header.jsx") with zero knowledge of the actual brand,
        # business, or content inside those files — so on a task like "add more
        # things to my website" it would invent an entirely unrelated theme/business
        # from scratch instead of extending the real one. Reading the actual entry
        # file content fixes this at the source, for both the planner and the agent.
        _existing_site_snippet = ""
        if _needs_planning(task):
            for entry_path in ("src/App.jsx", "src/App.tsx", "src/App.js",
                                "src/app/page.tsx", "pages/index.js", "index.html"):
                try:
                    content = await self.executor.read_file(entry_path)
                except Exception:
                    continue
                if not content.startswith("ERROR") and len(content) > 150:
                    _existing_site_snippet = content[:1200]
                    workspace_ctx += (
                        f"\n\nEXISTING SITE — current content of {entry_path}:\n{content[:2000]}\n\n"
                        f"CRITICAL: This is the user's EXISTING site/business. Unless the task "
                        f"explicitly asks for a redesign, rebuild, or theme change, you MUST preserve "
                        f"its branding, business identity, and existing sections — only ADD or modify "
                        f"what the task specifically requests. Do NOT invent a different theme, "
                        f"business, or purpose."
                    )
                    logger.info(f"[agent] existing-site grounding injected from {entry_path}")
                    break

        # VibeMind pre-reasoning: for hard reasoning tasks, run the full
        # MoA network first to produce a verified plan, then inject it as
        # context so the agent executes a well-reasoned strategy.
        vibemind_ctx = ""
        if _is_hard_reasoning(task):
            try:
                from core.reasoning_core import reasoning_core
                logger.info("[agent] hard reasoning detected — pre-reasoning with VibeMind")
                # Same 25s cap as the sibling _needs_planning branch below --
                # found in code review (2026-07-13): this call had no timeout
                # at all, unlike that sibling, even though reason() fans out
                # to 5 parallel proposers hitting the exact same no-connector-
                # timeout risk (fix #7 above). If any provider is slow or
                # rate-limited this would stall the entire agent before it
                # even starts, same risk the sibling's own comment warns about.
                bb = await asyncio.wait_for(
                    reasoning_core.reason(problem=task, depth=1, max_tokens=2000, user_facing=False),
                    timeout=25.0,
                )
                if bb.final:
                    vibemind_ctx = (
                        f"\n\nVIBEMIND PRE-ANALYSIS (verified multi-model reasoning):\n"
                        f"{bb.final}\n\nUse this analysis to guide your approach."
                    )
                    logger.info("[agent] VibeMind plan injected into agent context")
            except asyncio.TimeoutError:
                logger.warning("[agent] VibeMind pre-reasoning timed out (25s) — proceeding without it")
            except Exception as exc:
                logger.warning(f"[agent] VibeMind pre-reasoning skipped: {str(exc)[:60]}")

        # VibeMind planning: for creative/build/design tasks, run 3 models in
        # parallel to produce a comprehensive implementation spec BEFORE the agent
        # starts. This prevents half-finished results (missing sections, placeholder
        # content, animations installed but not wired up).
        elif _needs_planning(task):
            try:
                from core.reasoning_core import reasoning_core
                logger.info("[agent] creative/build task — VibeMind planning pass (25s cap)")
                # Hard 25-second cap: plan() runs 3 parallel model calls; if any provider is
                # slow or rate-limited this would stall the entire agent before it even starts.
                plan = await asyncio.wait_for(
                    reasoning_core.plan(task=task, context=workspace_ctx),
                    timeout=25.0,
                )
                if plan:
                    vibemind_ctx = (
                        f"\n\nVIBEMIND IMPLEMENTATION SPEC (produced by planning models):\n"
                        f"{plan}\n\n"
                        f"MANDATORY: You MUST implement EVERY section/component listed above. "
                        f"Do not finish until all quality gates are met. "
                        f"STRUCTURE: for React projects, put each section in its own file under "
                        f"src/components/ (Header.jsx, Hero.jsx, ...) and import them in App.jsx — "
                        f"do NOT write the whole page as one monolithic App.jsx."
                    )
                    logger.info("[agent] VibeMind implementation spec injected")
            except asyncio.TimeoutError:
                logger.warning("[agent] VibeMind planning timed out (25s) — proceeding without spec")
            except Exception as exc:
                logger.warning(f"[agent] VibeMind planning skipped: {str(exc)[:60]}")

        # Cross-session lessons: past solved build failures relevant to this
        # task (tools/memory.py, written by _store_build_lesson below). Local
        # ChromaDB + local embeddings -- no network call -- but still capped
        # defensively in case the embedding model's first load is slow.
        lessons_ctx = ""
        try:
            from tools.memory import memory
            await self._ensure_memory_ready(memory)
            retrieved = await asyncio.wait_for(
                memory.retrieve_context(
                    task, team="code", top_k=2,
                    workspace=str(self.executor.workspace.resolve()),
                ),
                timeout=10.0,
            )
            if retrieved:
                lessons_ctx = f"\n\n{retrieved}"
                logger.info("[agent] past-lesson context injected")
        except Exception as exc:
            logger.warning(f"[agent] lesson retrieval skipped: {str(exc)[:60]}")

        messages  = self._build_messages(
            task, system,
            context + skills_ctx + workspace_ctx + vibemind_ctx + lessons_ctx,
            history,
        )
        # Reflexion state — capped at 2 cycles so this can never spin (agent-lessons: infinite loop)
        _reflexion_cycles = 0
        _plan_spec        = vibemind_ctx  # pass spec to the critic so it knows what was planned
        if _existing_site_snippet:
            _plan_spec += (
                f"\n\nORIGINAL SITE CONTENT (before this task ran — branding/business must "
                f"still be present unless a redesign was explicitly requested):\n{_existing_site_snippet}"
            )
        # Compact format is provider-driven from the very first call, not just on
        # fallback: a Groq model selected as PRIMARY (e.g. `/model gpt_oss_120b_coder` for
        # fast runs — Groq is ~5-10x faster per call than Cerebras) has the same
        # tight per-minute token budget as a Groq fallback, and sending it the
        # full ~8k-token schema would 413 immediately.
        _fallback_active  = _use_compact_for(connector)
        _last_tool_sig    = ""            # repetition guard: signature of last single-tool call
        _repeat_count     = 0             # consecutive identical calls counter
        _loop_warnings    = 0             # how many times the soft LOOP WARNING has already fired
        _no_write_iters   = 0             # consecutive tool-using iterations with zero file writes
        _empty_streak     = 0             # consecutive empty/stub responses (handoff at 3)
        _tool_fail_streak = 0             # consecutive tool_use_failed recoveries (handoff at 2)
        # Read-loop dithering guard (core/read_loop_guard.py) — live-enforced
        # (shadow=True validated first on 2026-07-16: zero false positives
        # across 10 legitimate multi-model iterations, correctly detected the
        # exact glm_47_flash_zai read-only dithering pattern that had broken
        # every prior run). Escalates through DECISIONS, never forces an
        # edit: cache short-circuit -> nudge -> decision checkpoint -> model
        # handoff -> named stall. Deliberately isolated: never reads or
        # writes _no_write_iters/_repeat_count/friends above, and vice versa
        # -- see the module docstring for the precedence rule between them.
        from core.read_loop_guard import ReadLoopGuard, GuardConfig, Action as _RLGAction
        _read_loop_guard = ReadLoopGuard(GuardConfig(shadow=False))
        _pressure_made_paths: list[str] = []   # edits landing under nudge/checkpoint pressure
        _build_verified   = False         # True once 'npm run build' passes without errors
        _build_fail_cycles = 0            # consecutive build-gate failures (comparison-judge at 2+)
        _last_build_err   = ""            # text of the most recent build failure, for the lesson store below
        _verifiers_passed = False         # True once the deterministic battery is clean
        _verifier_cycles  = 0             # capped fix cycles driven by the battery
        _vision_done      = False         # vision-loop QA fires at most once per run
        _scaffold_dir     = ""            # set once a "npm create vite" style scaffold succeeds
        _scaffold_is_vite = False         # golden scaffold only applies to vite projects
        _ledger_build     = "never run"   # last known build status, shown in the ledger
        _ledger_findings: list[str] = []  # open deterministic-verifier findings

        # ── Project ledger ─────────────────────────────────────────────────────
        # A harness-maintained state summary injected as a message and REWRITTEN
        # in place every iteration. Weak models fail at keeping state, not at
        # writing code (observed live: a model re-read the same file six times
        # because it had no memory of its own actions). The ledger carries that
        # state for them — and because it's a user-role message, it survives the
        # compact-format rebuild used for tight-TPM (Groq) connectors.
        def _render_ledger(iter_no: int) -> str:
            files = list(dict.fromkeys(result.files_created + result.files_edited))
            proj = ("(workspace root)" if _scaffold_dir in (".", "") else f"{_scaffold_dir}/") \
                   if (_scaffold_dir or files) else "(none yet)"
            lines = [
                "PROJECT LEDGER (system-maintained, always current — trust it over your memory):",
                f"- iteration: {iter_no}/{self.MAX_ITERATIONS}",
                f"- project dir: {proj}",
                f"- last build: {_ledger_build}",
                f"- files created/edited ({len(files)}): "
                + (", ".join(files[-15:]) if files else "(none yet — create files to make progress)"),
            ]
            if _ledger_findings:
                lines.append("- OPEN ISSUES from automated checks (fix these):")
                lines += [f"    * {f}" for f in _ledger_findings[:8]]
            return "\n".join(lines)

        _ledger_idx = len(messages)
        messages.append({"role": "user", "content": _render_ledger(0)})

        logger.info(f"[agent] start | model={model_id} | task={task[:60]}")
        try:
            from core.activity_log import activity_log
            activity_log.log_task_start(task)
        except Exception:
            pass

        for i in range(self.MAX_ITERATIONS):
            result.iterations = i + 1
            messages[_ledger_idx]["content"] = _render_ledger(i + 1)
            # Re-evaluate compact mode each iteration: a Cerebras context-limit
            # discovery mid-run (see _use_compact_for) must flip the format
            # immediately — full format under a live 8k limit strangles the run
            # (observed: context amputated to 6/51 messages, model produced
            # nothing for 8 straight iterations). One-way switch: once compact,
            # stay compact for the rest of the run.
            _fallback_active = _fallback_active or _use_compact_for(connector)
            await cb.emit_iteration(i + 1, connector.api_model)
            try:
                from core.activity_log import activity_log
                activity_log.log_agent_iter(i + 1, connector.api_model)
            except Exception:
                pass

            try:
                if _fallback_active:
                    # A tight-TPM (Groq) connector is active. Compact messages +
                    # a budget-fitted, task-relevant tool set (essentials plus
                    # whatever relevant tools fit the model's real TPM) — instead
                    # of the old fixed 6-tool minimal that stripped design/vision/
                    # git/github entirely. See select_tools_for_budget().
                    response = await _watchdog(connector.generate_with_tools(
                        messages=_build_fallback_messages(messages),
                        tools=select_tools_for_budget(task, _compact_tool_budget(connector)),
                        max_tokens=min(params["max_tokens"], 8192),
                        temperature=params["temperature"],
                        task_type=task_type,
                    ), f"{connector.model_id} (primary, compact)")
                else:
                    response = await _watchdog(connector.generate_with_tools(
                        messages=messages,
                        tools=_task_tools(task),
                        max_tokens=params["max_tokens"],
                        temperature=params["temperature"],
                        task_type=task_type,
                    ), f"{connector.model_id} (primary)")
            except Exception as exc:
                # Fallback chain: primary → tier 1 → tier 2 → tier 3 → tier 4 (see
                # _FALLBACK_CHAIN). Each tier is tried inside its own guard so a
                # failure at ANY level (including a fallback model itself being
                # unavailable) falls through to the next tier instead of raising
                # out and crashing run().
                remaining = []
                _probe = connector.model_id
                while (nxt := _next_fallback_tier(_probe)) is not None:
                    remaining.append(nxt)
                    _probe = nxt

                response  = None
                last_exc  = exc
                for fb_id in remaining:
                    logger.warning(
                        f"[agent] {connector.model_id} failed ({str(last_exc)[:60]}) "
                        f"— switching to fallback: {fb_id}"
                    )
                    try:
                        connector = registry.get(fb_id)
                        _tried_models.add(fb_id)
                        params    = get_params(connector.api_model, task_type)
                        # Compact format is decided by PROVIDER, not by tier position —
                        # sending the full ~8k-token schema to a tight-quota free model
                        # blows its per-minute token budget (observed live as a 413).
                        _fallback_active = _use_compact_for(connector)
                        if _fallback_active:
                            response = await _watchdog(connector.generate_with_tools(
                                messages=_build_fallback_messages(messages),
                                tools=select_tools_for_budget(task, _compact_tool_budget(connector)),
                                max_tokens=min(params["max_tokens"], 8192),
                                temperature=params["temperature"],
                                task_type=task_type,
                            ), f"{connector.model_id} (fallback, compact)")
                        else:
                            response = await _watchdog(connector.generate_with_tools(
                                messages=messages,
                                tools=_task_tools(task),
                                max_tokens=min(params["max_tokens"], 8192),
                                temperature=params["temperature"],
                                task_type=task_type,
                            ), f"{connector.model_id} (fallback)")
                        break  # this tier succeeded
                    except Exception as exc2:
                        last_exc = exc2
                        response = None
                        continue  # try the next tier, if any

                if response is None:
                    # Broader-pool escalation (core/model_escalation.py): the
                    # fixed _FALLBACK_CHAIN is exhausted, but that just means
                    # 5 specific models failed -- not that every capable model
                    # is dead. Reach into the rest of the registry (other
                    # teams/providers) plus any Ollama model actually pulled
                    # locally before truly giving up. User-requested capability
                    # (2026-07-10): the assigned tiers couldn't do it, so reach
                    # for something else automatically rather than failing.
                    try:
                        from core.model_escalation import agentic_candidates
                        escalation_pool = await agentic_candidates(exclude=_tried_models)
                    except Exception as exc_pool:
                        logger.warning(f"[agent] escalation pool lookup failed: {str(exc_pool)[:80]}")
                        escalation_pool = []
                    for esc_id in escalation_pool:
                        logger.warning(
                            f"[agent] all standard tiers failed — reaching beyond the "
                            f"fixed chain to {esc_id}"
                        )
                        try:
                            connector = registry.get(esc_id)
                            _tried_models.add(esc_id)
                            params    = get_params(connector.api_model, task_type)
                            _fallback_active = _use_compact_for(connector)
                            if _fallback_active:
                                response = await _watchdog(connector.generate_with_tools(
                                    messages=_build_fallback_messages(messages),
                                    tools=select_tools_for_budget(task, _compact_tool_budget(connector)),
                                    max_tokens=min(params["max_tokens"], 8192),
                                    temperature=params["temperature"],
                                    task_type=task_type,
                                ), f"{connector.model_id} (escalation, compact)")
                            else:
                                response = await _watchdog(connector.generate_with_tools(
                                    messages=messages,
                                    tools=_task_tools(task),
                                    max_tokens=min(params["max_tokens"], 8192),
                                    temperature=params["temperature"],
                                    task_type=task_type,
                                ), f"{connector.model_id} (escalation)")
                            logger.info(f"[agent] escalation to {esc_id} succeeded")
                            break
                        except Exception as exc3:
                            last_exc = exc3
                            response = None
                            continue

                if response is None:
                    # All available models failed — break gracefully instead of
                    # crashing the entire CLI with an unhandled exception.
                    logger.error(f"[agent] all fallbacks exhausted ({str(last_exc)[:120]})")
                    result.final_response = (
                        f"All models are unavailable right now ({str(last_exc)[:160]}). "
                        f"Please try again in a moment."
                    )
                    break

            # Extract and emit thinking tokens
            thinking = _extract_thinking(response.get("content", "") or "")
            if thinking:
                await cb.emit_thinking(thinking)

            tool_calls = response.get("tool_calls", [])

            if tool_calls:
                _tool_fail_streak = 0

            if not tool_calls:
                # ── Tool-call recovery failure guard ─────────────────────────────
                # Some connectors (e.g. Groq, see groq_conn.py::_call_with_tools)
                # mark tool_call_failed=True when the model attempted a tool call
                # the API rejected (400 tool_use_failed) and the text-format
                # recovery parser couldn't salvage it either. This is NOT a
                # legitimate text answer -- the model was trying to act and
                # couldn't -- so it needs to escalate faster than the generic
                # empty-response guard below, which checks response LENGTH and
                # completely misses this: a failed_generation dump runs up to
                # 2000 chars, so it never looked "empty." Found live
                # (2026-07-15): the same model failed the identical create_file
                # call 5 times in a row across 2 reflexion + 3 verifier fix
                # cycles, shipping a page with a missing stylesheet the
                # deterministic verifier had already correctly flagged every
                # single time -- because nothing upstream ever saw this as a
                # failure at all.
                if response.get("tool_call_failed"):
                    _tool_fail_streak += 1
                    logger.warning(
                        f"[agent] {connector.model_id} tool-call recovery failed "
                        f"({_tool_fail_streak} in a row) at iteration {i+1}"
                    )
                    if _tool_fail_streak >= 2:
                        _next_tier = _next_fallback_tier(connector.model_id)
                        if _next_tier:
                            logger.warning(
                                f"[agent] {_tool_fail_streak} tool-call failures from "
                                f"{connector.model_id} — handing off to {_next_tier}"
                            )
                            connector        = registry.get(_next_tier)
                            _tried_models.add(_next_tier)
                            params           = get_params(connector.api_model, task_type)
                            _fallback_active = _use_compact_for(connector)
                        _tool_fail_streak = 0
                else:
                    _tool_fail_streak = 0

                # Final text response
                content = response.get("content", "")
                result.final_response = _strip_thinking(content)

                # ── Empty/stub response guard ───────────────────────────────────
                # A build/creative task that has touched ZERO files and produced a
                # near-empty response (e.g. "We'll scaffold.") is almost certainly the
                # model stalling, not a legitimate short answer — observed directly
                # under cascading-fallback stress, where a weak tail-end model gave up
                # without writing anything. Force a direct retry instead of spending a
                # critic call (itself fallible) to confirm what's already obvious.
                if ((_needs_planning(task) or _wants_artifact(task))
                        and not result.files_created and not result.files_edited
                        and (len(result.final_response.strip()) < 60
                             or _promised_not_acted(result.final_response))
                        and i < self.MAX_ITERATIONS - 1):
                    _empty_streak += 1
                    # A model that returns empties repeatedly will not recover by
                    # being asked again (observed live: GLM returned 5 consecutive
                    # empty responses while the old guard retried it forever).
                    # After 3, hand the task to the next fallback tier instead.
                    if _empty_streak >= 3:
                        _next_tier = _next_fallback_tier(connector.model_id)
                        if _next_tier:
                            logger.warning(
                                f"[agent] {_empty_streak} empty responses from "
                                f"{connector.model_id} — handing off to {_next_tier}"
                            )
                            connector        = registry.get(_next_tier)
                            _tried_models.add(_next_tier)
                            params           = get_params(connector.api_model, task_type)
                            _fallback_active = _use_compact_for(connector)
                            _empty_streak    = 0
                    logger.warning(
                        f"[agent] empty/stub response on build task at iteration {i+1} "
                        f"— forcing retry"
                    )
                    messages.append({"role": "assistant", "content": result.final_response})
                    messages.append({
                        "role": "user",
                        "content": (
                            "You have not created any files yet and your response was "
                            "essentially empty. You MUST act now: use create_file to write "
                            "the actual project files. Do not respond with a short "
                            "acknowledgement — produce real work."
                        ),
                    })
                    result.final_response = ""
                    continue
                _empty_streak = 0

                # ── Build verification gate ────────────────────────────────────
                # Any task that touched files (not just creative/planning ones —
                # a plain "fix this bug" request deserves verification too).
                # npm build for Node projects, pytest for Python ones; no-op
                # otherwise (see _run_build_check). Run-scoped: gates verify the
                # project THIS run touched, not whatever leftover dir lists first.
                _touched = list(dict.fromkeys(result.files_created + result.files_edited))
                if not _build_verified and _touched:
                    build_err = await self._run_build_check(_touched)
                    if build_err:
                        _build_verified = False
                        _build_fail_cycles += 1
                        _last_build_err = build_err
                        _ledger_build   = f"FAILING: {build_err[:120]}"
                        logger.warning(
                            f"[agent] build-verification gate FAILED at iteration {i+1} "
                            f"(fail cycle {_build_fail_cycles}): {build_err[:200]}"
                        )
                        # Hand over the failing file's actual current content along with
                        # the error — without this, weak fallback models spend a whole
                        # extra iteration just calling read_file to see what they broke
                        # before they can even start fixing it.
                        file_ctx = await self._failing_file_context(build_err)
                        strategy_block = ""
                        # cycle >= 2 means the PREVIOUS fix attempt didn't clear the
                        # build — same rationale as the verifier-battery comparison
                        # judge below: blind "fix these errors" retry already failed
                        # once, so it's worth comparing two independent strategies
                        # instead of repeating the same instruction with no new
                        # information attached.
                        references_block = ""
                        if _build_fail_cycles >= 2:
                            try:
                                from core.comparison_judge import propose_and_pick_fix_strategy
                                strategy = await propose_and_pick_fix_strategy(
                                    [build_err[:500]], context=task[:500]
                                )
                                if strategy:
                                    strategy_block = (
                                        f"\n\nSUGGESTED STRATEGY (compared across two "
                                        f"independent proposals, since the previous fix "
                                        f"attempt didn't clear this):\n{strategy}"
                                    )
                                    logger.info(
                                        f"[agent] comparison-judge strategy injected for "
                                        f"build fail cycle {_build_fail_cycles}"
                                    )
                            except Exception as exc:
                                logger.warning(f"[agent] comparison-judge skipped: {str(exc)[:60]}")
                            # Indirect tool use (user request, 2026-07-12): the
                            # harness googles the error message on the model's
                            # behalf — what a human does after a failed fix, but
                            # which models in the lean coding loop essentially
                            # never do themselves. Same >=2-cycle gate as the
                            # judge above (first failures don't need outside
                            # help), independently fail-soft.
                            try:
                                from tools.bug_references import fetch_bug_references
                                refs = await fetch_bug_references(build_err)
                                if refs:
                                    references_block = f"\n\n{refs}"
                            except Exception as exc:
                                logger.warning(f"[agent] bug-references skipped: {str(exc)[:60]}")
                        messages.append({"role": "assistant", "content": result.final_response})
                        messages.append({
                            "role": "user",
                            "content": (
                                f"BUILD FAILED — fix ALL errors before finishing:\n{build_err}\n"
                                f"{file_ctx}{strategy_block}{references_block}\n\n"
                                f"Fix the issue(s) directly with edit_file or create_file, "
                                f"then run 'npm run build' again to confirm. When inserting a "
                                f"new element as a sibling at the top of a JSX return block, "
                                f"rewrite the WHOLE return statement (don't partially replace "
                                f"just the opening tag — that orphans the matching closing tag "
                                f"and breaks the element tree)."
                            ),
                        })
                        result.final_response = ""
                        continue
                    else:
                        _build_verified = True
                        _ledger_build   = f"PASSING (gate-verified at iteration {i+1})"
                        logger.info(f"[agent] build-verification gate PASSED at iteration {i+1}")
                        # Durable lesson: only when this build genuinely failed at
                        # least once first — a first-try pass has nothing to teach
                        # a future session. Cross-session (ChromaDB, not the
                        # per-run ledger above), so the NEXT task — even in a new
                        # process — can retrieve this via memory.retrieve_context()
                        # before it re-derives the same fix from scratch.
                        if _build_fail_cycles > 0 and _last_build_err:
                            await self._store_build_lesson(_last_build_err, result.final_response)

                # ── Deterministic verification battery ─────────────────────────
                # Zero-token, pure-code checks for the defect classes weak models
                # reliably ship even with a passing build: unstyled classNames,
                # imports to missing files, placeholder stubs, hard-404 images.
                # Runs AFTER the build gate (build errors dominate) and BEFORE the
                # reflexion critic (deterministic findings are cheaper and never
                # hallucinated — spend the LLM critic only on what code can't check).
                # Skip the whole battery when the run produced no code/web
                # artifacts at all (a .txt/.md letter, a data file...) --
                # there is nothing the verifiers CHECK in such output, and
                # scoping to a directory would sweep in leftover projects.
                # Live incident (2026-07-12): a formal-letter task (one root
                # .txt) got graded against a stale aurora-site/ dir; 8
                # findings from that unrelated project hijacked the fix
                # cycles into building a website nobody asked for.
                _code_exts = (".py", ".js", ".jsx", ".ts", ".tsx", ".css", ".html", ".json", ".vue", ".svelte")
                _touched_code = [f for f in _touched if str(f).lower().endswith(_code_exts)]
                if _build_verified and not _verifiers_passed and _touched_code:
                    findings: list[str] = []
                    proj = await self._find_project_dir(_touched)
                    # Node project -> its dir; otherwise (e.g. Python backends,
                    # which have no package.json) fall back to the first dir this
                    # run touched, so Python quality checks still run.
                    #
                    # _verify_allowed_dirs / _verify_allowed_root_files: both None
                    # when vroot IS a single project's own directory (nothing to
                    # over-scope). When vroot falls back to the WHOLE workspace,
                    # both get populated so the checks below don't wander into
                    # anything this run didn't itself touch. Caught live
                    # (2026-07-12), in two rounds against the same repro:
                    # (1) "create reverse_string.py" (2 root files) fell into this
                    # fallback, vroot became the whole workspace, and the battery
                    # reported 14 findings from unrelated aurora-site/meridian/
                    # my-app leftover PROJECT DIRECTORIES -- fixed by
                    # _verify_allowed_dirs (14 -> 8 findings). (2) Re-running the
                    # SAME scenario live after that fix still showed 8 findings
                    # and a still-active problem: 2 of them came from a stale,
                    # unrelated root-level index.html left over from a past
                    # session (root-level LOOSE files were unconditionally
                    # allowed on the untested assumption that a file directly in
                    # root always belongs to the current task) -- the model
                    # "fixed" that stale finding by writing a 346-line page,
                    # which then triggered a real ~3-minute image-localization
                    # detour. _verify_allowed_root_files closes that second gap:
                    # a root-level file must be in THIS run's own touched set.
                    if proj is not None:
                        vroot = (self.executor.workspace / proj) if proj else self.executor.workspace
                        _verify_allowed_dirs = None
                        _verify_allowed_root_files = None
                    else:
                        tops = self._touched_top_dirs(_touched_code)
                        if tops and tops[0]:
                            vroot = self.executor.workspace / tops[0]
                            _verify_allowed_dirs = None
                            _verify_allowed_root_files = None
                        else:
                            vroot = self.executor.workspace
                            _verify_allowed_dirs = {t for t in tops if t}
                            _verify_allowed_root_files = {
                                f for f in _touched_code if "/" not in f.replace("\\", "/")
                            }
                    from core.verifiers import run_all as _run_verifiers
                    # Localize generative-image URLs BEFORE the checks: models
                    # hotlink image.pollinations.ai, which generates images live
                    # on first request — slow (30-60s per image) and flaky under
                    # load, so "some images don't load" for the user. Downloading
                    # them into public/images/ and rewriting the references makes
                    # the site self-contained. Exact-string URL replacement inside
                    # quotes can't break syntax, so no build re-verification needed.
                    try:
                        n = await self._localize_remote_images(
                            vroot, _verify_allowed_dirs, _verify_allowed_root_files
                        )
                        if n:
                            logger.info(f"[agent] localized {n} generated image(s) into public/images/")
                    except Exception as exc:
                        logger.warning(f"[agent] image localization skipped: {str(exc)[:60]}")
                    try:
                        findings = await _run_verifiers(vroot, _verify_allowed_dirs, _verify_allowed_root_files)
                    except Exception as exc:
                        logger.warning(f"[agent] verifier battery skipped: {str(exc)[:60]}")
                    _ledger_findings = findings
                    # Cap raised 2 -> 3: the Aurora exam run exhausted 2 cycles with
                    # 6 cosmetic findings still open. Cheap cycles (deterministic
                    # detection, no LLM cost) and MAX_ITERATIONS has headroom.
                    if findings and _verifier_cycles < 3:
                        _verifier_cycles += 1
                        logger.warning(
                            f"[agent] verifier battery: {len(findings)} finding(s) at "
                            f"iteration {i+1} — fix cycle {_verifier_cycles}/3"
                        )
                        findings_block = "\n".join(f"- {f}" for f in findings[:10])
                        preamble = "AUTOMATED CHECKS FOUND REAL ISSUES (deterministic verification, not opinions)"
                        # cycle >= 2 means a blind single-shot retry already failed to
                        # clear these findings — worth paying for a CePO-style compare:
                        # two independent models each propose a fix strategy, a third
                        # model picks the one more likely to actually work, and that
                        # gets handed to the acting model instead of just repeating
                        # "fix these" a second time with no new information.
                        if _verifier_cycles >= 2:
                            try:
                                from core.comparison_judge import propose_and_pick_fix_strategy
                                strategy = await propose_and_pick_fix_strategy(findings, context=task[:500])
                                if strategy:
                                    findings_block += f"\n\nSUGGESTED STRATEGY (compared across two independent proposals):\n{strategy}"
                                    preamble += " — a previous attempt did not fully resolve these"
                                    logger.info(f"[agent] comparison-judge strategy injected for fix cycle {_verifier_cycles}/3")
                            except Exception as exc:
                                logger.warning(f"[agent] comparison-judge skipped: {str(exc)[:60]}")
                        messages.append({"role": "assistant", "content": result.final_response})
                        messages.append({
                            "role": "user",
                            "content": (
                                f"{preamble}:\n{findings_block}"
                                "\n\nFix ALL of the above with edit_file/create_file, then finish."
                            ),
                        })
                        result.final_response = ""
                        continue
                    _verifiers_passed = True
                    if not findings:
                        _ledger_findings = []

                # ── Vision-loop QA (web projects, once per run) ────────────────
                # Text-only gates cannot see rendered output: the Aurora exam
                # shipped a text overlap and a sparse column that passed every
                # code-level check. Serve the built dist/, screenshot it, and
                # have a vision model critique the actual pixels.
                if (_build_verified and _verifiers_passed and not _vision_done
                        and _needs_planning(task) and _touched):
                    _vision_done = True
                    proj = await self._find_project_dir(_touched)
                    if proj is not None:
                        v_findings = await self._vision_qa(proj)
                        if v_findings and i < self.MAX_ITERATIONS - 2:
                            logger.warning(
                                f"[agent] vision QA: {len(v_findings)} visual defect(s) "
                                f"at iteration {i+1} — fix cycle"
                            )
                            _ledger_findings = v_findings
                            messages.append({"role": "assistant", "content": result.final_response})
                            messages.append({
                                "role": "user",
                                "content": (
                                    "VISUAL REVIEW of the rendered site found these defects "
                                    "(from screenshots of the actual page):\n"
                                    + "\n".join(f"- {f}" for f in v_findings[:8])
                                    + "\n\nFix them in the CSS/JSX, then finish."
                                ),
                            })
                            result.final_response = ""
                            continue

                # ── Reflexion quality gate ─────────────────────────────────────
                # For creative/build tasks only. Runs a fast critic pass to check
                # completeness. If the critic finds gaps, injects specific feedback
                # and continues the loop so the agent can fix it.
                # Hard cap: 2 reflexion cycles max (prevents infinite loops). This was
                # previously "< 1", which only ever allowed ONE cycle despite the comment
                # and log message both saying 2 — a real gap: a model that fails its
                # first retry attempt (not uncommon under cascading fallback) got no
                # second chance even though the cap was meant to allow one.
                if _needs_planning(task) and _reflexion_cycles < 2:
                    touched_files = list(dict.fromkeys(result.files_created + result.files_edited))
                    gaps = await self._reflexion_check(
                        task, result.final_response, _plan_spec, task_type, touched_files
                    )
                    if gaps:
                        _reflexion_cycles += 1
                        logger.info(
                            f"[agent] reflexion cycle {_reflexion_cycles}/2: "
                            f"continuing to fix gaps"
                        )
                        try:
                            from core.activity_log import activity_log
                            activity_log.log_tool(
                                name="reflexion_critic",
                                args_preview=f"cycle {_reflexion_cycles}",
                                result_preview=gaps[:100],
                                success=False,  # incomplete = not success
                            )
                        except Exception:
                            pass
                        # Inject the agent's finished response + the critic's feedback
                        messages.append({"role": "assistant", "content": result.final_response})
                        messages.append({
                            "role": "user",
                            "content": (
                                f"Quality gate failed — fix ALL of the following before finishing:\n"
                                f"{gaps}\n\n"
                                f"Do not stop until every item above is resolved. "
                                f"Use tools to check, create, or edit as needed."
                            ),
                        })
                        result.final_response = ""
                        continue  # re-enter the loop for another round

                break  # truly done (COMPLETE or reflexion cap reached)

            # Execute all tool calls in parallel
            for tc in tool_calls:
                await cb.emit_tool_call(tc["name"], tc.get("args", {}))

            # Repetition guard — if the model calls the same single tool with identical
            # args 3 times in a row it's stuck. Execute normally but inject a correction
            # message after so the next iteration breaks out of the loop.
            _loop_correction = False
            if len(tool_calls) == 1:
                curr_sig = f"{tool_calls[0]['name']}:{json.dumps(tool_calls[0].get('args', {}), sort_keys=True)}"
                if curr_sig == _last_tool_sig:
                    _repeat_count += 1
                else:
                    _last_tool_sig = curr_sig
                    _repeat_count  = 0
            else:
                _last_tool_sig = ""
                _repeat_count  = 0
            if _repeat_count >= 2:
                _loop_correction = True

            # No-write-progress guard: catches the "same tool forever, always
            # different args" pathology the identical-args guard can't see —
            # observed live as 94 consecutive design_asset calls with unique
            # descriptions and zero files ever written. Iterations that only
            # explore/generate (no create_file/edit_file) are counted; writes
            # reset the counter.
            _wrote_this_iter = any(tc["name"] in ("create_file", "edit_file") for tc in tool_calls)
            if _wrote_this_iter:
                _no_write_iters = 0
            else:
                _no_write_iters += 1
            if _no_write_iters == 4:
                messages_pending_nudge = (
                    "PROGRESS CHECK: you have gone 4 iterations without writing a single "
                    "file. Whatever you are gathering (assets, listings, file contents), "
                    "you have enough. If the file already exists, use edit_file on it "
                    "NOW -- do not just create_file for brand-new files and stop there."
                )
            elif _no_write_iters >= 6:
                # Same escalation as the identical-call loop: a stuck model is a
                # model problem — hand the task to the next tier while budget remains.
                _loop_correction = True
                _loop_warnings   = max(_loop_warnings, 1)  # skip straight to handoff
                messages_pending_nudge = None
            else:
                messages_pending_nudge = None

            _read_loop_verdict = _read_loop_guard.begin_iteration(i + 1, tool_calls)
            if _read_loop_verdict.action is not _RLGAction.PROCEED:
                # Precedence rule (see read_loop_guard module docstring): this
                # guard's verdict wins for the turn -- never inject two
                # contradictory corrective messages in one iteration.
                messages_pending_nudge = None
                _loop_correction = False

            _scaffold_dir_before = _scaffold_dir
            if _read_loop_verdict.short_circuits:
                _rlg_real_calls = [
                    tc for tc in tool_calls if tc["id"] not in _read_loop_verdict.short_circuits
                ]
                _rlg_stub_results = [
                    ToolResult(tool_name=tc["name"], call_id=tc["id"], success=True,
                               output=_read_loop_verdict.short_circuits[tc["id"]], duration_ms=0.0)
                    for tc in tool_calls if tc["id"] in _read_loop_verdict.short_circuits
                ]
                tool_results = _rlg_stub_results + (
                    await self.executor.execute_parallel(_rlg_real_calls) if _rlg_real_calls else []
                )
            else:
                tool_results = await self.executor.execute_parallel(tool_calls)

            # Live-caught bug (2026-07-16): an edit_file failure (old_str
            # didn't match -- almost always because the model guessed at a
            # file's content instead of reading it fresh right before
            # editing) became just another tool result with no urgency
            # attached. The model would abandon that specific edit for the
            # rest of the run rather than reading the file and retrying --
            # confirmed reproducible across 2 separate live runs, same file,
            # different fallback models both times. The no-write nudge
            # (below) doesn't cover this: the model IS writing other files,
            # just never retrying THIS one.
            _failed_edits: list[str] = []

            for tr in tool_results:
                await cb.emit_tool_result(tr)
                tc_args = next(
                    (tc.get("args", {}) for tc in tool_calls if tc["id"] == tr.call_id), {}
                )
                # Only record files/commands that actually succeeded — tracking a
                # create_file/edit_file call that failed (e.g. a blocked path, a
                # missing old_str) poisons the build-verification gate and feeds the
                # reflexion critic paths that were never actually written.
                if tr.success:
                    if tr.tool_name == "create_file":
                        result.files_created.append(tc_args.get("path", ""))
                        # A verified state is stale the moment anything changes —
                        # without this reset, a reflexion fix cycle could break the
                        # build AFTER the gate passed and the run would still finish
                        # claiming "verified" (latent bug: _build_verified was sticky).
                        _build_verified   = False
                        _verifiers_passed = False
                        if _read_loop_guard.on_mutation(i + 1, tc_args) == "pressure_made":
                            _pressure_made_paths.append(tc_args.get("path", ""))
                    elif tr.tool_name == "edit_file":
                        result.files_edited.append(tc_args.get("path", ""))
                        _build_verified   = False
                        _verifiers_passed = False
                        # Step 5 (read_loop_guard design): an edit landing within
                        # the pressure window of a nudge/checkpoint is never
                        # silently trusted -- it must clear the SAME verifier
                        # battery as everything else (already guaranteed by the
                        # _verifiers_passed=False reset above; this just makes
                        # the pressured edit visible for review instead of
                        # indistinguishable from a normal one).
                        if _read_loop_guard.on_mutation(i + 1, tc_args) == "pressure_made":
                            _pressure_made_paths.append(tc_args.get("path", ""))
                            logger.warning(
                                f"[agent] read_loop_guard: edit to {tc_args.get('path', '')} "
                                f"at iteration {i+1} landed under pressure (nudge/checkpoint) "
                                f"-- verifier battery must clear it before this run can finish"
                            )
                    elif tr.tool_name in ("delete_file", "move_file"):
                        _read_loop_guard.on_mutation(i + 1, tc_args)
                    elif tr.tool_name == "bash":
                        cmd = tc_args.get("command", "")
                        result.commands_run.append(cmd[:60])
                        if "npm run build" in cmd:
                            head = tr.output[:400]
                            if "✓ $" in head:
                                _ledger_build = f"passing (model-run at iteration {i+1})"
                            elif "✗" in head:
                                _ledger_build = f"FAILING (model-run at iteration {i+1})"
                        if not _scaffold_dir:
                            m = ToolExecutor.SCAFFOLD_NAME_RE.search(cmd)
                            if m:
                                _scaffold_dir = m.group(1)
                                if _scaffold_dir in (".", ".."):
                                    _scaffold_dir = "."  # scaffolded into the workspace root
                                _scaffold_is_vite = "vite" in cmd.lower()
                        _read_loop_guard.on_mutation(i + 1)
                    elif tr.tool_name in ("read_file", "list_dir"):
                        _read_loop_guard.record_read_result(tc_args, tr.output, i + 1)
                elif tr.tool_name == "edit_file":
                    _failed_edits.append(tc_args.get("path", ""))
                try:
                    from core.activity_log import activity_log
                    activity_log.log_tool(
                        name=tr.tool_name,
                        args_preview=str(tc_args)[:60],
                        result_preview=tr.output[:100],
                        duration_ms=tr.duration_ms,
                        success=tr.success,
                    )
                except Exception:
                    pass

            # Append to message history
            messages.append({
                "role": "assistant", "content": None,
                "tool_calls": [
                    {"id": tc["id"], "type": "function",
                     "function": {"name": tc["name"], "arguments": json.dumps(tc.get("args", {}))}}
                    for tc in tool_calls
                ],
            })
            for tr in tool_results:
                messages.append({"role": "tool", "tool_call_id": tr.call_id, "content": tr.output})
            if _failed_edits:
                paths = ", ".join(dict.fromkeys(p for p in _failed_edits if p))
                messages.append({
                    "role": "user",
                    "content": (
                        f"Your edit_file call on {paths} just failed because old_str "
                        f"didn't match the file's real current content. Do NOT move on "
                        f"or abandon this edit -- call read_file on {paths} right now "
                        f"to see its exact current content, then retry edit_file with "
                        f"an old_str copied verbatim from what you just read."
                    ),
                })
            if _scaffold_dir and _scaffold_dir != _scaffold_dir_before:
                # Fire once, immediately after the scaffold succeeds — a mechanical
                # detection beats a static system-prompt mention, which has been
                # observed to get ignored 3/3 times by weak fallback models under
                # cascading-failover stress. For vite scaffolds we go further: the
                # boilerplate the models most reliably corrupt (vite.config.js was
                # rewritten or corrupted in essentially every live run) is OVERWRITTEN
                # with known-good golden files by the harness itself, so the model
                # only ever fills in content — the part it's actually good at.
                prefix = "" if _scaffold_dir == "." else f"{_scaffold_dir}/"
                where  = "the workspace root" if _scaffold_dir == "." else f"'{_scaffold_dir}/'"
                if _scaffold_is_vite:
                    written = await self._apply_golden_scaffold(prefix)
                    result.files_created.extend(written)
                    logger.info(
                        f"[agent] scaffold detected in {where} — golden scaffold applied "
                        f"({len(written)} files)"
                    )
                    messages.append({
                        "role": "user",
                        "content": (
                            f"A GOLDEN SCAFFOLD was just applied to {where}: "
                            f"{prefix}vite.config.js, {prefix}index.html, {prefix}src/main.jsx and "
                            f"{prefix}src/styles.css are already correct and verified — do NOT "
                            f"rewrite or edit them. Your job now:\n"
                            f"1. Create one component per section in {prefix}src/components/ "
                            f"(Header.jsx, Hero.jsx, ...)\n"
                            f"2. Rewrite {prefix}src/App.jsx to import and render those components\n"
                            f"3. Put ALL styles in {prefix}src/styles.css (it is already imported "
                            f"in main.jsx — do not create other CSS files)\n"
                            f"4. Verify with: cd {_scaffold_dir} && npm run build"
                            if _scaffold_dir != "." else
                            f"A GOLDEN SCAFFOLD was just applied at the workspace root: "
                            f"vite.config.js, index.html, src/main.jsx and src/styles.css are "
                            f"already correct — do NOT rewrite them. Create one component per "
                            f"section in src/components/, rewrite src/App.jsx to compose them, "
                            f"put ALL styles in src/styles.css, verify with: npm run build"
                        ),
                    })
                else:
                    logger.info(f"[agent] scaffold detected: '{_scaffold_dir}/' — injecting path reminder")
                    messages.append({
                        "role": "user",
                        "content": (
                            f"You just scaffolded a new project into '{_scaffold_dir}/'. That is now "
                            f"the project root. From this point on, EVERY file path (create_file, "
                            f"edit_file, read_file) MUST start with '{_scaffold_dir}/' — e.g. "
                            f"'{_scaffold_dir}/src/App.jsx', NOT 'src/App.jsx'. Build commands must "
                            f"also run inside it: 'cd {_scaffold_dir} && npm run build'. Do NOT create "
                            f"any files at the workspace root from now on."
                        ),
                    })
            if messages_pending_nudge:
                logger.warning(f"[agent] no-write nudge at iteration {i+1} "
                               f"({_no_write_iters} iterations without a file write)")
                messages.append({"role": "user", "content": messages_pending_nudge})
            if _read_loop_verdict.inject_message:
                logger.warning(
                    f"[agent] read_loop_guard {_read_loop_verdict.action.name} at "
                    f"iteration {i+1}: {_read_loop_verdict.reason}"
                )
                messages.append({"role": "user", "content": _read_loop_verdict.inject_message})
            if _read_loop_verdict.action is _RLGAction.ESCALATE_MODEL:
                # Same escalate-or-name-the-stall pattern as the legacy
                # _loop_correction handoff below -- a model stuck reading
                # without ever attempting an edit is a MODEL problem, so try
                # the next fallback tier before giving up. One iteration of a
                # stronger model is cheaper than several more of a weak one
                # going in circles.
                _next_tier = _next_fallback_tier(connector.model_id)
                if not _next_tier:
                    try:
                        from core.model_escalation import agentic_candidates
                        _pool = await agentic_candidates(exclude=_tried_models)
                        _next_tier = _pool[0] if _pool else None
                    except Exception as exc_pool:
                        logger.warning(f"[agent] escalation pool lookup failed: {str(exc_pool)[:80]}")
                        _next_tier = None
                if _next_tier and i < self.MAX_ITERATIONS - 2:
                    logger.warning(
                        f"[agent] read-loop escalation at iteration {i+1}: handing off "
                        f"{connector.model_id} -> {_next_tier}"
                    )
                    connector        = registry.get(_next_tier)
                    _tried_models.add(_next_tier)
                    params           = get_params(connector.api_model, task_type)
                    _fallback_active = _use_compact_for(connector)
                    _loop_warnings   = 0
                    _repeat_count    = 0
                    _last_tool_sig   = ""
                    _no_write_iters  = 0
                    messages.append({
                        "role": "user",
                        "content": (
                            "A previous model spent too long reading without making any "
                            "change. You are taking over. Check the PROJECT LEDGER above "
                            "for current state, then make concrete progress with "
                            "create_file/edit_file — do not re-explore."
                        ),
                    })
                    continue
                logger.warning(
                    f"[agent] named stall at iteration {i+1}: read-only loop, "
                    f"no fallback tiers left — ending run"
                )
                if not result.final_response:
                    result.final_response = (
                        "Stopped early: stalled:read_loop — the model read files "
                        "repeatedly without ever attempting an edit, and no fallback "
                        "model tier remained. The work completed before that point "
                        "(if any) is in the workspace."
                    )
                break
            if _loop_correction:
                _loop_warnings += 1
                # Escalate: a soft in-context warning was tried once already and the
                # model repeated the exact same call again anyway. A stuck model is a
                # MODEL problem, not a task problem — so before giving up, hand the
                # task to the next fallback tier (observed live: GPT-OSS-120B stuck
                # in a read-loop while a perfectly healthy Llama-3.3 tier sat idle
                # and the old code killed the whole run instead of using it).
                if _loop_warnings >= 2:
                    _next_tier = _next_fallback_tier(connector.model_id)
                    if not _next_tier:
                        # Fixed chain exhausted -- reach into the broader pool
                        # (core/model_escalation.py) before hard-stopping. A
                        # stuck model is a MODEL problem, not a task problem,
                        # so a healthy model outside the 4-tier chain is still
                        # worth trying over ending the run.
                        try:
                            from core.model_escalation import agentic_candidates
                            _pool = await agentic_candidates(exclude=_tried_models)
                            _next_tier = _pool[0] if _pool else None
                        except Exception as exc_pool:
                            logger.warning(f"[agent] escalation pool lookup failed: {str(exc_pool)[:80]}")
                            _next_tier = None
                    if _next_tier and i < self.MAX_ITERATIONS - 2:
                        logger.warning(
                            f"[agent] loop-stuck at iteration {i+1}: handing off "
                            f"{connector.model_id} -> {_next_tier}"
                        )
                        connector        = registry.get(_next_tier)
                        _tried_models.add(_next_tier)
                        params           = get_params(connector.api_model, task_type)
                        _fallback_active = _use_compact_for(connector)
                        _loop_warnings   = 0
                        _repeat_count    = 0
                        _last_tool_sig   = ""
                        # Without this reset, a fresh model's very first (and
                        # perfectly reasonable) orientation read -- which the
                        # handoff message below explicitly tells it to do --
                        # instantly re-trips the >=6 no-write threshold left
                        # over from the PREVIOUS model's failure, cascading
                        # through the rest of the fallback chain in 1-2
                        # iterations each with no real chance to fix anything
                        # (observed live 2026-07-16: 3 handoffs in 2 iterations,
                        # hard-stopped with tiers exhausted at iteration 21/22).
                        _no_write_iters  = 0
                        messages.append({
                            "role": "user",
                            "content": (
                                "A previous model got stuck repeating the same action. "
                                "You are taking over. Check the PROJECT LEDGER above for "
                                "current state, then make concrete progress with "
                                "create_file/edit_file — do not re-explore."
                            ),
                        })
                        continue
                    logger.warning(
                        f"[agent] hard loop-stop at iteration {i+1}: model repeated "
                        f"identical tool calls even after a warning — no tiers left, ending run"
                    )
                    if not result.final_response:
                        result.final_response = (
                            "Stopped early: the model kept repeating the same action "
                            "without making further progress. The work completed before "
                            "that point (if any) is in the workspace."
                        )
                    break
                messages.append({
                    "role": "user",
                    "content": (
                        "LOOP WARNING: You have called the same tool with identical arguments "
                        "3 times in a row. This almost always means the task is ALREADY DONE. "
                        "Do NOT repeat this call. If the file(s) already exist with correct "
                        "content, STOP calling tools now and respond with a short text summary "
                        "of what you completed."
                    ),
                })
                _repeat_count  = 0
                _last_tool_sig = ""
        else:
            result.final_response = f"Reached {self.MAX_ITERATIONS} iterations. Check workspace: {self.executor.workspace}"

        result.total_ms = (time.perf_counter() - t0) * 1000
        await cb.emit_final(result)
        try:
            from core.activity_log import activity_log
            activity_log.log_task_done(
                f"iter={result.iterations} | "
                f"files_created={len(result.files_created)} | "
                f"files_edited={len(result.files_edited)} | "
                f"commands={len(result.commands_run)} | "
                f"{result.total_ms/1000:.1f}s"
            )
        except Exception:
            pass

        # Adaptive routing memory: learn from this run's OUTCOME, but only from
        # UNAMBIGUOUS signals — build/tests passing is a clear success; spinning
        # to the iteration cap is a clear failure. Everything else (a task with
        # nothing to build, an interrupted run) is ambiguous and deliberately
        # NOT recorded — learning from noise is exactly what makes naive
        # self-improvement dangerous. Recorded against the STARTING model, since
        # "was starting with model X for this kind of task a good call" is
        # precisely the routing decision being learned.
        try:
            hit_cap = result.iterations >= self.MAX_ITERATIONS
            outcome: bool | None = True if _build_verified else (False if hit_cap else None)
            if outcome is not None:
                from core.routing_memory import get_memory
                cats = get_memory().record(task, _starting_model, outcome)
                if cats:
                    logger.info(f"[agent] routing memory: recorded {_starting_model} "
                                f"{'✓' if outcome else '✗'} for {'/'.join(cats)}")
        except Exception as exc:
            logger.warning(f"[agent] routing memory record skipped: {str(exc)[:60]}")

        # Show the document itself in the terminal, not just a "file
        # created" line. Found live (2026-07-12): a formal-letter task
        # correctly wrote a .txt, but the user still had to open the file
        # themselves to read it -- final_response is the model's own closing
        # remark ("I've saved the letter"), not the document body. Scoped to
        # plain-text deliverables only (same split used above to skip the
        # verifier battery on non-code output) -- a real web/code project
        # has too much to dump into a terminal and the summary table already
        # lists every file.
        try:
            _touched_all = list(dict.fromkeys(result.files_created + result.files_edited))
            _text_exts = (".txt", ".md")
            if _touched_all and all(str(f).lower().endswith(_text_exts) for f in _touched_all):
                previews = []
                for f in _touched_all[:3]:
                    content = await self.executor.read_file(f)
                    if not content.startswith("ERROR"):
                        previews.append((f, content))
                if previews:
                    result.document_preview = "\n\n".join(
                        f"--- {f} ---\n{c[:4000]}" for f, c in previews
                    )
        except Exception as exc:
            logger.warning(f"[agent] document preview skipped: {str(exc)[:60]}")

        if _pressure_made_paths:
            logger.warning(
                f"[agent] read_loop_guard: {len(_pressure_made_paths)} edit(s) this run "
                f"landed under nudge/checkpoint pressure: {', '.join(dict.fromkeys(_pressure_made_paths))} "
                f"-- verify these were actually checked by the build/verifier gate, not just written"
            )

        return result

    async def _reflexion_check(
        self,
        task:     str,
        response: str,
        spec:     str,
        task_type: str,
        files:    list[str] | None = None,
    ) -> str | None:
        """
        Runs a single "brain" review pass after the agent stops — the critic
        reads the ACTUAL files the agent touched (not just its text summary),
        so it can catch real bugs (mismatched CSS classes, broken imports,
        duplicate renders, dead references) that a summary would hide.
        Returns None if the task is complete; returns the specific gap/rating
        report if not. Bounded cost: one fast model call, ~500 output tokens.
        Cap at 2 calls per run (enforced by caller) so this can never spin.
        """
        from models.registry import generate_resilient
        spec_block = f"\n\nIMPLEMENTATION SPEC (what was planned):\n{spec[:2000]}" if spec else ""

        # Pull real file contents so the critic reviews code, not a description of it.
        files_block = ""
        if files:
            chunks = []
            budget = 6000  # total chars across all files, keeps the review call cheap
            for path in files[:10]:
                if budget <= 0:
                    break
                try:
                    content = await self.executor.read_file(path)
                except Exception:
                    continue
                snippet = content[:1500]
                budget -= len(snippet)
                chunks.append(f"--- {path} ---\n{snippet}")
            if chunks:
                files_block = "\n\nFILES PRODUCED (actual current content):\n" + "\n\n".join(chunks)

        prompt = (
            f"ORIGINAL TASK:\n{task}{spec_block}\n\n"
            f"AGENT'S FINAL RESPONSE (summary of what it claims it did):\n{response[:1500]}"
            f"{files_block}"
        )
        try:
            # generate_resilient cross-model-failovers (same team, then a cross-team
            # safety net) instead of a single hardcoded critic model. A plain
            # registry.get("gemini_flash").generate() here is a single point of failure —
            # exactly when the system is already under rate-limit stress (Gemini
            # exhausted) is exactly when this quality gate is needed most, and a
            # hardcoded model means the gate silently no-ops at the worst time.
            verdict = await generate_resilient(
                "gemini_flash",
                prompt=prompt,
                system=_REFLEXION_CRITIC_SYS,
                max_tokens=500,
                temperature=0.1,
                task_type=task_type,
            )
            if verdict.strip().upper().startswith("COMPLETE"):
                logger.info("[agent/reflexion] quality gate: COMPLETE")
                return None
            logger.info(f"[agent/reflexion] quality gate: INCOMPLETE — {verdict[:160]}")
            return verdict.strip()
        except Exception as exc:
            logger.warning(f"[agent/reflexion] critic skipped: {str(exc)[:60]}")
            return None  # never block the run on a critic failure

    def _build_messages(self, task, system, context, history=None):
        if context:
            task = f"{context}\n\n{task}"
        messages = [{"role": "system", "content": system or _AGENT_SYSTEM}]
        if history:
            # Prior conversation is background/continuity context, NOT a
            # continuation of an in-progress task. Confirmed live
            # (2026-07-19): splicing history in verbatim gave the model no
            # signal to distinguish "old topic from days ago" from "the
            # current live task" -- a fresh, unrelated request ("generate
            # an image of a boy playing football") got answered with a
            # palindrome-checker program pulled straight from a stale
            # history-compaction summary (cli.py's _maybe_summarize_history,
            # which never expires once created), and a later request
            # drifted into resuming an old half-built "openstack" project
            # the user hadn't mentioned this turn. Same "team's actual job,
            # not the whole request" disambiguation principle already used
            # in teams/leadership.py's TEAM_MANDATES, applied to history.
            messages.append({
                "role": "system",
                "content": (
                    "The following is PAST conversation history, included for "
                    "background/continuity only. It may describe different, "
                    "already-finished, or abandoned topics. Do NOT treat it as "
                    "part of the CURRENT task, and do NOT resume or continue "
                    "any old unfinished work from it unless the user's current "
                    "message below explicitly asks you to."
                ),
            })
            messages.extend(history)
            messages.append({
                "role": "system",
                "content": "END of past history. Everything above this line is background only -- solve the CURRENT task below.",
            })
        messages.append({"role": "user", "content": task})
        return messages

    @staticmethod
    async def _ensure_memory_ready(memory) -> None:
        """Lazily init the tools.memory singleton, bounded so a slow first
        model/collection load can't hang the caller. Shared by both call
        sites (lesson retrieval above, lesson storage below) so the guard
        is written once instead of drifting between two copies.
        """
        if not memory._ready:
            await asyncio.wait_for(memory.init(), timeout=10.0)

    async def _store_build_lesson(self, error: str, fix_explanation: str) -> None:
        """Persist a solved build failure to VectorMemory (tools/memory.py) so a
        future task — even in a brand-new process — can retrieve it via
        memory.retrieve_context() before re-deriving the same fix. Best-effort:
        this must never affect the run it's called from.
        """
        try:
            from tools.memory import memory
            await self._ensure_memory_ready(memory)
            await memory.store_error_fix(
                error=error[:500], fix=fix_explanation[:800], team="code",
                workspace=str(self.executor.workspace.resolve()),
            )
        except Exception as exc:
            logger.warning(f"[agent] build-lesson store skipped: {str(exc)[:60]}")

    async def _run_build_check(self, touched: list[str] | None = None) -> str | None:
        """
        Verification gate. Node projects: 'npm run build'. Python projects with
        tests: 'python -m pytest' (previously test-passing for backend tasks
        relied entirely on the model's diligence — now it's mechanically
        enforced, same as builds). Returns error text on failure, None on
        success or when there's nothing verifiable.
        ToolExecutor.bash() prefixes output with '✓' or '✗ (exit N)' from the
        actual process exit code — that prefix is the ground truth. Keyword-
        sniffing output text is unreliable (Vite's failure message contains
        neither "failed" nor "exit code").
        """
        try:
            build_dir = await self._find_project_dir(touched)
            if build_dir is not None:
                cmd = f"cd {build_dir} && npm run build 2>&1" if build_dir else "npm run build 2>&1"
                out = await self.executor.bash(cmd, timeout=90)
                if out.startswith("✗"):
                    prefix = f"[in {build_dir}/] " if build_dir else ""
                    return prefix + out[:1500]
                return None

            # No Node project — check for a Python project with tests among the
            # dirs this run touched.
            for top in self._touched_top_dirs(touched):
                prefix = f"{top}/" if top else ""
                root = self.executor.workspace / top if top else self.executor.workspace
                has_tests = any(root.glob("tests/test_*.py")) or any(root.glob("test_*.py"))
                has_py    = any(root.glob("*.py")) or any(root.glob("**/*.py"))
                if has_py and has_tests:
                    target = top if top else "."
                    out = await self.executor.bash(f"python -m pytest {target} -q 2>&1", timeout=90)
                    if out.startswith("✗"):
                        return f"[pytest in {target}] " + out[:1500]
                    return None
            return None  # nothing verifiable
        except Exception as exc:
            logger.warning(f"[agent] build check skipped: {str(exc)[:60]}")
            return None  # never block the run on a build-check failure

    async def _apply_golden_scaffold(self, prefix: str) -> list[str]:
        """
        Overwrite a fresh vite scaffold's boilerplate with known-good golden files
        and remove the demo-template cruft models keep getting confused by (the
        stock App.css/index.css were left untouched-but-unused in multiple live
        runs while the model wrote real styles into files that were never imported).
        Returns the list of paths written, for result.files_created bookkeeping.
        """
        written: list[str] = []
        golden = {
            f"{prefix}vite.config.js":  _GOLDEN_VITE_CONFIG,
            f"{prefix}index.html":      _GOLDEN_INDEX_HTML,
            f"{prefix}src/main.jsx":    _GOLDEN_MAIN_JSX,
            f"{prefix}src/styles.css":  _GOLDEN_STYLES_CSS,
            f"{prefix}src/App.jsx":     _GOLDEN_APP_JSX,
        }
        for path, content in golden.items():
            try:
                await self.executor.create_file(path, content)
                written.append(path)
            except Exception as exc:
                logger.warning(f"[agent] golden scaffold write failed for {path}: {str(exc)[:60]}")
        # Demo cruft — delete_file returns an "ERROR: Not found" string (doesn't
        # raise) when the template didn't include the file, which is fine.
        for path in (f"{prefix}src/App.css", f"{prefix}src/index.css"):
            try:
                await self.executor.delete_file(path)
            except Exception:
                pass
        return written

    _GEN_IMG_RE = re.compile(r"https://image\.pollinations\.ai/[^\s\"')]+")

    async def _localize_remote_images(
        self,
        root: Path,
        allowed_dirs: set[str] | None = None,
        allowed_root_files: set[str] | None = None,
    ) -> int:
        """
        Download every image.pollinations.ai URL referenced in the project into
        public/images/ and rewrite the references (JSX src, CSS url()) to the
        local path. Generation-on-request hosting means each remote reference is
        a 30-60s page-load stall or an intermittent broken image for the user;
        one download at build time makes it a permanent local asset instead.
        A URL whose download fails is left untouched (better remote than broken).

        Downloads go through curl, not aiohttp: pollinations' CDN serves aiohttp
        an empty 200 response (TLS-fingerprint bot filtering — verified directly:
        same URL, curl gets the 73KB image, aiohttp gets 0 bytes with any
        User-Agent). curl ships with Windows 10+ and macOS, and is exempt.

        allowed_dirs / allowed_root_files mirror core/verifiers.py's params of
        the same names -- when `root` is the whole multi-project workspace (a
        task with no project directory of its own), this must NOT rglob into
        every leftover project OR every stale root-level file sitting there
        and rewrite THEIR content. Both None means no restriction (root is
        already a single project's own dir).
        """
        import hashlib
        import shutil as _shutil

        curl = _shutil.which("curl")

        def _in_scope(p: Path) -> bool:
            if allowed_dirs is None:
                return True
            rel_parts = p.relative_to(root).parts
            if len(rel_parts) > 1:
                return rel_parts[0] in allowed_dirs
            return allowed_root_files is None or p.name in allowed_root_files

        src_files = [
            p for p in root.rglob("*")
            if p.suffix.lower() in (".jsx", ".tsx", ".js", ".html", ".css")
            and not any(part in ("node_modules", "dist", "build", ".git") for part in p.parts)
            and _in_scope(p)
        ]
        url_locations: dict[str, list[Path]] = {}
        for f in src_files:
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for m in self._GEN_IMG_RE.finditer(text):
                url_locations.setdefault(m.group(0), []).append(f)
        if not url_locations:
            return 0

        # A root-absolute "/images/..." reference is only correct when a
        # bundler dev/build server maps public/ to the served root (Vite,
        # CRA, Next.js — the convention this function was originally written
        # for, per the JSX-src docstring above). Found live (2026-07-13): a
        # bare "create an image of X" request produces ONE standalone .html
        # file with no framework, no package.json, no dev server at all —
        # opened directly (file:// or a naive static server rooted wherever
        # the .html happens to sit), "/images/gen-<hash>.jpg" resolves to
        # nothing, even though the file downloaded correctly to
        # public/images/. The image generation and download were never the
        # problem; the path written into the HTML was wrong for how it gets
        # opened. A real .jsx/.tsx file or a package.json in the project is
        # an unambiguous bundler signal; absent both, this isn't a bundler
        # project and every reference needs a path relative to the
        # referencing file itself, which resolves correctly however the
        # file is opened.
        is_bundler_project = (
            any(p.suffix.lower() in (".jsx", ".tsx") for p in src_files)
            or (root / "package.json").exists()
        )

        img_dir = root / "public" / "images"
        img_dir.mkdir(parents=True, exist_ok=True)
        replaced = 0
        for url, files in url_locations.items():
            name = f"gen-{hashlib.sha1(url.encode()).hexdigest()[:10]}.jpg"
            target = img_dir / name
            if not target.exists():
                if not curl:
                    continue  # no downloader available — leave URLs remote
                try:
                    # Generous timeout — pollinations GENERATES on first request.
                    proc = await asyncio.create_subprocess_exec(
                        curl, "-sL", "--max-time", "90", "-o", str(target), url,
                        stdout=asyncio.subprocess.DEVNULL,
                        stderr=asyncio.subprocess.DEVNULL,
                    )
                    await asyncio.wait_for(proc.communicate(), timeout=100)
                    if proc.returncode != 0 or not target.exists() or target.stat().st_size < 1024:
                        target.unlink(missing_ok=True)  # error page / stub, not an image
                        continue
                except Exception:
                    target.unlink(missing_ok=True)
                    continue  # leave this URL remote
            for f in set(files):
                try:
                    if is_bundler_project:
                        local_rel = f"/images/{name}"
                    else:
                        import os as _os
                        local_rel = _os.path.relpath(target, start=f.parent).replace("\\", "/")
                    text = f.read_text(encoding="utf-8", errors="replace")
                    if url in text:
                        f.write_text(text.replace(url, local_rel), encoding="utf-8")
                except OSError:
                    continue
            replaced += 1
        return replaced

    async def _vision_qa(self, proj: str) -> list[str]:
        """
        Render the BUILT site and have a vision model critique the actual
        pixels. Serves dist/ over a local http.server (vite's absolute /assets/
        paths break under file://), screenshots three scroll positions with
        Playwright, and asks a vision model for concrete visual defects only.

        Fails soft at every step: no playwright, no dist, no vision quota —
        the gate silently skips. It must never block a run.
        """
        import base64
        import socket
        import subprocess
        import sys as _sys
        import tempfile as _tempfile

        try:
            from playwright.async_api import async_playwright
        except ImportError:
            logger.info("[agent/vision-qa] playwright not installed — skipping")
            return []

        root = (self.executor.workspace / proj) if proj else self.executor.workspace
        dist = root / "dist"
        if not (dist / "index.html").exists():
            return []

        # Free port for the throwaway static server
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]

        server = subprocess.Popen(
            [_sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1",
             "--directory", str(dist)],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        shots: list[str] = []
        try:
            await asyncio.sleep(1.0)
            # NOTE: plain paths inside a TemporaryDirectory, NOT mkstemp —
            # mkstemp returns an OPEN file descriptor, and on Windows that open
            # handle blocks playwright from writing the screenshot (WinError 32).
            with _tempfile.TemporaryDirectory(prefix="vibe_shots_") as shot_dir:
                async with async_playwright() as p:
                    browser = await p.chromium.launch(headless=True)
                    page = await browser.new_page(viewport={"width": 1440, "height": 900})
                    await page.goto(f"http://127.0.0.1:{port}/", timeout=20_000)
                    await page.wait_for_timeout(2500)  # let animations settle
                    for idx, pos in enumerate((0.0, 0.45, 1.0)):
                        await page.evaluate(f"window.scrollTo(0, document.body.scrollHeight * {pos})")
                        await page.wait_for_timeout(1000)
                        tmp = Path(shot_dir) / f"shot_{idx}.png"
                        await page.screenshot(path=str(tmp))
                        shots.append(base64.b64encode(tmp.read_bytes()).decode())
                    await browser.close()
        except Exception as exc:
            logger.warning(f"[agent/vision-qa] screenshot failed: {str(exc)[:80]}")
            return []
        finally:
            server.terminate()

        try:
            from models.registry import generate_resilient
            verdict = await generate_resilient(
                "gemini_flash_vision",
                prompt=(
                    "These are three screenshots (top, middle, bottom) of one webpage. "
                    "List ONLY concrete visual DEFECTS you can actually see: overlapping "
                    "or clipped text, broken/missing images, large empty regions where "
                    "content obviously should be, unreadable low-contrast text, elements "
                    "escaping their container. One line each, most severe first, max 6. "
                    "Name the section (hero/pricing/footer...). Do NOT suggest stylistic "
                    "improvements — defects only. If there are none, reply exactly: CLEAN"
                ),
                images=shots,
                max_tokens=700,
                temperature=0.1,
            )
        except Exception as exc:
            logger.warning(f"[agent/vision-qa] vision model unavailable: {str(exc)[:80]}")
            return []

        verdict = (verdict or "").strip()
        if not verdict or verdict.upper().startswith("CLEAN"):
            logger.info("[agent/vision-qa] rendered page is visually clean")
            return []
        findings = [f"[visual] {ln.lstrip('-• ').strip()}"
                    for ln in verdict.splitlines() if ln.strip() and len(ln.strip()) > 12]
        return findings[:6]

    @staticmethod
    def _touched_top_dirs(touched: list[str] | None) -> list[str]:
        """Top-level directories that THIS RUN's file operations touched, in
        first-touched order. '' means files were written at the workspace root."""
        seen: list[str] = []
        for p in touched or []:
            norm = p.replace("\\", "/").lstrip("./")
            top = norm.split("/", 1)[0] if "/" in norm else ""
            if top not in seen:
                seen.append(top)
        return seen

    async def _find_project_dir(self, touched: list[str] | None = None) -> str | None:
        """
        Locate the project to verify: "" for workspace root, a subdirectory
        name, or None if no verifiable project exists.

        Directories touched by THE CURRENT RUN are checked first — during live
        exam testing, a backend run in shortlink-api/ got graded against a
        LEFTOVER frontend project (aurora-analytics/) simply because that dir
        came first in the listing; the run then wasted iterations fixing the
        other project's findings. Run-scoped resolution closes that.

        read_file returns an "ERROR: ..." string on a missing file (it doesn't
        raise), hence the string checks.
        """
        async def _is_project(prefix: str) -> bool:
            pkg = await self.executor.read_file(f"{prefix}package.json" if prefix else "package.json")
            return not pkg.startswith("ERROR") and '"build"' in pkg

        # 1) Directories this run actually touched, in touch order
        for top in self._touched_top_dirs(touched):
            prefix = f"{top}/" if top else ""
            if await _is_project(prefix):
                return top
        # 2) Workspace root
        if await _is_project(""):
            return ""
        # 3) Any top-level subdirectory — LEGACY-CALLER-ONLY last resort.
        # When the run DID touch files but none of them belong to a project
        # (e.g. a plain .txt deliverable like a formal letter), there is
        # nothing of THIS run's to verify — scanning unrelated leftover dirs
        # here is exactly the cross-contamination this method exists to
        # prevent. Bitten live (2026-07-12): "write an application to the
        # block coordinator" correctly produced a root-level .txt in ~10s,
        # then this fallback picked the leftover aurora-site/ (July 5) as
        # "the project", the build gate + verifier battery graded THAT, and
        # the fix cycles marched the model into rebuilding a website nobody
        # asked for. The scan now runs only for legacy callers that pass no
        # touch information at all.
        # `is not None`, NOT truthiness. An EMPTY list means the caller ran and
        # touched nothing -- there is nothing of THIS run's to verify, so the
        # scan must not run. Truthiness conflated [] with None and let a
        # zero-file run fall through to the leftover scan: caught live
        # (2026-07-23) when a fresh "make one nimbus-landing.html" run created
        # 0 files, scanned, adopted the leftover aurora-site/ as "the project",
        # and ran `cd aurora-site && npm install && npm run build` on work
        # nobody asked about. Only a caller that passes NO touch info at all
        # (None -- legacy) still gets the last-resort scan.
        if touched is not None:
            return None
        listing = await self.executor.list_dir(".")
        for line in listing.splitlines():
            if not line.startswith("📁 "):
                continue
            candidate = line.removeprefix("📁 ").strip()
            if "/" in candidate or "\\" in candidate:
                continue  # only top-level directories
            if await _is_project(f"{candidate}/"):
                return candidate
        return None

    async def _failing_file_context(self, build_err: str) -> str:
        """
        Extract the failing file's path from Vite/esbuild/Babel error text and
        return its current content. Saves the model an extra read_file round-trip
        before it can act on the error — without this, a weak fallback model
        routinely spends a whole iteration just re-discovering what it broke.
        """
        # [^:\n]+? (not [^\s:]+) — Windows paths routinely contain spaces (this very
        # repo lives under "VS Codes"), so excluding whitespace from the path body
        # would silently fail to match on exactly the kind of path this repo has.
        m = re.search(r"([A-Za-z]:[\\/][^:\n]+?\.(?:jsx?|tsx?|css|html))(?::\d+)?", build_err)
        if not m:
            return ""
        try:
            rel = Path(m.group(1)).resolve().relative_to(self.executor.workspace)
        except Exception:
            return ""
        content = await self.executor.read_file(str(rel))
        if content.startswith("ERROR"):
            return ""
        return f"\n\nCURRENT CONTENT of {rel} (the file the error points to):\n{content[:2000]}"

    async def run_parallel_files(self, files: list[dict]) -> list[str]:
        tasks = [self.executor.create_file(f["path"], f["content"]) for f in files]
        return await asyncio.gather(*tasks)

def _extract_thinking(text: str) -> str:
    m = re.search(r"<think>(.*?)</think>", text, re.DOTALL)
    return m.group(1).strip() if m else ""

def _strip_thinking(text: str) -> str:
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL).strip()

_REFLEXION_CRITIC_SYS = """\
You are the senior reviewer ("brain") for a coding agent. The agent below you writes code;
your only job is to read the ACTUAL file contents it produced and catch what it missed —
real bugs, not vague design opinions. You are given real file contents, not a description.

FIND ACTUAL ERRORS (read the code, don't assume it's correct):
1. Broken/duplicate imports (typo'd package names, importing something never installed)
2. A component/element rendered twice (e.g. same overlay mounted in two different files)
3. References to files or assets that do not exist in the FILES PRODUCED list (image paths,
   missing components) — these silently break at runtime even though the build may pass
4. CSS class names used in JSX with NO matching rule in the stylesheet shown
5. Dead placeholders: "lorem ipsum", "coming soon", [TODO], "placeholder", "SECTION_HERE"
6. Every nav link has a matching built section with real content
7. Every feature/section mentioned in the task or spec is actually implemented
8. Requested libraries are USED (not just imported) — animations wired, scroll smooth
9. No section is empty or has fewer than 2 real paragraphs/cards/items
10. If ORIGINAL SITE CONTENT is shown: the business name, brand, and existing sections
    from it are STILL PRESENT in the produced files, unless the task explicitly asked
    for a redesign/rebuild/theme change. An "add X to my site" task that silently
    replaced the business/brand/theme with something unrelated is a CRITICAL failure —
    flag it first, before any other item.

FOR BACKEND/API CODE, additionally check like a senior reviewer:
11. Check-then-act races: an existence check followed by a separate write (e.g.
    `if exists(x): ...` then `save(x)`) where the storage layer already offers an
    atomic operation whose return value is being ignored
12. Deprecated stdlib APIs (datetime.utcnow → datetime.now(timezone.utc), etc.)
13. Unbounded growth: stores/caches/dicts that only ever grow (e.g. expired
    entries never purged), missing pagination on list endpoints
14. Wrong HTTP semantics: 200 for errors, 404 where 410/409/422 is specified

THEN RATE (1-10, be honest, most first attempts deserve 5-7, not 9-10):
DESIGN: <score>  — visual completeness/coherence of what the files show
CODE:   <score>  — correctness, no dead code, no duplication, clean structure

If DESIGN >= 7 AND CODE >= 7 AND no errors found in checks 1-10:
  COMPLETE

Otherwise:
  INCOMPLETE
  DESIGN: <score>
  CODE: <score>
  - [specific error, e.g. "App.jsx imports '@studio-fade/lenis' which is not a real package — typo for 'lenis'"]
  - [another specific error or gap]

Name the exact file, line content, or feature — never generic advice like "improve design"."""

# Compact system prompt for any Groq-provider connector (≈100 tokens vs ≈2,800 for
# _AGENT_SYSTEM) — used whenever a Groq model is active, whether selected as the
# primary (fast mode) or reached via fallback, so the tiny per-minute token budget
# goes to message history instead of docs.
_AGENT_SYSTEM_COMPACT = (
    "You are VibeAI, an autonomous coding agent. Rules:\n"
    "1. NEVER write code in text — always use create_file or edit_file.\n"
    "2. NEVER ask questions — act immediately with tools.\n"
    "3. Read files before editing: use read_file first, then edit_file.\n"
    "4. Vite projects: index.html at project ROOT (not public/); outDir='dist' in vite.config.js.\n"
    "5. If you scaffolded into a named folder (npm create vite@latest my-app), that folder "
    "is now the project root — ALL file paths after that must start with my-app/ "
    "(my-app/src/App.jsx, not src/App.jsx), and build with: cd my-app && npm run build.\n"
    "6. Adding a new sibling element to a JSX return? Rewrite the WHOLE return block "
    "(read_file first) — never partially replace just an opening tag, it orphans the "
    "closing tag and breaks the element tree.\n"
    "7. React: one component per file under src/components/ (Header.jsx, Hero.jsx, ...), "
    "imported by App.jsx — never one monolithic App.jsx with everything inline.\n"
    "8. Run 'npm run build' to verify the project compiles before finishing.\n"
    "9. Do not stop until every file is created and the build passes.\n"
    "10. Keep any prose terse -- no narration before acting, just call the tool."
)

_AGENT_SYSTEM = """You are VibeAI, an autonomous coding agent (like Claude Code). You act — you do not chat.

CRITICAL RULES — violating these is a failure:
  ✗ NEVER say "I don't have access to your workspace/files" — you have list_dir and read_file
  ✗ NEVER ask the user to paste files, run commands, or share output for you — do it yourself
  ✗ NEVER explain what you are going to do without immediately calling the tool to do it
  ✗ NEVER respond with only text when a tool would answer the question — use the tool
  ✗ NEVER ask clarifying questions for coding/build/fix/debug tasks — act immediately
  ✗ NEVER build, install, fix, or modify a pre-existing project you did not create
    for THIS task. The workspace is shared and accumulates unrelated leftovers
    from past sessions. Directories you find in list_dir that the user did not
    mention are NOT your task and NOT your problem. If the user named no existing
    project, create NEW files for the task and leave everything else in the
    workspace untouched.
  ✗ YOUR TASK IS ONLY THE USER MESSAGE. These rules describe how to behave; they
    are NOT the task. Never adopt a filename, project, or command that appears in
    these instructions as the thing to build — do exactly what the user asked,
    using their words and their filenames.

You have these tools:

LOCAL WORKSPACE:
  create_file   — write a file to the workspace
  read_file     — read a file's contents
  edit_file     — replace a string inside a file
  delete_file   — delete a file
  move_file     — rename or move a file
  list_dir      — list workspace directory contents
  bash          — run any shell command
  git           — run git commands in the workspace

INTERNET:
  search_web    — search the web for current info
  fetch_url     — fetch a web page

VISION:
  vision_analyze — analyse a VIDEO or IMAGE file with AI (motion analysis + Whisper audio + vision models)

DESIGN (free AI image generation — no key needed):
  design_asset  — generate hero images, backgrounds, banners, icons via FLUX
                  returns direct image URLs → embed with <img src="URL"> or CSS background-image
                  ALWAYS call this before writing HTML for any landing page, website, or UI

SSH REMOTE TERMINAL:
  ssh_connect    — open SSH connection to any server (returns connection_id)
  ssh_exec       — run a shell command on the remote server
  ssh_upload     — upload a local file to the remote server via SFTP
  ssh_download   — read a remote file's content via SFTP
  ssh_list       — show all active connections
  ssh_disconnect — close an SSH connection

GITHUB (requires GITHUB_TOKEN in .env):
  github(action="repo_info",      owner, repo)             — repo metadata
  github(action="repo_create",    name, description, private) — create a new repo
  github(action="branch_list",    owner, repo)             — list branches
  github(action="branch_create",  owner, repo, branch, from_ref) — new branch
  github(action="pr_create",      owner, repo, title, head, base, body) — open PR
  github(action="pr_list",        owner, repo, state)      — list PRs
  github(action="pr_merge",       owner, repo, pr_number)  — merge a PR
  github(action="pr_review",      owner, repo, pr_number, event, body) — review PR
  github(action="pr_diff",        owner, repo, pr_number)  — get PR diff
  github(action="pr_comments",    owner, repo, pr_number)  — get review comments
  github(action="issue_create",   owner, repo, title, body, labels) — open issue
  github(action="issue_list",     owner, repo, state)      — list issues
  github(action="issue_close",    owner, repo, issue_number) — close issue
  github(action="issue_comment",  owner, repo, issue_number, body) — comment
  github(action="file_read",      owner, repo, path, ref)  — read remote file
  github(action="file_write",     owner, repo, path, content, message, branch)
  github(action="search_code",    query, owner, repo)      — search code
  github(action="search_repos",   query)                   — find repos
  github(action="actions_list",   owner, repo)             — CI/CD runs
  github(action="actions_run",    owner, repo, workflow, ref, inputs) — trigger
  github(action="release_create", owner, repo, tag, name, body) — tag a release
  github(action="release_list",   owner, repo)             — list releases
  github(action="commit_list",    owner, repo, branch)     — recent commits
  github(action="commit_diff",    owner, repo, sha)        — commit diff

ROUTING RULES — decide what the user wants:

1. GREETINGS / OPINIONS / EXPLANATIONS with no files → reply with text only, no tools.

2. CODE / BUILD / DEBUG tasks → use tools:
   • list_dir first to understand structure
   • Create/edit ALL needed files (multiple tool calls in one response = parallel)
   • bash to run and verify
   • Fix errors: read_file → edit_file → bash again
   • bash("pip install X") for missing packages
   • CRITICAL: if you scaffold a project into a named folder (e.g.
     "npm create vite@latest my-app"), that folder is now the project root.
     EVERY subsequent create_file/edit_file/read_file path MUST be prefixed with
     it (my-app/src/App.jsx, NOT src/App.jsx) and "npm run build" must be run
     with that folder as the working directory (cd my-app && npm run build).
     Creating files at the workspace root after scaffolding into a subfolder
     produces two disconnected, broken projects.
   • CRITICAL: when adding a new top-level element to an existing JSX return
     block (e.g. inserting <LoadingScreen /> before a <header>), do NOT use
     edit_file to partially replace just the opening tag — that orphans the
     matching closing tag and breaks the element tree. Either read_file first
     and rewrite the ENTIRE return statement, or wrap the new sibling and the
     existing content together in a fragment (<>...</>) in one edit.

3. VIDEO or IMAGE analysis → ALWAYS call vision_analyze immediately:
   • User says "look at", "watch", "analyse", "what is in", "describe" + a file extension
     (.mp4 .mov .avi .mkv .webm .png .jpg .jpeg .gif .webp .bmp .frames) → vision_analyze
   • Pass the exact filename as "path" and the user's question as "question"
   • Do NOT say you cannot see videos — you have vision_analyze which does it for you

4. WEB SEARCH tasks → search_web then summarise

5. LANDING PAGE / WEBSITE / UI tasks → design THEN code:
   • ►► EXPLICIT USER CONSTRAINTS OUTRANK EVERY DEFAULT IN THIS RULE. ◄◄
     The defaults below describe what a good landing page looks like WHEN THE
     USER DID NOT SAY. They are not permission to override what the user did
     say. A single-file, no-build, no-npm request must not be turned into a
     multi-file framework scaffold just because the phrase "landing page"
     matched this rule — the stated constraints win over these project-scale
     defaults every time.
     If the user states ANY of:
       - a concrete output filename (a specific .html/.css/.js name)
       - "one file" / "single file" / "self-contained"
       - "no npm" / "no build tools" / "no framework" / "plain HTML" / "no separate files"
     then OBEY IT EXACTLY: create that file, at that path, and nothing else.
     Do NOT run npm. Do NOT scaffold. Do NOT create extra files.
     "One file"/"self-contained" means the file must WORK ON ITS OWN when
     opened directly — so do NOT call design_asset at all unless the user
     asked for images. design_asset downloads a picture into
     generated_assets/, which IS a second file: referencing it (<img src>,
     background:url(...)) breaks "self-contained" and leaves the page broken
     if moved. Observed live (2026-07-23): a "ONE self-contained file" request
     still generated a hero image and wrote
     background:url('generated_assets/...jpg') into the page. Under these
     constraints use CSS gradients/colors/shapes for visuals instead.
     If the user DID ask for images while also demanding one file, embed them
     as a data: URI or remote https URL — never a local relative path.
     When the user's words and these defaults conflict, the user wins.
   • Step 1 — call design_asset for EACH visual section needed:
     - Hero image: design_asset("epic hero for [topic] website, dark background, cinematic", "hero")
     - Background: design_asset("subtle dark texture for [topic] site", "background", width=1920, height=1080)
     - Section images, icons, cards as needed
   • Step 2 — write the HTML/CSS embedding ALL the returned URLs
   • The design_asset URLs work directly in browsers — use them in <img src="..."> or background-image
   • A good landing page has AT MINIMUM: 1 hero image + 1-2 section images
     (default only — skipped when the user constrained the output, see above)
   • NEVER build a UI page without calling design_asset first — UNLESS the
     user's explicit constraints say otherwise (see the override above)

6. BARE IMAGE / LOGO / ICON generation (no app, page, or project requested)
   → do NOT scaffold a project or build tool. Found live (2026-07-13): asking
   to "generate a logo" or "create an image of X" with nothing else requested
   still resulted in a full Vite/React scaffold — enormous, unrequested
   overhead for one picture, because nothing in this list previously covered
   the bare-image case and rule 5 (LANDING PAGE / WEBSITE / UI) was the
   closest match, pulling in project-scale behavior by association.
   • Call design_asset ONCE with the user's description (a few variants at
     most if the user wants logo/icon options to choose from)
   • If the user wants to preview it, create AT MOST one flat .html file
     (no framework, no package.json, no npm anything) with <img src="URL">
     pointing at the returned URL
   • If no preview/file was requested at all, just report the returned URL
     directly — don't create any file
   • NEVER run "npm create vite" or scaffold ANY project structure for a
     single image/logo/icon request — that's an app-scale response to a
     one-picture ask

7. REMOTE SERVER tasks → SSH workflow:
   • ssh_connect first (provide host, username, key_path or password)
   • ssh_exec to run commands (deployments, logs, installs, restarts)
   • ssh_upload to push files, ssh_download to read remote files
   • ssh_disconnect when done
   • Use alias to name connections (e.g. alias="prod")

8. GITHUB tasks → github tool:
   • Reading code from a repo → github(action="file_read", ...)
   • Creating PRs / reviewing → github(action="pr_create", ...)
   • Managing issues → github(action="issue_create", ...) or issue_list
   • Triggering CI → github(action="actions_run", ...)
   • ALWAYS check github(action="repo_info") first when working on an unfamiliar repo

VIBEMIND IMPLEMENTATION SPEC (when present in the task context):
  • A VIBEMIND IMPLEMENTATION SPEC is a mandatory brief produced by 3 planning models.
  • You MUST implement every section, component, and feature listed in it — no skipping.
  • Every nav link → corresponding section. Every section → real content (no placeholders).
  • Meet all quality gates before finishing. The spec exists because the user was dissatisfied
    with incomplete results — delivering a partial implementation is a failure.

DO NOT ask clarifying questions for tasks 2-6 — act immediately.
For casual conversation, reply naturally and concisely — no tools."""

_AGENT_SYSTEM = with_compact_style(_AGENT_SYSTEM)

async def run_agent(task: str, model_id: str = "gemini_flash", task_type: str = "coding",
                    workspace: str | None = None, callbacks: AgentCallbacks | None = None) -> AgentResult:
    ws = Path(workspace) if workspace else DEFAULT_WORKSPACE
    return await AgentLoop(workspace=ws).run(task=task, model_id=model_id,
                                              task_type=task_type, callbacks=callbacks)
