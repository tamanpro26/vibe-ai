"""
manager/claude_manager.py  (v4 — fallback chain integrated)

What changed from v3:
  Every manager generate() call now goes through ManagerFallbackChain.
  If Claude Sonnet 4.6 fails (credits, rate limit), the chain auto-switches
  to the next capable backup tier transparently — no code changes needed.

Fallback chain (tools/manager_fallback.py):
  Tier 1  claude_sonnet_4.6   Anthropic       Primary (paid, frontier)
  Tier 2  qwen36_27b_verifier          Alibaba         QI 56.58, best open overall
  Tier 3  llama33_70b_memory        Xiaomi          Comparable to Opus 4.6
  Tier 4  gemini_flash              Z.AI            77.8% SWE-bench, broad agentic
  Tier 5  gpt_oss_120b_planner      DeepSeek        1M ctx, best complex planning
  Tier 6  gpt_oss_120b_coord         Alibaba         Ultra-long ctx, multimodal
"""
from __future__ import annotations

import asyncio
import base64
import json
import re
import uuid
from pathlib import Path
from typing import Any

from loguru import logger

from config.scaffold_loader import get_prompt
from config.settings import settings
from core.bus import bus
from core.imcp import (
    IMCPMessage, MessageType, TaskJSON,
    TeamActivation, ReviewResult, ModelRef,
)
from core.state import state
from models.registry import registry
from teams.prompt_refiner import PromptRefinerPipeline
from tools.manager_fallback import fallback_chain, BACKUP_MANAGER_SYSTEM


_FORCEABLE_TEAMS = ("brain", "code", "vision", "design", "research")

# Website reasoning levels are orchestration preferences, not model aliases.
# Their presence enters the Manager's normal refiner -> specialist-team ->
# synthesis pipeline instead of the single-model simple-request fast path.
_REASONING_MODE_GUIDANCE = {
    "fast": "Work through VibeAI's orchestration pipeline, but keep the task focused and the final answer concise.",
    "balanced": "Use VibeAI's standard multi-agent plan, specialist work, critique, and synthesis for a dependable answer.",
    "deep": "Use VibeAI's most deliberate orchestration. Examine assumptions, route to the needed specialists, critique the result, and surface meaningful trade-offs.",
}

_VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".frames"}
_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
_MEDIA_RE   = re.compile(
    r'[\w./ \\-]+\.(?:mp4|mov|avi|mkv|webm|frames|png|jpg|jpeg|gif|webp|bmp)',
    re.IGNORECASE,
)


def _detect_media(prompt: str, workspace: Path) -> dict[str, Any]:
    """
    Scan prompt for video/image file paths that exist on disk.
    Returns extra dict ready for VisionTeam, or {} if nothing found.
    """
    for raw in _MEDIA_RE.findall(prompt):
        raw = raw.strip()
        for candidate in (workspace / raw, Path(raw)):
            if candidate.exists():
                ext = candidate.suffix.lower()
                if ext in _VIDEO_EXTS:
                    logger.info(f"[manager] media detected: video → {candidate.name}")
                    return {"video_path": str(candidate)}
                if ext in _IMAGE_EXTS:
                    logger.info(f"[manager] media detected: image → {candidate.name}")
                    return {"image_b64": base64.b64encode(candidate.read_bytes()).decode()}
    return {}


def _get_team(name: str):
    if name == "brain":
        from teams.brain import BrainTeam; return BrainTeam()
    if name == "code":
        from teams.code import CodeTeam; return CodeTeam()
    if name == "vision":
        from teams.vision import VisionTeam; return VisionTeam()
    if name == "design":
        from teams.design import DesignTeam; return DesignTeam()
    if name == "research":
        from teams.research import ResearchTeam; return ResearchTeam()
    if name == "router":
        from teams.router_team import RouterTeam; return RouterTeam()
    raise ValueError(f"Unknown team: {name}")


_MANAGER_SYSTEM = get_prompt("MANAGER_SYSTEM", """You are the manager of VibeAI — a 31-model AI system for vibe coding,
debugging, UI/animation design, and video analysis. You manage 5 specialist teams.

REVIEW task — score output 0.0–1.0 against success_criteria[]. Return ONLY JSON:
{
  "quality_score": 0.0-1.0,
  "criteria_passed": ["..."],
  "criteria_failed": ["..."],
  "issues": ["specific issue"],
  "refine_instruction": "exact fix instruction",
  "action": "APPROVE|REFINE|ESCALATE"
}
APPROVE >= 0.85. REFINE below. ESCALATE after 3 attempts.
For design/image team output: a real generated asset URL (an http/https
link) IS the deliverable — score it on relevance to the request, not on
prose or explanation. Never mark a working image URL as low quality just
because it lacks surrounding text.

SYNTHESIS task — combine team outputs into one polished response. If any
team output (especially DESIGN) contains a generated asset URL (http/https
link to an actual image), you MUST include that exact URL verbatim in your
response — it is a real, already-generated deliverable, not a suggestion.
Never replace it with generic advice like "paste this prompt into
Midjourney/DALL-E/Stable Diffusion" — the image already exists at that URL.""")


