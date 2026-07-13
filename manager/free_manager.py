"""
manager/free_manager.py  (v3 — 5-model collaborative Council)

The Free Manager Council replaces Claude Sonnet 4.6 the moment it becomes
unavailable. Unlike v2 (which routed each call to a single specialist),
the Council COLLABORATES on every call — even simple ones — through a
5-stage pipeline:

  Plan → Draft → Critique → Refine → Polish

5-member committee, deliberately drawn from NON-brain teams so the
Council never competes with the Brain team for the same models:

  Planner     — Gemini 2.0 Flash   (Google,    free)  — manager team, dedicated
                quota bucket (see gemini_flash_council in models_config.py —
                gemini-2.0-flash is a SEPARATE Google quota pool from the
                gemini-2.5-flash bucket brain/vision/prompt-refiner all share)
  Drafter     — GPT-OSS 120B       (Groq,      free)  — code team
  Critic      — GPT-OSS 120B       (Groq,      free)  — code team
  Refiner     — GLM 4.7            (Cerebras,  free)  — code team
  Synthesizer — GLM 4.7 Flash      (Z.AI,      free)  — code team

Roster corrected 2026-07-13 (was stale — see DECISIONS.md): Critic used to
be listed as Llama 4 Scout, deprecated by Groq 2026-06-17 and long since
swapped for gpt_oss_120b_debug; Synthesizer used to be Gemma 4 31B
(gemma_4), which failed 100% of live calls this session (OpenRouter serving
it from a backend that 404s) and was swapped to the already-proven
glm_47_flash_zai. Planner moved off the brain/vision-shared Gemini bucket
onto its own. Use `FreeManagerTeam.roster_summary()` for the current live
roster rather than trusting a hardcoded string anywhere — that's exactly
how this one went stale.

For REVIEW tasks (structured JSON quality scoring), the full prose
pipeline doesn't apply — instead two members independently score and
their verdicts are merged into a single consensus JSON.

For everything else (synthesis, dispatch, or any general prompt routed
here while Claude is down — including "simple" tasks), all 5 members
take a pass, so even a one-line request gets genuine multi-model
collaboration instead of a single model's first draft.

Internal resilience: if a member fails at its stage, the Council tries
the next member in rotation for that stage, so one dead provider never
blocks the whole pipeline.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from loguru import logger

from manager.free_manager_helpers import parse_json, merge_reviews


# ── Routing: review calls get a 2-model consensus, everything else the ────────
# ── full 5-stage pipeline. ─────────────────────────────────────────────────────

_REVIEW_WORDS = [
    "review", "quality_score", "criteria_passed", "criteria_failed",
    "approve", "refine_instruction", "score this", "score the",
]


@dataclass
class Member:
    role:       str
    model_id:   str     # references MODEL_REGISTRY key
    model_name: str
    provider:   str


# Drawn from prompt/code/router teams only — never the brain team, so the
# Council doesn't cannibalize the models the Brain team needs.
# model_name/provider MUST match what the model_id actually resolves to in
# config/models_config.py — these labels are shown to users in /status.
COUNCIL: list[Member] = [
    Member(role="Planner",     model_id="gemini_flash_council", model_name="Gemini 2.0 Flash", provider="Google"),
    Member(role="Drafter",     model_id="gpt_oss_120b_coder",   model_name="GPT-OSS 120B",      provider="Groq"),
    Member(role="Critic",      model_id="gpt_oss_120b_debug",   model_name="GPT-OSS 120B",      provider="Groq"),
    Member(role="Refiner",     model_id="glm_47_cerebras",      model_name="GLM 4.7",           provider="Cerebras"),
    Member(role="Synthesizer", model_id="glm_47_flash_zai",     model_name="GLM 4.7 Flash",     provider="Z.AI"),
]


# ── Stage system prompts ───────────────────────────────────────────────────────

# Found live (2026-07-13): a "create an image of a cat wearing sunglasses"
# request correctly reached the Design team, which generated a real
# Pollinations image URL -- it was present in the text this Council's
# Drafter stage received. But the Drafter's instructions ("write a complete,
# high-quality response... produce the actual content") gave it no signal
# that a URL sitting in its input WAS the actual content; being a plain text
# model with no image-generation tool of its own, it "helpfully" invented a
# brand-new "prompt for a text-to-image model" and told the user to go paste
# it into Midjourney/DALL-E -- discarding the working link entirely. Critic/
# Refiner/Synthesizer never checked for a dropped URL either, so nothing
# caught it downstream. This rule is deliberately repeated at every stage
# that touches content (not just the final Synthesizer) because a single
# early drop can't be recovered by a later stage that never saw the URL.
_URL_PRESERVE_RULE = """If the request or any prior-stage output contains a real
generated asset URL (an http/https link, e.g. to image.pollinations.ai or
similar) -- that IS the deliverable. Copy it into your output character-for-
character. Never replace it with a text description, a "prompt you could use",
or advice to go generate the image in a different tool -- the image already
exists at that URL."""

# Shared anti-hallucination rule, added 2026-07-13 across every content-
# touching stage. This generalizes the URL-preservation rule above: the
# underlying failure mode found repeatedly this session (hallucinated "I
# can't generate images", invented "paste this into Midjourney" advice) was
# never really about URLs specifically -- it was free-tier text models
# filling a gap with a plausible-sounding fabrication instead of either
# using what's actually in front of them or admitting uncertainty. Stated
# generally here so the same failure shape doesn't need to be caught and
# patched one specific instance at a time.
_QUALITY_BAR = """Never invent or assume a capability, tool, file, URL, fact, or
limitation that isn't actually evidenced by what you were given. If the
input already contains a concrete result (a generated asset, real data, a
completed action), present THAT — don't describe a hypothetical alternative
or suggest the user do it themselves elsewhere. If you are genuinely
uncertain about a specific detail, say so plainly rather than filling the
gap with a confident-sounding guess."""

_STAGE_SYS = {
    "Planner": """You are the Planner in VibeAI's Free Manager Council — a 5-model team
