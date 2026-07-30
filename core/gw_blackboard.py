"""
core/gw_blackboard.py
Blackboard / Global Workspace Theory prototype -- links independent hosted
models into ONE computation via a shared, versioned state object instead of
isolated calls. Hosted models share no hidden states, gradients, or KV
caches; the only real channel between them is tokens in/tokens out. So the
"network" lives in this state object plus the rules for who fires and how
outputs merge -- same shape as a 1970s blackboard system (Hearsay-II) /
Global Workspace Theory, not a literal neural net. Design brief: user,
2026-07-17.

Related prior art already in this codebase: core/reasoning_core.py's
VibeMind (Mixture-of-Agents). Deliberately NOT reused here -- it is a
DIFFERENT mechanism and this module exists to test the difference:
  - VibeMind:  flat layers, every aggregator sees ALL proposals verbatim,
               one-shot (a single reason() call solves one problem and
               returns; no persistent state across calls).
  - Here:      a persistent Blackboard across N ticks, typed Pydantic
               activations (Hypothesis/Objection/Decision) instead of raw
               prose, a render-view attention slice per neuron (nobody
               sees the whole state -- proposers see only the task,
               the critic sees only hypotheses, the aggregator sees only
               post-veto survivors), explicit objection/veto authority,
               and a trust table that updates from real outcomes.

STANDALONE TEST OF THE IDEA -- not wired into AgentLoop or the manager.
Real live models via models/registry.py, no mocks.
"""
from __future__ import annotations

import json
import re
import asyncio
from typing import Any, Literal

from loguru import logger
from pydantic import BaseModel, Field

from models.registry import registry

# ── Typed activations ──────────────────────────────────────────────────────
# Every neuron's output is a validated struct, never raw prose passed
# straight through -- this is what makes the state mergeable/scoreable
# instead of just a longer and longer chat transcript.


class Hypothesis(BaseModel):
    id: str
    claim: str
    rationale: str
    confidence: float
    author: str


class Objection(BaseModel):
    target_id: str
    reason: str
    severity: Literal["minor", "moderate", "fatal"]
    author: str


class Decision(BaseModel):
    claim: str
    provenance: list[str]
    confidence: float
    author: str
    method: Literal["synthesized", "degraded"] = "synthesized"


class Canary(BaseModel):
    """A hypothesis with a KNOWN, seeded defect, injected alongside real
    hypotheses to test whether the critic seat is actually evaluating
    content or just reflexively returning no objections. Confirmed live
    (2026-07-17): gpt_oss_20b_free returned "[]" across dozens of coding-
    benchmark critique calls, including ones with confirmed-real bugs in
    the hypotheses -- with no canary, "no objections because the code is
    clean" and "no objections because the neuron is dead" are
    indistinguishable. This is the negative control that tells them apart."""
    claim: str
    rationale: str
    domain: str
    min_expected_severity: Literal["minor", "moderate", "fatal"] = "moderate"


class GWBlackboard(BaseModel):
    """The shared activation state. One canonical object; every neuron call
    is conditioned on the accumulated state of all previous calls."""
    task_spec:  str
    tick:       int = 0
    hypotheses: list[Hypothesis] = Field(default_factory=list)
    objections: list[Objection]  = Field(default_factory=list)
    decision:   Decision | None  = None


# ── Typed handoff / completion report (jcode Feature 4 port) ────────────────
# Premature-victory (empty-ish content passing shallow verification) is a
# documented VibeAI failure mode -- the six-instance harness-blame saga is
# exactly the class of thing a REQUIRED "what did you NOT check" field
# surfaces. The schema alone isn't the feature; the enforcement gates are.
# Concept from jcode v0.54.4 (MIT): deep-swarm handoffs mandate
# what_i_did_not_check, and completion reports may not be a bare "done".


class CompletionReport(BaseModel):
    outcome: Literal["done", "partial", "blocked", "failed"]
    changes: list[str] = Field(default_factory=list)              # files/artifacts touched
    validation_performed: list[str] = Field(default_factory=list) # checks actually RUN
    what_i_did_not_check: list[str] = Field(default_factory=list)  # REQUIRED non-empty (gate)
    blockers: list[str] = Field(default_factory=list)
    confidence: float = 0.5
    report_quality: Literal["ok", "degraded"] = "ok"             # set by the gate