_FAST_SYSTEM = get_prompt("FAST_SYSTEM", """You are VibeAI, a multi-model AI assistant. This request was
classified as simple, so you are answering it directly.
Answer well and completely: for questions give a clear, accurate answer; for
small coding requests give clean, working code with a docstring. Do not
mention internal teams, routing, or classification.""")


class ClaudeManager:
    def __init__(self) -> None:
        self._refiner          = PromptRefinerPipeline()
        self._memory_ready     = False
        self._memory_init_task: asyncio.Task | None = None
        bus.subscribe("claude_sonnet_4.6", self._handle_message)

    # ── Startup ───────────────────────────────────────────────────────────────

    async def startup(self) -> None:
        """
        Kicks off memory init in the background instead of awaiting it here.
        Found live (2026-07-15): memory.init() alone took ~35s (SentenceTransformer
        model load + a Hub round-trip), and this method used to be awaited
        directly by cli.py's main() before the interactive prompt ever showed --
        every single launch paid that cost even though _memory_ready already
        degrades gracefully to "no memory this call" everywhere it's checked
        (_retrieve_memory/_store_memory below). Nothing in this codebase
        actually requires memory to be ready before the manager is usable.
        """
        self._memory_init_task = asyncio.create_task(self._init_memory_background())
        logger.info(
            f"[manager] ready | active_manager={fallback_chain.active_name} "
            "| memory loading in background"
        )

    async def _init_memory_background(self) -> None:
        # Confirmed live (2026-07-19): even backgrounded via create_task,
        # memory.init()'s SentenceTransformer/chromadb load is heavy enough
        # real CPU/disk work (~30-60s on a cold cache, per this project's
        # own docs) that it raced the CLI's interactive input-thread for
        # actual OS-level CPU time at the exact moment the terminal tried
        # to render its first prompt -- observed as the prompt appearing to
        # hang for up to 30s, even though internal timestamps proved the
        # asyncio scheduling itself was instant. A short delay here doesn't
        # change the event-loop logic, it just lets the terminal finish
        # rendering and start blocking on a read (which needs ~no CPU)
        # before the heavy load competes for cycles.
        await asyncio.sleep(1.5)
        try:
            from tools.memory import memory
            self._memory_ready = await memory.init()
        except Exception as exc:
            logger.warning(f"[manager] memory init skipped: {exc}")
        try:
            from core.collective_memory import collective_memory
            await collective_memory.init()
        except Exception as exc:
            logger.warning(f"[manager] collective memory init skipped: {exc}")
        if self._memory_ready:
            logger.info("[manager] background memory init complete")

    # ── Entry point ───────────────────────────────────────────────────────────

    async def handle_user_request(
        self,
        user_prompt: str,
        extra: dict[str, Any] | None = None,
        forced_team: str | None = None,
        reasoning_mode: str | None = None,
        memory_namespace: str = "",
        supplied_search_context: str = "",
        capability_snapshot: Any | None = None,
    ) -> str:
        session_id = f"s_{uuid.uuid4().hex[:8]}"
        extra      = extra or {}
        reasoning_guidance = _REASONING_MODE_GUIDANCE.get(reasoning_mode or "")
        from core.collab_viz import emit as viz
        viz("stage", "User request received")
        from capabilities.context import apply_capability_context

        capability_prompt = apply_capability_context(user_prompt, capability_snapshot)
        if capability_prompt != user_prompt:
            selected_count = len(
                capability_snapshot.selected
                if hasattr(capability_snapshot, "selected")
                else capability_snapshot.get("selected", [])
            )
            logger.info(f"[manager] capability snapshot attached ({selected_count} selected)")

        # Detect video/image paths mentioned in the prompt and inject into extra
        if not extra:
            from tools.agent_tools import DEFAULT_WORKSPACE
            detected = _detect_media(user_prompt, DEFAULT_WORKSPACE)
            if detected:
                extra = detected

        if fallback_chain.using_backup:
            logger.warning(
                f"[manager] ⚡ running on backup: {fallback_chain.active_name}"
            )
        else:
            logger.info(f"[manager] ── session {session_id}")

        # 0a. Creative path — writing tasks get the 5-step creative pipeline
        #     (runs before fast path so creative tasks are never fast-pathed)
        # Both 0a and 0b bypass classification entirely, so an explicit team
        # override (e.g. the website's "Route to team" selector) must skip
        # them -- otherwise the user's pick would be silently ignored exactly
        # the way it was before this was wired up.
        if not extra and forced_team is None and reasoning_guidance is None:
            try:
                from tools.creative_engine import creative_engine, is_creative_task
                if is_creative_task(user_prompt):
                    logger.info("[manager] creative task detected — creative engine")
                    viz("stage", "Creative task detected — 5-step creative pipeline")
                    return await creative_engine.synthesize(capability_prompt)
            except Exception as exc:
                logger.warning(f"[manager] creative engine failed ({str(exc)[:60]}) — pipeline")

        # 0b. Fast path — simple text-only requests skip the heavy pipeline
        if not extra and forced_team is None and reasoning_guidance is None:
            viz("stage", "Manager triaging request (fast path check)…")
            fast = await self._try_fast_path(capability_prompt)
            if fast is not None:
                viz("stage", "Simple request — answered directly (fast path)", status="done")
                return fast

        # 1a. Prompt Enhancer — strengthen prompt before the Prompt Refiner sees it
        pipeline_prompt = capability_prompt
        if reasoning_guidance:
            pipeline_prompt = (
                f"{capability_prompt}\n\n"
                f"[VibeAI reasoning level: {reasoning_mode}]\n{reasoning_guidance}"
            )

        enhanced_prompt = pipeline_prompt
        try:
            from teams.prompt_enhancer import prompt_enhancer
            viz("stage", "Prompt Enhancer strengthening the request (3 models in parallel)…")
            enhanced_prompt = await prompt_enhancer.enhance(pipeline_prompt)
        except Exception as exc:
            logger.warning(f"[manager] prompt enhancer failed ({str(exc)[:60]}) — using original")

        # 1b. Prompt Refiner → Task JSON  (uses enhanced prompt as input)
        viz("stage", "Prompt Refiner analyzing and classifying the task…")
        task_json = await self._refiner.run(enhanced_prompt, session_id)
        task_json.original_prompt = user_prompt   # keep the user's raw prompt for review/memory

        # Explicit team override beats the refiner's own classification -- the
        # caller asked for a specific team by name, so honor it instead of
        # guessing why their pick disagrees with the classifier. Mirrors the
        # force-activation pattern _dispatch already uses for media-detected
        # vision requests, just driven by the caller instead of by content.
        if forced_team in _FORCEABLE_TEAMS:
            for team_key in _FORCEABLE_TEAMS:
                activation = task_json.active_teams.get(team_key)
                if activation is None:
                    activation = TeamActivation(active=False)
                    task_json.active_teams[team_key] = activation
                activation.active = (team_key == forced_team)
            logger.info(f"[manager] team override: forcing '{forced_team}'")

        # Media requests keep the user's ORIGINAL wording.
        #
        # Every prompt-refiner stage is a text-only model, so when the ask is
        # about an attachment it cannot see, refinement doesn't sharpen the
        # prompt -- it corrupts it. Observed live: "Describe this image. What
        # shape and what colour is it?" came back refined as "Please provide
        # the image you would like me to describe", a model's REPLY captured
        # as the refined prompt. That string then became the vision team's
        # instruction and the web-search query, so the one team that CAN see
        # the image was told to ask for it.
        #
        # The refiner's other output (classification, team activation) is
        # still useful and is kept; only the rewritten prompt is discarded.
        if extra and task_json.original_prompt:
            if task_json.refined_prompt != task_json.original_prompt:
                logger.info(
                    "[manager] media attached — keeping the original prompt "
                    "over the text-only refiner's rewrite"
                )
            task_json.refined_prompt = capability_prompt

        await state.save_session(session_id, task_json)
        _active_teams = [t.upper() for t, c in task_json.active_teams.items()
                         if c.active and t != "router"]
        viz("stage", f"Task classified — teams assigned: {' + '.join(_active_teams) or 'none'}",
            status="done")

        # 2. Memory retrieval (Upgrade 6)
        memory_ctx = await self._retrieve_memory(
            task_json.refined_prompt, workspace=memory_namespace
        )

        # 3. Search context (Upgrade 1)
        search_ctx = supplied_search_context or await self._fetch_search_context(task_json)

        # 4. Dispatch teams
        team_outputs = await self._dispatch(
            session_id, task_json, extra, memory_ctx, search_ctx
        )

        # 5. Synthesise via fallback chain
        viz("stage", "Manager combining team results into the final answer…")
        try:
            final = await self._synthesise(task_json, team_outputs)
        except Exception as exc:
            # Found in code review (2026-07-13): this call was unguarded, so
            # a correlated failure (all 5 Free Manager Council members dying
            # at the same pipeline stage -- the exact scenario DECISIONS.md
            # already documents happening live for 2-provider overlaps)
            # raised past every caller with no try/except along the way
            # (this function, the REST /api/prompt handler, the WS pipeline's
            # own except-block notwithstanding) -- turning a request whose
            # team_outputs above had ALREADY been computed successfully into
            # a raw 500, discarding that real, completed work. Degrading to
            # the best available team output is strictly better than
            # throwing away work that already succeeded.
            logger.warning(f"[manager] synthesis failed ({str(exc)[:100]}) — degrading to raw team output")
            final = self._degrade_to_team_outputs(team_outputs)

        # 6. Store approved outputs
        await self._store_memory(
            task_json, team_outputs, session_id, workspace=memory_namespace
        )
        viz("stage", "Final answer ready", status="done")

        return final

    @staticmethod
    def _degrade_to_team_outputs(team_outputs: dict[str, str]) -> str:
        """Best-effort final answer when synthesis itself fails. Prefers the
        longest non-error team output (closest to a complete answer) over
        just concatenating everything, since most callers show this as a
        single response, not a multi-section report."""
        usable = {
            team: output for team, output in team_outputs.items()
            if output and not output.startswith("[ERROR:")
        }
        if not usable:
            return (
                "All specialist teams and the manager's synthesis step failed for this "
                "request. Please try again in a moment."
            )
        best_team, best_output = max(usable.items(), key=lambda kv: len(kv[1]))
        return (
            f"{best_output}\n\n"
            f"[Note: the manager's final synthesis step failed, so this is the "
            f"{best_team} team's own output shown directly, unedited.]"
        )

    # ── Fast path ─────────────────────────────────────────────────────────────

    async def _try_fast_path(self, user_prompt: str) -> str | None:
        """
        Triage with the router (~1s). Simple requests get a direct answer
        from a fast, strong model instead of the full refine → dispatch →
        review pipeline — 10-50x faster for the most common requests.
        Returns None when the request deserves the full pipeline.
        """
        try:
            from teams.router_team import RouterTeam
            quick = await RouterTeam().classify_quick(user_prompt)
        except Exception as exc:
            logger.warning(f"[manager] triage failed ({str(exc)[:60]}) — full pipeline")
            return None

        # Gate on task_type too, not just the needs_design/needs_vision
        # booleans -- found live (2026-07-13): the local edge-router model
        # (qwen25_3b_ollama) correctly classified "generate a logo for my
        # startup" as task_type="ui_design" but left needs_design unset
        # (None, not False) often enough to matter. `not None` is True, so
        # the boolean-only gate let it slip through to the fast path anyway.
        # A real image/design/vision request must never hit the fast path
        # (a plain text model with no image capability), even if one of the
        # two signals the classifier emits is unreliable this run.
        # "research" is here for the same reason ui_design is: the fast path is
        # one plain text model answering from its weights. That is precisely the
        # wrong answer for a question about current facts -- it would produce a
        # confident, uncited, potentially stale reply instead of one grounded in
        # retrieved sources. Route it to ResearchTeam, which searches first.
        _needs_full_pipeline = (
            quick.get("needs_vision")
            or quick.get("needs_design")
            or quick.get("task_type") in ("ui_design", "animation", "video_analysis", "research")
        )
        # A pure coding/debugging task takes the direct path at ANY complexity,
        # not just "simple". Measured 2026-08-08 on vibeloop's 10-task v4 train
        # split (deterministic execution checks, no LLM judging):
        #
        #   full pipeline (enhancer -> refiner -> dispatch -> review)  0.70, ~20min
        #   gpt_oss_120b_coder alone, this same _CODE_SYSTEM           1.00, 115s
        #   gpt_oss_120b_coder alone, generic prompt                   1.00, 46-69s
        #   llama33_70b_coder (the CHEAP tier) alone                   1.00, 73s
        #
        # Every task the pipeline lost, a single model solved — the three
        # remaining losses were 180s timeouts with zero output, not wrong
        # answers. Running it with VibeAI's own _CODE_SYSTEM rules out "the
        # bare model just had a better prompt": the prompts are fine, the
        # orchestration is what costs the score.
        # These tasks classify complexity="moderate", so they missed the old
        # gate by one word and paid the whole pipeline for a worse answer.
        #
        # Deliberately NOT extended to ui_design/animation/video_analysis
        # (already excluded above) or "mixed" — multi-part work is the case
        # orchestration is actually for, and this benchmark does not measure it.
        _is_pure_code = quick.get("task_type") in ("vibe_coding", "debugging")

        if not _needs_full_pipeline and (
            quick.get("complexity") == "simple" or _is_pure_code
        ):
            logger.info(
                f"[manager] ⚡ fast path{' (code)' if _is_pure_code else ''}: "
                f"{quick.get('quick_summary', user_prompt[:60])}"
            )
            from models.registry import generate_resilient
            if _is_pure_code:
                from teams.code import _CODE_SYSTEM
                fast_model, fast_system, fast_temp = (
                    "gpt_oss_120b_coder", _CODE_SYSTEM, 0.2,
                )
            else:
                fast_model, fast_system, fast_temp = (
                    "qwen36_27b_verifier", _FAST_SYSTEM, 0.6,
                )
            answer = await generate_resilient(
                fast_model,   # qwen36_27b_verifier is qwen/qwen3.6-27b — a REASONING model
                prompt=user_prompt,
                system=fast_system,
                # Hidden reasoning is billed against max_tokens, so this budget
                # buys reasoning AND the visible answer. Measured live on one
                # graded task (2026-08-08): at 2048, 7/7 calls returned
                # finish_reason="length" at exactly 2048 completion tokens —
                # every answer cut mid-sentence. At 4096, 0/3 truncated, but
                # real usage was 2611–3982, so 4096 leaves only 3% headroom.
                # 2000 was therefore truncating the fast path's answers as a
                # matter of course; it surfaced downstream as SyntaxError on
                # half-written code, not as an obvious generation failure.
                # 5000 is the most the connector's own TPM formula allows here
                # (6000 TPM − ~195 input − 800 margin = 5005).
                max_tokens=5000,
                temperature=fast_temp,
            )

            # The fast path skips team dispatch entirely, so code returned here
            # never reached CodeTeam._verify and was the ONE output path with no
            # ground-truth check at all. Confirmed live against the deployed
            # backend (2026-08-01): asking for a snippet that calls an undefined
            # function returned it unrepaired, because triage classified the
            # request "simple" and answered directly.
            #
            # Simple requests are the common case, so leaving this unverified
            # would mean most generated code is never executed -- which is the
            # exact defect the verification work set out to remove.
            if "```python" in (answer or ""):
                answer = await self._verify_fast_code(user_prompt, answer)
            return answer
        return None

    async def _verify_fast_code(self, user_prompt: str, answer: str) -> str:
        """
        Import-test any Python in a fast-path answer; on failure hand the real
        traceback to CodeTeam's repair pass.

        Reuses CodeTeam rather than duplicating the harness: one definition of
        "verified" for both paths, so they cannot drift apart.
        """
        try:
            from teams.code import CodeTeam, _extract_python

            code = _extract_python(answer)
            if not code:
                return answer

            # CodeTeam._verify returns the input unchanged when the code is
            # sound, and a repaired version when it is not -- so this is a
            # no-op on the happy path and costs one extra model call only when
            # the code genuinely does not run.
            return await CodeTeam()._verify(answer, user_prompt)
        except Exception as exc:
            # Verification is an improvement, never a new failure mode: a
            # broken checker must not take down an answer that was already
            # produced successfully.
            logger.warning(f"[manager] fast-path verify skipped ({str(exc)[:80]})")
            return answer

    # ── Manager generate — all calls go through fallback chain ────────────────

    async def _manager_generate(
        self,
        prompt:      str,
        system:      str = "",
        max_tokens:  int   = 1000,
        temperature: float = 0.3,
        task_kind:   str   = "",
    ) -> str:
        """All manager LLM calls route through here → auto-failover.

        task_kind ("review" | "synthesis") tells the Free Manager Council
        which mode to use when it has taken over — explicit routing instead
        of keyword guessing.
        """
        return await fallback_chain.generate(
            prompt=prompt,
            system=system or _MANAGER_SYSTEM,
            max_tokens=max_tokens,
            temperature=temperature,
            task_kind=task_kind,
        )

    # ── Memory helpers ────────────────────────────────────────────────────────

    async def _retrieve_memory(self, query: str, workspace: str = "") -> str:
        if not self._memory_ready:
            return ""
        try:
            from tools.memory import memory
            return await memory.retrieve_context(query, top_k=3, workspace=workspace)
        except Exception:
            return ""

    async def _store_memory(
        self, task_json: TaskJSON, outputs: dict[str, str], session_id: str,
        workspace: str = "",
    ) -> None:
        if not self._memory_ready:
            return
        try:
            from tools.memory import memory
            for team, output in outputs.items():
                if not output.startswith("[ERROR"):
                    await memory.store_solution(
                        task_description=task_json.refined_prompt,
                        team=team, output=output, quality_score=0.85,
                        task_id=task_json.task_id,
                        task_type=task_json.classification.primary_type.value,
                        workspace=workspace,
                    )
        except Exception as exc:
            logger.warning(f"[manager] memory store failed: {exc}")

    # ── Search context ────────────────────────────────────────────────────────

    async def _fetch_search_context(self, task_json: TaskJSON) -> str:
        try:
            from tools.search import search_stack
            results = await search_stack.search(task_json.refined_prompt[:80])
            return search_stack.format_for_prompt(results) if results else ""
        except Exception:
            return ""

    # ── Dispatch ──────────────────────────────────────────────────────────────

    async def _dispatch(
        self,
        session_id: str,
        task_json:  TaskJSON,
        extra:      dict[str, Any],
        memory_ctx: str,
        search_ctx: str,
    ) -> dict[str, str]:
        # If media was detected, force VisionTeam active regardless of Prompt Refiner
        if ("video_path" in extra or "image_b64" in extra or "image_url" in extra):
            if "vision" not in task_json.active_teams or not task_json.active_teams["vision"].active:
                from core.imcp import TeamActivation, Priority
                task_json.active_teams["vision"] = TeamActivation(
                    active=True,
                    models=["gemini_flash_vision", "nemotron_vl", "llama4_maverick"],
                    instruction=task_json.refined_prompt or task_json.original_prompt,
                    priority=Priority.HIGH,
                )
                logger.info("[manager] VisionTeam force-activated (media detected)")

        active = {
            t: c for t, c in task_json.active_teams.items()
            if c.active and t != "router"
        }
        from core.collab_viz import emit as viz
        viz("stage", f"Dispatching {len(active)} team(s) in parallel: "
                     f"{', '.join(t.upper() for t in active)}")
        tasks = [
            self._run_team(session_id, task_json, t, c, extra, memory_ctx, search_ctx)
            for t, c in active.items()
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for tk, r in zip(active.keys(), results):
            if isinstance(r, Exception):
                viz("stage", f"{tk.upper()} team failed: {str(r)[:80]}", status="fail")
        return {
            tk: (f"[ERROR: {r}]" if isinstance(r, Exception) else r)
            for tk, r in zip(active.keys(), results)
        }

    async def _run_team(
        self,
        session_id: str,
        task_json:  TaskJSON,
        team:       str,
        cfg:        TeamActivation,
        extra:      dict[str, Any],
        memory_ctx: str,
        search_ctx: str,
    ) -> str:
        team_obj = _get_team(team)

        # The per-team instruction from nemotron_nano_format is meant to be
        # team-specific COORDINATION guidance (style/composition/scope), not
        # a replacement for the actual task content. Found live (2026-07-13):
        # for "create an image of a cat wearing sunglasses", the design
        # team's cfg.instruction came back as "Select an appropriate
        # artistic style, composition, and ensure the image meets style and
        # quality guidelines for general audiences" -- zero mention of a
        # cat -- and since this is truthy, it silently REPLACED (never
        # supplemented) refined_prompt in the old fallback below. Fed
        # verbatim to flux_asset (a raw text-to-image model with no other
        # source of "what to draw"), it generated a photorealistic
        # architectural interior, completely unrelated to the request.
        # Whether a per-team instruction happens to restate the subject
        # isn't guaranteed -- it's a stochastic model output that varies
        # per run -- but refined_prompt is the one field guaranteed to
        # carry it, so it must never be dropped, only supplemented.
        team_instruction = cfg.instruction or task_json.team_instructions.get(team, "")
        instruction = (
            f"{task_json.refined_prompt}\n\n{team_instruction}".strip()
            if team_instruction else task_json.refined_prompt
        )

        if memory_ctx:
            instruction = memory_ctx + "\n\n" + instruction
        if search_ctx and team in ("brain", "code"):
            instruction = search_ctx + "\n\n" + instruction

        # Math routing (Upgrade 3)
        if team in ("brain", "code"):
            try:
                from tools.code_executor import reasoner
                if reasoner.is_computational(instruction):
                    result = await reasoner.solve(instruction)
                    await state.save_output(
                        session_id, task_json.task_id, team, team, result, 0.9, True, 1
                    )
                    return result
            except Exception:
                pass

        # Brief pipeline (Upgrade 2) -- only for design tasks that also have
        # a code team to coordinate with. Found live (2026-07-13): brief
        # enforcement wraps the instruction in meta-text ("You are executing
        # a design task within a mandatory Creative Brief. You MUST follow
        # the brief exactly...") meant for an LLM reading it as instructions.
        # That's fine when the design team's own model is an LLM (e.g.
        # glm_47_cerebras writing CSS/motion specs to keep in sync with
        # CodeTeam's output) -- but a bare "create an image of a cat wearing
        # sunglasses" request has no code team active, and DesignTeam's
        # image models (flux_asset, flux_world, ...) are raw text-to-image
        # endpoints with zero instruction-following semantics: the entire
        # meta-instruction text got embedded as the literal Pollinations
        # prompt, producing an image of the JSON brief itself and a URL
        # containing "You%20are%20executing%20a%20design%20task..." instead
        # of a cat. Brief coordination only has a job to do when there's a
        # second team (code) to stay consistent with; skip it otherwise so
        # DesignTeam's own lightweight _expand_prompt (in teams/design.py)
        # is the only enrichment applied before the raw image call.
        code_team = task_json.active_teams.get("code")
        if team == "design" and code_team and code_team.active:
            try:
                from tools.brief_pipeline import brief_pipeline
                _, instruction = await brief_pipeline.create_and_enforce(
                    instruction, task_json.context.tech_stack
                )
            except Exception:
                pass

        # Quality budget scales with complexity: simple/moderate tasks get one
        # iteration and no adversarial critic — the review still gates quality,
        # but we don't pay 3 full team re-runs for a small function.
        complexity = task_json.classification.complexity.value
        max_iters  = settings.max_review_iterations if complexity == "complex" else 1

        # Main loop with adversarial critic + manager review
        from core.collab_viz import emit as viz
        viz("stage", f"{team.upper()} team working…")
        for iteration in range(1, max_iters + 1):
            output = await team_obj.run(
                task_json=task_json, instruction=instruction,
                iteration=iteration, extra=extra,
            )

            # Adversarial Critic (Upgrade 5) — complex tasks only
            critique_instruction = ""
            if complexity == "complex":
                try:
                    from tools.critic import adversarial_critic
                    tt = "code" if team == "code" else "design" if team == "design" else "general"
                    report = await adversarial_critic.critique(output, task_json.task_id, tt)
                    if adversarial_critic.should_trigger_refine(report):
                        critique_instruction = adversarial_critic.to_refine_instruction(report)
                except Exception:
                    pass

            # Manager review (via fallback chain)
            logger.info(f"[manager] reviewing {team} output (iteration {iteration})")
            viz("stage", f"Manager reviewing {team.upper()} team's output (iteration {iteration})…")
            review = await self._review(
                task_json, team, output, iteration, critique_instruction, max_iters
            )
            await state.save_output(
                session_id, task_json.task_id, team, team,
                output, review.quality_score, review.approved, iteration,
            )
            await state.save_review(session_id, review)

            if review.action == "APPROVE":
                viz("stage", f"{team.upper()} team completed — approved by manager", status="done")
                return output
            if review.action == "ESCALATE" or iteration == max_iters:
                output = await self._escalate(
                    task_json, team, instruction, output, review, max_iters
                ) or output
                viz("stage", f"{team.upper()} team completed (best effort after {iteration} iteration(s))",
                    status="done")
                return output

            viz("stage", f"Manager sent {team.upper()} team back to refine (quality below bar)…")
            instruction = (
                f"{review.refine_instruction}\n\n"
                f"Original: {instruction}\n\nPrevious attempt:\n{output}"
            )

        return output

    # ── Broader-pool escalation (quality-driven, not error-driven) ────────────
    #
    # User request (2026-07-10): "if the task ... cannot be done with good
    # quality with the assigned ais, then the manager ai council or claude
    # model can use any other models by itself ... apis, ollama local models
    # etc." review.action == "ESCALATE" (or exhausting max_iters without an
    # APPROVE) is exactly that "the assigned team can't clear the quality
    # bar" signal -- it already exists, it just used to mean "give up and
    # return best-effort." This reaches ONE model from outside the team's own
    # roster (core/model_escalation.py) before accepting best-effort as
    # final. Bounded to a single extra call; the escalated output only wins
    # if a fresh review actually scores it higher than what the team already
    # produced -- never swaps in a worse answer just because it's different.
    async def _escalate(
        self,
        task_json:   TaskJSON,
        team:        str,
        instruction: str,
        best_output: str,
        review:      ReviewResult,
        max_iters:   int,
    ) -> str | None:
        from config.models_config import MODEL_REGISTRY
        from core.model_escalation import single_shot_candidates
        from core.collab_viz import emit as viz

        # Design escalation is a category error, found live (2026-07-13):
        # a "create an image of a cat wearing sunglasses" run correctly
        # reached DesignTeam and flux_asset/flux_world both returned real
        # Pollinations image URLs -- but the generic reviewer (a text model
        # judging design output the same way it judges code/brain prose)
        # scored the bare "DESIGN OUTPUTS\n...Design asset: <url>" text
        # 0.00 and triggered escalation. The escalation candidate pool
        # (single_shot_candidates) is text/reasoning models with no image
        # generation tool access -- passing them just `instruction` made
        # one invent generic "paste this into Midjourney/DALL-E" prose,
        # which then scored 0.03 > 0.00 and WON, discarding the actual
        # generated image and replacing it with unusable advice. No
        # escalation candidate can ever legitimately improve on a design
        # output that already contains a real generated asset URL, so
        # never escalate away from one.
        if team == "design" and re.search(r"https?://\S+", best_output):
            logger.info(
                "[manager] design output already contains a real generated "
                "asset URL — skipping escalation (no escalation candidate "
                "can generate images)"
            )
            return None

        # Models this team already fields get excluded -- they had their shot.
        exclude = {mid for mid, d in MODEL_REGISTRY.items() if d.team == team}
        try:
            pool = await single_shot_candidates(exclude)
        except Exception as exc:
            logger.warning(f"[manager] escalation pool lookup failed: {str(exc)[:80]}")
            return None
        if not pool:
            return None

        candidate_id = pool[0]
        logger.info(
            f"[manager] {team} team didn't clear the quality bar — escalating to {candidate_id}"
        )
        viz("stage", f"Escalating: {team.upper()} team's output wasn't good enough — trying {candidate_id}…")
        try:
            alt_output = await registry.get(candidate_id).generate(
                prompt=instruction, max_tokens=4096, temperature=0.4, task_type="escalation",
            )
        except Exception as exc:
            logger.warning(f"[manager] escalation call to {candidate_id} failed: {str(exc)[:80]}")
            return None
        if not alt_output or not alt_output.strip():
            return None

        alt_review = await self._review(
            task_json, candidate_id, alt_output, iteration=max_iters, max_iters=max_iters
        )
        if alt_review.quality_score > review.quality_score:
            logger.info(
                f"[manager] escalation improved quality "
                f"{review.quality_score:.2f} -> {alt_review.quality_score:.2f}"
            )
            return alt_output
        logger.info(
            f"[manager] escalation to {candidate_id} did not improve quality "
            f"({alt_review.quality_score:.2f} <= {review.quality_score:.2f}) — keeping original"
        )
        return None

    # ── Review via fallback chain ─────────────────────────────────────────────

    async def _review(
        self,
        task_json: TaskJSON,
        model_id:  str,
        output:    str,
        iteration: int,
        critique:  str = "",
        max_iters: int | None = None,
    ) -> ReviewResult:
        max_iters      = max_iters or settings.max_review_iterations
        criteria       = "\n".join(f"- {c}" for c in task_json.success_criteria)
        critique_block = f"\n\nAdversarial critique:\n{critique}" if critique else ""

        raw = await self._manager_generate(
            prompt=(
                f"Review output against success criteria.\n\n"
                f"CRITERIA:\n{criteria}{critique_block}\n\n"
                f"OUTPUT (iteration {iteration}):\n{output[:3000]}"
            ),
            max_tokens=600,
            temperature=0.1,
            task_kind="review",
        )
        try:
            data   = _parse_json(raw)
            action = data.get("action", "REFINE")
            if iteration >= max_iters and action == "REFINE":
                action = "ESCALATE"
            return ReviewResult(
                task_id=task_json.task_id, model_id=model_id,
                approved=(action == "APPROVE"),
                quality_score=float(data.get("quality_score", 0.5)),
                criteria_passed=data.get("criteria_passed", []),
                criteria_failed=data.get("criteria_failed", []),
                issues=data.get("issues", []),
                refine_instruction=data.get("refine_instruction", ""),
                action=action,
            )
        except Exception:
            return ReviewResult(
                task_id=task_json.task_id, model_id=model_id,
                approved=False, quality_score=0.5,
                action="REFINE" if iteration < max_iters else "ESCALATE",
                refine_instruction="Improve output quality.",
            )

    # ── Synthesis via fallback chain ──────────────────────────────────────────

    # A "**Sources**" block is a factual attachment, not prose to be polished.
    # Measured 2026-08-09: the research team returns a cited answer plus this
    # block, and synthesis dropped it EVERY time -- inline URLs survived only
    # sometimes (0 urls on one run, 12 on the next, same question). Citations
    # are the entire contract of a grounded answer, so they cannot depend on a
    # rewrite step happening to keep them.
    _SOURCES_RE = re.compile(r"\n*---\n\*\*Sources\*\*\n(?:.+\n?)+", re.MULTILINE)
    # The same rewrite also throws away GENERATED ARTIFACTS. Measured
    # 2026-08-09: the design team returned a real Pollinations image URL and
    # synthesis dropped it, substituting inline SVG it wrote itself -- the only
    # surviving URL in the final answer was the w3.org SVG namespace. A user
    # asking for an image got prose about an image. An artifact URL is a
    # pointer to work already done; it cannot be re-derived by paraphrasing.
    _ARTIFACT_RE = re.compile(
        r"https?://[^\s<>\"')]+(?:pollinations|\.png|\.jpg|\.jpeg|\.webp|\.gif)[^\s<>\"')]*",
        re.IGNORECASE,
    )

    async def _synthesise(self, task_json: TaskJSON, outputs: dict[str, str]) -> str:
        logger.info("[manager] synthesising final response")
        block = "\n\n".join(f"=== {t.upper()} ===\n{o}" for t, o in outputs.items())
        active_note = (
            f"[Running on backup manager: {fallback_chain.active_name}]\n\n"
            if fallback_chain.using_backup else ""
        )
        # Pull any sources block out BEFORE synthesis so it can be restored
        # verbatim afterwards. Asking the model nicely is done too, but the
        # re-attach below is what actually guarantees it.
        sources = ""
        artifacts: list[str] = []
        for out in outputs.values():
            found = self._SOURCES_RE.search(out or "")
            if found and not sources:
                sources = found.group(0).rstrip()
            for url in self._ARTIFACT_RE.findall(out or ""):
                if url not in artifacts:
                    artifacts.append(url)

        final = await self._manager_generate(
            prompt=(
                f"{active_note}Synthesise into one polished response.\n\n"
                "Keep every source URL, citation marker and generated-asset URL exactly "
                "as given. They are evidence and delivered work, not styling: an "
                "unsourced claim is worse than no claim, and describing an image the "
                "teams already generated is not the same as delivering it. Never "
                "replace a generated asset URL with your own substitute.\n\n"
                f"Request: {task_json.original_prompt}\n\n{block}"
            ),
            max_tokens=4000,
            temperature=0.4,
            task_kind="synthesis",
        )
        final = final or ""

        # Instructions are asked for above; these re-attaches are what actually
        # guarantee it, because a rewrite step that ignores them is exactly the
        # measured failure.
        missing = [u for u in artifacts if u not in final]
        if missing:
            logger.info(f"[manager] synthesis dropped {len(missing)} asset url(s) — re-attaching")
            final = f"{final.rstrip()}\n\n" + "\n".join(f"![generated asset]({u})" for u in missing)
        if sources and "**Sources**" not in final:
            logger.info("[manager] synthesis dropped the sources block — re-attaching")
            final = f"{final.rstrip()}\n\n{sources}"
        return final

    async def _handle_message(self, msg: IMCPMessage) -> None:
        logger.debug(f"[manager] bus: {msg.type.value} from {msg.from_.model}")


def _parse_json(text: str) -> dict:
    try: return json.loads(text.strip())
    except Exception: pass
    m = re.search(r"\{[\s\S]+\}", text)
    if m: return json.loads(m.group(0))
    raise ValueError("No JSON")


manager = ClaudeManager()