standing in for Claude Sonnet 4.6, which is temporarily unavailable.

Read the request below and produce a short plan (2-5 bullet points) describing
how to answer it well: structure, key points to cover, tone, format.
Be concise — this plan guides the Drafter. Do NOT write the actual response yet.

TEAM CAPABILITIES — mention the relevant team(s) in your plan when the task needs them:
  Code team    — writes HTML, CSS, JavaScript, Python, React, APIs, etc.
  Design team  — generates AI images via FLUX (hero images, backgrounds, banners, icons)
                 outputs direct image URLs embeddable in <img src="..."> or CSS background-image
  Vision team  — analyses videos and images using AI
  Brain team   — handles complex reasoning, math, architecture decisions

For LANDING PAGE / WEBSITE / UI tasks, your plan MUST include:
  • "Call Design team first: generate hero image + background + any section images"
  • "Code team embeds Design team URLs into the HTML/CSS"
  Without design assets, the UI will look basic and unimpressive.

Before finalizing the plan, check it actually addresses every explicit
requirement in the request — a plan that quietly drops part of the ask
produces a Drafter output that does the same.
""" + _QUALITY_BAR,

    "Drafter": """You are the Drafter in VibeAI's Free Manager Council.
Using the plan provided, write a complete, high-quality response to the
original request. Produce the actual content — do not describe what you
would write, write it. Preserve any code blocks exactly as needed.
""" + _URL_PRESERVE_RULE + "\n\n" + _QUALITY_BAR,

    "Critic": """You are the Critic in VibeAI's Free Manager Council.
Critique the draft against the original request. List up to 3 concrete,
actionable improvements (clarity, correctness, completeness, tone). If the
draft is already excellent, say so explicitly and keep this brief.
If the original request/context contained a real generated asset URL and
the draft dropped it, that is issue #1 — flag it explicitly. Also flag any
claim, capability, or limitation the draft states without evidence in the
input.
""" + _QUALITY_BAR,

    "Refiner": """You are the Refiner in VibeAI's Free Manager Council.