class Handoff(BaseModel):
    task_spec: str                       # verbatim, never summarized
    report: CompletionReport
    provenance: list[str] = Field(default_factory=list)


def enforce_report_policy(
    raw: dict, retry_fn=None,
) -> tuple[CompletionReport, bool]:
    """Apply the completion-report gates to a raw model-produced report dict.
    Returns (report, mandatory_verify).

    Gates (jcode Feature 4):
      1. what_i_did_not_check == []  -> a model claiming it checked everything
         either misunderstood or is over-claiming. Retry ONCE (via retry_fn,
         if supplied) asking it to name at least one unverified thing; if it
         still comes back empty, tag report_quality="degraded" and force the
         verifier battery.
      2. outcome == "done" but validation_performed == []  -> "done" with no
         evidence is not "done". Tag degraded + force verify. (A real "done"
         still only REQUESTS the DoD battery -- the battery decides.)
    A degraded report always returns mandatory_verify=True."""
    report = CompletionReport(**raw)

    if not report.what_i_did_not_check and retry_fn is not None:
        retried = retry_fn(
            "Your report claimed you verified everything. List at least one "
            "specific thing you did NOT check or could not verify."
        )
        if isinstance(retried, dict):
            report = CompletionReport(**{**raw, **retried})

    degraded = (
        not report.what_i_did_not_check
        or (report.outcome == "done" and not report.validation_performed)
    )
    if degraded:
        report.report_quality = "degraded"
        logger.warning(
            f"[gw_blackboard] completion report degraded "
            f"(unchecked={report.what_i_did_not_check!r}, "
            f"validated={report.validation_performed!r}) -- forcing verifier battery"
        )
    return report, degraded


def unchecked_todos_for_next_seat(report: CompletionReport) -> str:
    """Render a report's unchecked claims as explicit TODO-verify entries for
    the NEXT model/seat's view, so unverified claims stop propagating
    silently through the pipeline."""
    if not report.what_i_did_not_check:
        return ""
    lines = ["UNVERIFIED BY THE PREVIOUS STEP -- verify these before relying on the handoff:"]
    lines += [f"  [ ] {item}" for item in report.what_i_did_not_check]
    return "\n".join(lines)


# ── Synapse table ───────────────────────────────────────────────────────────
# The only thing that "learns" here: no gradients, just trust weights per
# model, nudged by real outcomes (fatally-vetoed -> distrust a bit, survived
# into an accepted decision -> trust a bit more). Module-level and
# process-lifetime, same pattern as models/circuit_breaker.py.

_TRUST: dict[str, float] = {}


def _trust(model_id: str) -> float:
    return _TRUST.get(model_id, 1.0)


def _update_trust(model_id: str, delta: float) -> None:
    _TRUST[model_id] = max(0.1, min(3.0, _trust(model_id) + delta))


# ── Canary tracking (critic integrity, per model x domain) ────────────────
# "critic" is not a capability -- "code-critic" and "prose-critic" are, and
# the SAME model can demonstrably pass one and fail the other (confirmed
# live: gpt_oss_20b_free correctly flagged real prose-reasoning errors in
# one test, then returned zero objections across dozens of code-critique
# calls with confirmed bugs present). Tracking is keyed by (model_id,
# domain), not just model_id, for exactly that reason.

_CANARY_CHECKS: dict[tuple[str, str], int] = {}
_CANARY_MISSES: dict[tuple[str, str], int] = {}
_SEVERITY_RANK = {"minor": 1, "moderate": 2, "fatal": 3}


def _record_canary_result(model_id: str, domain: str, caught: bool) -> None:
    key = (model_id, domain)
    _CANARY_CHECKS[key] = _CANARY_CHECKS.get(key, 0) + 1
    if not caught:
        _CANARY_MISSES[key] = _CANARY_MISSES.get(key, 0) + 1
        logger.warning(
            f"[gw_blackboard] CANARY MISSED by {model_id} (domain={domain}) — "
            f"seeded defect went unflagged"
        )


def is_critic_degenerate(
    model_id: str, domain: str, threshold: float = 0.5, min_checks: int = 3,
) -> bool:
    """True once a critic seat has missed at least `threshold` fraction of
    its canaries in this domain, after at least `min_checks` real attempts
    (avoids flagging degenerate off a single unlucky miss)."""
    key = (model_id, domain)
    checks = _CANARY_CHECKS.get(key, 0)
    if checks < min_checks:
        return False
    return (_CANARY_MISSES.get(key, 0) / checks) >= threshold


def canary_stats() -> dict[str, dict]:
    """Human-readable catch-rate report per (model, domain) -- for a
    status endpoint or CEO-style oversight report, same spirit as
    teams/leadership.py's verdict log."""
    out: dict[str, dict] = {}
    for key, checks in _CANARY_CHECKS.items():
        model_id, domain = key
        misses = _CANARY_MISSES.get(key, 0)
        out[f"{model_id}:{domain}"] = {
            "checks": checks,
            "misses": misses,
            "miss_rate": round(misses / checks, 2) if checks else 0.0,
            "degenerate": is_critic_degenerate(model_id, domain),
        }
    return out


# ── The five neurons ────────────────────────────────────────────────────────
# Distinct real models, distinct real jobs -- proposer/proposer/critic/
# aggregator/controller. Picked from models already live-verified reliable
# in this project's own testing (config/models_config.py comments), spread
# across 5 different providers so no single provider outage blanks the test.

_PROPOSER_A = "llama33_70b_coder"      # groq
_PROPOSER_B = "glm_47_flash_zai"       # zai
_PROPOSERS  = [_PROPOSER_A, _PROPOSER_B]  # default narrow pool; run() can override
                                           # with a wider list for high-fan-out testing
_CRITIC     = "gpt_oss_20b_free"       # openrouter -- NVIDIA_API_KEY not set in this
                                        # environment, deepseek_v4_flash_nim's provider;
                                        # swapped to an already-verified-working free
                                        # OpenRouter model to keep this a real 5-provider
                                        # test instead of degrading to 4 live + 1 dead.
_AGGREGATOR          = "glm_47_cerebras"    # cerebras
_AGGREGATOR_FALLBACK = "gpt_oss_120b_debug" # groq -- different provider than the
                                             # primary aggregator on purpose: confirmed
                                             # live (2026-07-17) that a Cerebras 429
                                             # stalled a tick's decision for 3 straight
                                             # retries with NO fallback. The aggregator
                                             # must never be the one seat in a system
                                             # built to remove single points of failure
                                             # that IS a single point of failure.
_CRITIC_FALLBACK = "llama33_70b_coder" # groq -- reassignment target when canary
                                        # testing flags the primary critic seat
                                        # degenerate for a given domain (see
                                        # is_critic_degenerate below).
_CONTROLLER = "gpt_oss_120b_debug"     # groq (different endpoint than proposer_a)


def _critic_seat(domain: str = "general") -> str:
    """Primary critic, unless canary testing has flagged it degenerate for
    THIS domain -- reassign to the fallback seat automatically rather than
    silently keep trusting a neuron proven dead for this kind of content."""
    if is_critic_degenerate(_CRITIC, domain):
        logger.warning(
            f"[gw_blackboard] critic {_CRITIC} degenerate for domain={domain} "
            f"(missed canaries) — reassigning seat to {_CRITIC_FALLBACK}"
        )
        return _CRITIC_FALLBACK
    return _CRITIC

_PROPOSER_SYS = (
    "You are one independent proposer inside a multi-model reasoning system. "
    "You do NOT see any other model's answer -- reason from scratch, do not "
    "hedge by trying to cover every angle. "
    "Respond with ONLY one JSON object, no prose, no markdown fences, exactly: "
    '{"claim": "<your proposed answer/position, 1-3 sentences>", '
    '"rationale": "<why, 1-3 sentences>", "confidence": <float 0-1>}'
)