Revise the draft, applying the critic's feedback. Output ONLY the improved
response — no commentary, no meta-discussion about the changes.
""" + _URL_PRESERVE_RULE + "\n\n" + _QUALITY_BAR,

    "Synthesizer": """You are the Synthesizer in VibeAI's Free Manager Council — the final
polish pass. Tighten language, ensure consistent tone and formatting, and
preserve all code blocks character-for-character. Output ONLY the final
response, with no preamble. Do not append any council/model signature line —
that is added afterward, not by you.
""" + _URL_PRESERVE_RULE + "\n\n" + _QUALITY_BAR,
}

_REVIEW_SYS = """You are a reviewer in VibeAI's Free Manager Council.
Claude Sonnet 4.6 is temporarily unavailable. You are one of two independent
quality gates.

Think step by step before scoring. Always return ONLY valid JSON (no markdown,
no preamble):
{
  "quality_score": 0.0-1.0,
  "criteria_passed": ["..."],
  "criteria_failed": ["..."],
  "issues": ["specific actionable issue"],
  "refine_instruction": "exact change needed — not 'try again'",
  "action": "APPROVE|REFINE|ESCALATE"
}
APPROVE >= 0.85. REFINE below. ESCALATE after 3 iterations."""


# ── Free Manager Council ───────────────────────────────────────────────────────

class FreeManagerTeam:
    """
    5-model Council replacing Claude Sonnet 4.6 as system manager.
    Activates immediately when Claude becomes unavailable.

    - REVIEW calls  → 2-model consensus scoring (Critic + Refiner roles)
    - everything else → full 5-stage collaborative pipeline
      (Planner → Drafter → Critic → Refiner → Synthesizer)
    """

    def __init__(self) -> None:
        self._active      = False
        self._call_counts = {m.role: 0 for m in COUNCIL}
        self._fail_counts = {m.role: 0 for m in COUNCIL}
        self._total_calls = 0

    @property
    def is_active(self) -> bool:
        return self._active

    def activate(self) -> None:
        if not self._active:
            self._active = True
            roster = "\n".join(
                f"║  {m.role:<11} → {m.model_name:<17} ({m.provider:<10}· free)  ║"
                for m in COUNCIL
            )
            logger.warning(
                "\n"
                "╔══════════════════════════════════════════════════════╗\n"
                "║  ⚡  FREE MANAGER COUNCIL  —  NOW ACTIVE             ║\n"
                "║  Claude Sonnet 4.6 is unavailable.                   ║\n"
                "║  5-model council collaborating on every task.       ║\n"
                "║                                                      ║\n"
                f"{roster}\n"
                "╚══════════════════════════════════════════════════════╝"
            )

    def deactivate(self) -> None:
        if self._active:
            self._active = False
            logger.info("[free_council] Claude recovered — deactivating council")

    # ── Main interface ────────────────────────────────────────────────────────

    async def generate(
        self,
        prompt:      str,
        system:      str = "",
        images:      list[str] | None = None,
        max_tokens:  int   = 1000,
        temperature: float = 0.3,
        task_kind:   str   = "",
        **kwargs: Any,
    ) -> str:
        self.activate()
        self._total_calls += 1

        # Explicit task_kind from the caller wins; keyword sniffing is only a
        # fallback for callers that don't declare it. (Keyword matching against
        # the full prompt misroutes synthesis prompts whose embedded team
        # outputs happen to mention 'review'.)
        if task_kind == "review" or (not task_kind and self._is_review(prompt, system)):
            return await self._review_consensus(prompt, max_tokens)

        return await self._collaborative_pipeline(prompt, max_tokens, temperature)

    # ── Routing ───────────────────────────────────────────────────────────────

    def _is_review(self, prompt: str, system: str) -> bool:
        # Only sniff the head of the prompt — embedded team outputs further
        # down may legitimately contain review vocabulary.
        text = (prompt[:300] + " " + system[:300]).lower()
        return any(kw in text for kw in _REVIEW_WORDS)

    # ── 5-stage collaborative pipeline (synthesis / dispatch / general) ───────

    async def _collaborative_pipeline(
        self, prompt: str, max_tokens: int, temperature: float
    ) -> str:
        # Short prompts don't need all 5 stages — draft + polish is enough
        # and cuts Council latency by ~60%.
        if len(prompt) < 600:
            draft = await self._run_stage(
                "Drafter",
                prompt=f"ORIGINAL REQUEST:\n{prompt}",
                max_tokens=max_tokens, temperature=temperature,
            )
            final = await self._run_stage(
                "Synthesizer",
                prompt=f"ORIGINAL REQUEST:\n{prompt}\n\nREFINED RESPONSE:\n{draft}",
                max_tokens=max_tokens, temperature=0.3,
            )
            return self._with_signature(final or draft)

        plan = await self._run_stage(
            "Planner",
            prompt=f"ORIGINAL REQUEST:\n{prompt}",
            max_tokens=400, temperature=0.3,
        )

        draft = await self._run_stage(
            "Drafter",
            prompt=f"ORIGINAL REQUEST:\n{prompt}\n\nPLAN:\n{plan}",
            max_tokens=max_tokens, temperature=temperature,
        )

        critique = await self._run_stage(
            "Critic",
            prompt=f"ORIGINAL REQUEST:\n{prompt}\n\nDRAFT:\n{draft}",
            max_tokens=400, temperature=0.2,
        )

        refined = await self._run_stage(
            "Refiner",
            prompt=f"ORIGINAL REQUEST:\n{prompt}\n\nDRAFT:\n{draft}\n\nCRITIC FEEDBACK:\n{critique}",
            max_tokens=max_tokens, temperature=temperature,
        )

        final = await self._run_stage(
            "Synthesizer",
            prompt=f"ORIGINAL REQUEST:\n{prompt}\n\nREFINED RESPONSE:\n{refined}",
            max_tokens=max_tokens, temperature=0.3,
        )

        return self._with_signature(final or refined or draft)

    def _with_signature(self, text: str) -> str:
        """Append the Council signature in code, not by trusting a model to
        type a hardcoded string verbatim -- that string went stale exactly
        this way before (see roster_summary docstring)."""
        return f"{text}\n\n[Free Manager Council: {self.roster_summary()}]"

    def roster_summary(self) -> str:
        """Build the human-readable roster string from the live COUNCIL
        list itself, so it can never drift out of sync with reality again.
        Found live (2026-07-13): the previous hardcoded version of this
        string (baked into the Synthesizer's own prompt, and duplicated
        again in tools/manager_fallback.py and this module's docstring)
        still said "Llama 4 Scout" for Critic and "Gemma 4 31B" for
        Synthesizer long after both were swapped to different models --
        wrong on every single response shown to users for who knows how
        long, simply because nothing forced the three copies to agree."""
        seen: list[str] = []
        for m in COUNCIL:
            if m.model_name not in seen:
                seen.append(m.model_name)
        parts = [
            f"{name} x{sum(1 for m in COUNCIL if m.model_name == name)}"
            if sum(1 for m in COUNCIL if m.model_name == name) > 1 else name
            for name in seen
        ]
        return " + ".join(parts)

    # When a stage's member dies, prefer the fastest providers as stand-ins
    # (Groq ~1s, Cerebras ~2s) — OpenRouter free endpoints can take 10-30s.
    _FALLBACK_ORDER = ["Critic", "Refiner", "Drafter", "Synthesizer", "Planner"]

    async def _run_stage(self, role: str, prompt: str, max_tokens: int, temperature: float) -> str:
        """Run one pipeline stage, falling back to other Council members on failure."""
        primary    = self._member_by_role(role)
        candidates = [primary] + sorted(
            (m for m in COUNCIL if m.role != role),
            key=lambda m: self._FALLBACK_ORDER.index(m.role),
        )
        self._call_counts[role] += 1

        for candidate in candidates:
            try:
                result = await self._call(
                    member=candidate,
                    prompt=prompt,
                    system=_STAGE_SYS[role],
                    max_tokens=max_tokens,
                    temperature=temperature,
                )
                # A 200-OK empty response is still a failure from the
                # caller's perspective -- found live (2026-07-13): Z.AI's
                # glm_47_flash_zai took 84s and returned 0 chars at the
                # Synthesizer stage. No exception was raised, so this loop
                # never rotated to the next candidate; the empty result
                # only got rescued by `final or draft` further up the call
                # chain, an accident of that particular call site rather
                # than something this loop guaranteed. Treating blank output
                # as a failure here means the NEXT candidate gets tried
                # immediately instead of silently eating the primary's full
                # latency for nothing.
                if not result or not result.strip():
                    raise ValueError("empty response")
                return result
            except Exception as exc:
                self._fail_counts[candidate.role] += 1
                logger.warning(
                    f"[free_council] {role} stage: {candidate.model_name} failed: "
                    f"{str(exc)[:60]} — trying next member"
                )

        raise RuntimeError(f"Free Manager Council: all members failed at {role} stage.")

    # ── 2-model review consensus ────────────────────────────────────────────────
    #
    # Hardened 2026-07-13: this used to ask ONLY Critic and Refiner, with no
    # substitution, unlike _run_stage's full 5-member rotation. Found live
    # repeatedly this session: whenever Cerebras (Refiner's provider) was
    # rate-limited, review consensus silently dropped from 2 verdicts to 1
    # rather than substituting a different available member -- and if
    # Groq/Cerebras both flake in the same window (already observed live,
    # documented elsewhere in this repo as a correlated-outage risk), it
    # could drop to 0 and raise outright. Each of the 2 "independent opinion"
    # slots now falls back through the same _FALLBACK_ORDER rotation
    # _run_stage uses, skipping any role already used for the other slot so
    # the two verdicts stay genuinely independent -- a single dead provider
    # no longer weakens the review, it just changes who gives the second
    # opinion.

    async def _review_consensus(self, prompt: str, max_tokens: int) -> str:
        verdicts: list[dict] = []
        used_roles: set[str] = set()   # succeeded, contributed a verdict this round
        dead_roles: set[str] = set()   # failed this round -- don't retry for the other slot

        for primary_role in ("Critic", "Refiner"):
            candidates = [primary_role] + [
                r for r in self._FALLBACK_ORDER
                if r != primary_role and r not in used_roles and r not in dead_roles
            ]
            for role in candidates:
                if role in used_roles or role in dead_roles:
                    continue
                self._call_counts[role] += 1
                member = self._member_by_role(role)
                try:
                    raw = await self._call(
                        member=member, prompt=prompt, system=_REVIEW_SYS,
                        max_tokens=max_tokens, temperature=0.1,
                    )
                    verdicts.append(parse_json(raw))
                    used_roles.add(role)
                    break
                except Exception as exc:
                    dead_roles.add(role)
                    self._fail_counts[role] += 1
                    logger.warning(
                        f"[free_council] {role} review failed: {str(exc)[:60]} "
                        "— trying next member"
                    )

        if not verdicts:
            raise RuntimeError("Free Manager Council: all members failed at review stage.")

        merged = merge_reviews(verdicts)
        return json.dumps(merged)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _member_by_role(self, role: str) -> Member:
        return next(m for m in COUNCIL if m.role == role)

    async def _call(
        self,
        member:      Member,
        prompt:      str,
        system:      str,
        max_tokens:  int,
        temperature: float,
    ) -> str:
        from models.registry import registry
        connector = registry.get(member.model_id)
        return await connector.generate(
            prompt=prompt,
            system=system,
            images=[],
            max_tokens=max_tokens,
            temperature=temperature,
        )

    # ── Status ────────────────────────────────────────────────────────────────

    def status(self) -> dict:
        return {
            "active":       self._active,
            "call_counts":  self._call_counts,
            "fail_counts":  self._fail_counts,
            "total_calls":  self._total_calls,
            "members": [
                {
                    "role":     m.role,
                    "model":    m.model_name,
                    "provider": m.provider,
                    "calls":    self._call_counts.get(m.role, 0),
                    "fails":    self._fail_counts.get(m.role, 0),
                }
                for m in COUNCIL
            ],
        }


# Singleton
free_manager_team = FreeManagerTeam()