_CRITIC_SYS = (
    "You are a critic reviewing competing hypotheses for a technical decision. "
    "For EACH hypothesis, decide if it has a real, concrete flaw -- not a "
    "stylistic preference or \"could also consider X\". "
    "Respond with ONLY a JSON array (use [] if nothing has a real flaw), no "
    'markdown fences, each item exactly: {"target_id": "<hypothesis id>", '
    '"reason": "<concrete flaw>", "severity": "minor"|"moderate"|"fatal"}. '
    'Use "fatal" ONLY if adopting the hypothesis as-is would actually be '
    "wrong or harmful, not merely improvable."
)

_AGGREGATOR_SYS = (
    "You are the aggregator in a multi-model reasoning system. You are given "
    "surviving hypotheses (fatally-objected ones were already removed before "
    "you saw this) with a trust-weighted score per hypothesis, plus any "
    "non-fatal objections still open against them. Synthesize ONE final "
    "decision: adopt the strongest surviving hypothesis, or combine the best "
    "elements of several. Respond with ONLY one JSON object, no markdown "
    'fences: {"claim": "<the final decision, 2-4 sentences>", '
    '"confidence": <float 0-1>, "provenance": ["<hypothesis id>", ...]}'
)

_CONTROLLER_SYS = (
    "You are the termination gate in a multi-model reasoning system. Given "
    "the original task and the aggregator's proposed decision, decide if the "
    "decision genuinely and completely answers the task, or if it needs "
    "another round. Respond with ONLY one JSON object, no markdown fences: "
    '{"done": true|false, "reason": "<one sentence>"}'
)


def _safe_confidence(value: Any, default: float) -> float:
    """Clamped float parse that never raises. Confirmed live (2026-07-19):
    every OTHER field in this module degrades gracefully on a malformed
    model response (isinstance checks, str() coercion, severity fallback to
    "minor") except this one -- a bare `float(data.get("confidence", ...))`
    crashed the whole tick on a non-numeric value (e.g. "high" instead of
    0.8), which every model in this environment has shown a live tendency
    to occasionally do with SOME field this session. One malformed
    confidence value must skip/default, not take down the run."""
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return default


def _extract_json(raw: str) -> Any | None:
    """Lenient extraction: try the widest object first, then the widest
    array. Object must come first -- an object with a nested array field
    (e.g. the Decision schema's "provenance": [...]) would otherwise let a
    greedy array regex match just the inner array and silently return that
    instead of the real object (confirmed live: this exact case swallowed
    the aggregator's Decision on the first run of this module). A bare
    array-of-objects response (the critic's Objection list) correctly falls
    through to the array pattern, since \\{.*\\} greedily spanning multiple
    top-level objects with no wrapping brackets is not valid JSON and
    json.loads raises on it."""
    for pattern in (r"\{.*\}", r"\[.*\]"):
        m = re.search(pattern, raw, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                continue
    return None


async def _call_json(model_id: str, system: str, prompt: str, max_tokens: int = 500) -> Any | None:
    try:
        raw = await registry.get(model_id).generate(
            prompt=prompt, system=system, max_tokens=max_tokens,
            temperature=0.4, task_type="gw_blackboard",
        )
    except Exception as exc:
        logger.warning(f"[gw_blackboard] {model_id} call failed: {str(exc)[:80]}")
        return None
    data = _extract_json(raw)
    if data is None:
        logger.warning(f"[gw_blackboard] {model_id} returned unparseable output: {raw[:300]!r}")
    else:
        logger.debug(f"[gw_blackboard] {model_id} parsed: {data!r}")
    return data


# ── Tick phases ──────────────────────────────────────────────────────────────
# render_view is done inline per phase below: each neuron gets a purpose-
# built slice of the blackboard, never the whole thing -- e.g. the proposers
# never see each other's hypothesis, the aggregator never sees vetoed ones.

async def propose(bb: GWBlackboard, proposers: list[str] | None = None) -> None:
    """All proposers get an IDENTICAL task-only view and run in parallel --
    true independent reasoning, no cross-talk, same as a real network's
    layer-1 units not being wired to each other. `proposers` defaults to the
    narrow 2-model pool; pass a longer list for wide-fan-out testing (the
    theory's actual capability claim rests on N=8-10+ diverse samples, not
    the 2 used for the mechanism-validation runs)."""
    pool = proposers or _PROPOSERS
    view = f"TASK:\n{bb.task_spec}"
    results = await asyncio.gather(*(_call_json(m, _PROPOSER_SYS, view) for m in pool))
    for author, data in zip(pool, results):
        if not isinstance(data, dict) or not data.get("claim"):
            continue
        bb.hypotheses.append(Hypothesis(
            id=f"h{len(bb.hypotheses) + 1}",
            claim=str(data.get("claim", ""))[:500],
            rationale=str(data.get("rationale", ""))[:500],
            confidence=_safe_confidence(data.get("confidence"), 0.5),
            author=author,
        ))
    bb.tick += 1


async def critique_and_resolve(
    bb: GWBlackboard, canary: Canary | None = None, domain: str = "general",
) -> None:
    """Critic sees only live hypotheses (not the full task history). Fatal
    objections VETO -- a vetoed hypothesis never reaches the aggregator,
    full stop, not just down-weighted. Aggregator sees only survivors plus
    open non-fatal objections, each hypothesis annotated with its
    trust-weighted score (confidence x synapse trust for its author).

    `canary`, if supplied, is a hypothesis with a KNOWN seeded defect,
    injected into the critic's view alongside the real ones -- it is NEVER
    added to bb.hypotheses and its result NEVER reaches the aggregator or
    bb.objections. It exists solely to answer "is this critic seat actually
    reading content, or reflexively returning no objections" -- see Canary's
    docstring for why that distinction is otherwise unobservable."""
    if not bb.hypotheses:
        bb.tick += 1
        return

    critic_model = _critic_seat(domain)
    canary_id = "canary_0"
    hyp_block = "\n".join(
        f"- id={h.id} author={h.author} claim: {h.claim}\n  rationale: {h.rationale}"
        for h in bb.hypotheses
    )
    if canary is not None:
        hyp_block += (
            f"\n- id={canary_id} author=seeded claim: {canary.claim}\n"
            f"  rationale: {canary.rationale}"
        )
    critic_view = f"TASK:\n{bb.task_spec}\n\nHYPOTHESES:\n{hyp_block}"
    raw_objections = await _call_json(critic_model, _CRITIC_SYS, critic_view, max_tokens=600)
    # A critic with exactly one objection sometimes returns a bare object
    # instead of a 1-element array despite the system prompt -- confirmed
    # live (2026-07-18): this silently discarded a CORRECT canary catch,
    # miscounting it as a miss. Same lenient-parsing principle as
    # _extract_json's object/array handling above.
    if isinstance(raw_objections, dict):
        raw_objections = [raw_objections]

    objections: list[Objection] = []
    canary_severity: str | None = None
    if isinstance(raw_objections, list):
        for item in raw_objections:
            if not isinstance(item, dict) or not item.get("target_id"):
                continue
            severity = item.get("severity")
            if severity not in ("minor", "moderate", "fatal"):
                severity = "minor"
            target = str(item["target_id"])
            if canary is not None and target == canary_id:
                # Track the strongest severity flagged against the canary --
                # never let it become a real Objection on the real blackboard.
                if canary_severity is None or _SEVERITY_RANK[severity] > _SEVERITY_RANK[canary_severity]:
                    canary_severity = severity
                continue
            objections.append(Objection(
                target_id=target,
                reason=str(item.get("reason", ""))[:300],
                severity=severity,
                author=critic_model,
            ))
    bb.objections.extend(objections)

    if canary is not None:
        caught = (
            canary_severity is not None
            and _SEVERITY_RANK[canary_severity] >= _SEVERITY_RANK[canary.min_expected_severity]
        )
        _record_canary_result(critic_model, domain, caught)

    vetoed_ids = {o.target_id for o in objections if o.severity == "fatal"}
    for h in bb.hypotheses:
        if h.id in vetoed_ids:
            _update_trust(h.author, -0.15)

    survivors = [h for h in bb.hypotheses if h.id not in vetoed_ids]
    if not survivors:
        # Gap principle: nothing survived review -- don't force a decision
        # out of hypotheses already known to be flawed.
        bb.tick += 1
        return

    scored = "\n".join(
        f"- id={h.id} author={h.author} claim: {h.claim} "
        f"(confidence={h.confidence:.2f}, trust-weighted score={h.confidence * _trust(h.author):.2f})"
        for h in survivors
    )
    open_objs = [o for o in objections if o.target_id not in vetoed_ids]
    obj_block = "\n".join(
        f"- against {o.target_id} ({o.severity}): {o.reason}" for o in open_objs
    ) or "none"
    agg_view = (
        f"TASK:\n{bb.task_spec}\n\nSURVIVING HYPOTHESES:\n{scored}\n\n"
        f"OPEN NON-FATAL OBJECTIONS:\n{obj_block}"
    )
    data = await _call_json(_AGGREGATOR, _AGGREGATOR_SYS, agg_view, max_tokens=500)
    agg_author = _AGGREGATOR
    if data is None:
        # Primary aggregator exhausted its retries (rate limit / outage) --
        # try the fallback seat on a different provider before giving up.
        logger.warning(f"[gw_blackboard] {_AGGREGATOR} unavailable — trying aggregator fallback {_AGGREGATOR_FALLBACK}")
        data = await _call_json(_AGGREGATOR_FALLBACK, _AGGREGATOR_SYS, agg_view, max_tokens=500)
        agg_author = _AGGREGATOR_FALLBACK

    if isinstance(data, dict) and data.get("claim"):
        provenance = [p for p in data.get("provenance", []) if isinstance(p, str)]
        bb.decision = Decision(
            claim=str(data["claim"])[:800],
            provenance=provenance or [h.id for h in survivors],
            confidence=_safe_confidence(data.get("confidence"), 0.6),
            author=agg_author,
        )
    else:
        # Both aggregator seats failed. A 429 costs precision, never a
        # stall: deterministically resolve to the highest trust-weighted
        # surviving hypothesis instead of leaving the tick undecided.
        best = max(survivors, key=lambda h: h.confidence * _trust(h.author))
        logger.warning(
            f"[gw_blackboard] both aggregator seats failed — degraded merge "
            f"to highest-scored survivor {best.id} ({best.author})"
        )
        bb.decision = Decision(
            claim=best.claim,
            provenance=[best.id],
            confidence=best.confidence,
            author=best.author,
            method="degraded",
        )
    bb.tick += 1


async def check_done(bb: GWBlackboard) -> bool:
    """Termination gate -- the global objective belongs to the workspace,
    not to any one model. Controller failure fails OPEN (treated as done)
    rather than looping forever on an unreachable model."""
    if bb.decision is None:
        return False
    view = f"TASK:\n{bb.task_spec}\n\nPROPOSED DECISION:\n{bb.decision.claim}"
    data = await _call_json(_CONTROLLER, _CONTROLLER_SYS, view, max_tokens=200)
    bb.tick += 1
    if not isinstance(data, dict):
        return True
    done = bool(data.get("done", True))
    if done:
        for hid in bb.decision.provenance:
            h = next((x for x in bb.hypotheses if x.id == hid), None)
            if h:
                _update_trust(h.author, 0.1)
    return done


async def run(
    task_spec: str, max_ticks: int = 3, proposers: list[str] | None = None,
    canary: Canary | None = None, domain: str = "general",
) -> GWBlackboard:
    """Drives ticks until the controller says done or max_ticks is spent --
    recurrence falls out for free: the workspace converging over ticks IS
    the network "thinking it over." Kept wide-and-shallow (heavy parallel
    fan-out per tick, few ticks total) per the design brief. `proposers`
    overrides the default narrow 2-model pool for wide-fan-out testing.
    `canary`/`domain`: see critique_and_resolve -- pass a seeded-defect
    Canary periodically to verify the critic seat is actually reading
    content for this domain, not reflexively approving everything."""
    bb = GWBlackboard(task_spec=task_spec)
    await propose(bb, proposers=proposers)
    for _ in range(max_ticks):
        await critique_and_resolve(bb, canary=canary, domain=domain)
        if await check_done(bb):
            break
    return bb
