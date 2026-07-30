"""
core/read_loop_guard.py — guard against read-only dithering loops.

Problem this solves
--------------------
Weak fallback models (observed live, 2026-07-16: glm_47_flash_zai) re-read
the same files for 4+ iterations without ever attempting edit_file. This is
NOT the failed-edit-retry problem core/agent_loop.py already handles (see
the "_failed_edits" nudge) -- no edit is ever attempted, so that fix never
fires. Iteration budget burns to zero on pure observation.

Design constraints (why this module is shaped the way it is)
--------------------------------------------------------------
1. ISOLATION. AgentLoop.run() already juggles _no_write_iters, _repeat_count,
   _loop_warnings, _last_tool_sig, verifier cycles, and reflexion cycles in
   one stateful method -- 4 real bugs were found in that exact area in one
   session. All state for this feature lives HERE, behind two integration
   points (begin_iteration / on_mutation). The orchestrator never reads or
   writes this module's internals, and this module never touches the
   orchestrator's counters.

2. NEVER FORCE AN EDIT. A "you've read App.jsx 3 times, edit_file it NOW"
   nudge trades a visible stall for a silently-wrong rushed edit. The ladder
   escalates through DECISIONS, not edits: cache short-circuit -> soft nudge
   -> decision checkpoint -> model handoff -> named abort. The strongest
   prompt this module ever emits asks the model to COMMIT TO A PLAN or NAME
   MISSING INFORMATION -- never to produce a patch under pressure.

3. FAIL VISIBLY. If every fallback tier is exhausted, the caller aborts with
   status "stalled:read_loop" instead of silently running out the iteration
   cap (which is indistinguishable from every other kind of failure in a log).

Batch-aware by design (deviation from a per-call sketch)
----------------------------------------------------------
core/agent_loop.py's real iterations batch MULTIPLE tool calls at once --
confirmed repeatedly in live traces (e.g. 6 create_file + 1 edit_file in one
turn). A guard that classifies an iteration by whichever tool call it sees
FIRST would have its verdict depend on call order within the batch, which is
incidental, not meaningful. So the integration point is `begin_iteration()`,
called ONCE per iteration with the whole batch of tool calls, not once per
individual call.

Integration contract (agent_loop.py's run(), inside the tool-call loop)
--------------------------------------------------------------------------
    guard = ReadLoopGuard(config)                       # one instance per task

    # once per iteration, BEFORE executing this iteration's tool_calls:
    bv = guard.begin_iteration(iteration, tool_calls)

    for tc in tool_calls:
        if tc["id"] in bv.short_circuits:
            result = bv.short_circuits[tc["id"]]         # skip real read
        else:
            result = execute_tool(tc)
            if tc["name"] in config.read_tools:
                guard.record_read_result(tc["args"], result)

    if bv.inject_message:
        messages.append(user_msg(bv.inject_message))

    if bv.action is Action.ESCALATE_MODEL:
        packet = guard.build_handoff(task_spec, messages)
        return orchestrator.reassign(packet)             # next fallback tier

    if bv.action is Action.ABORT:
        return TaskResult(status="stalled:read_loop", detail=bv.reason)

    # AFTER each successful mutating tool call:
    for tc in tool_calls:
        if tc["name"] in config.mutating_tools and result_succeeded(tc):
            tag = guard.on_mutation(iteration, tc.get("args"))  # "pressure_made" | "normal"
            # "pressure_made" edits MUST go through the verifier battery.

Precedence with existing guards (core/agent_loop.py)
-------------------------------------------------------
If this guard and the legacy _no_write_iters/_repeat_count machinery would
BOTH fire on the same iteration, this guard's verdict takes precedence and
the legacy nudge is suppressed for that turn -- never inject two
contradictory corrective messages in one iteration. A mutation attempt that
fails still resets this guard's read-only streak (a real attempt is
engagement, not dithering); failed-edit RETRY handling remains
core/agent_loop.py's existing "_failed_edits" nudge's jurisdiction, deliberately
not this module's, so the two mechanisms can't fight over the same failure.

Shadow mode
-----------
GuardConfig(shadow=True): all internal state updates normally (so logged
verdicts reflect the real hypothetical ladder), but begin_iteration() always
returns a no-op verdict to the caller. Run this against real tasks first --
including a healthy run on a strong model -- and confirm the checkpoint never
fires on legitimate multi-file exploration before flipping shadow off.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any

from loguru import logger


# ── Configuration ───────────────────────────────────────────────────────────

@dataclass(frozen=True)
class GuardConfig:
    """All thresholds in one place. Tune in shadow mode against real traces."""

    # VibeAI's actual tool names (tools/agent_tools.py) -- not generic guesses.
    read_tools: frozenset[str] = frozenset({"read_file", "list_dir"})
    mutating_tools: frozenset[str] = frozenset(
        {"create_file", "edit_file", "delete_file", "move_file", "bash"}
    )

    # Level 1 — serve a stub for a repeat read of unchanged content instead
    # of re-executing it. Near-zero risk: changes the environment, not the
    # model's instructions.
    cache_short_circuit: bool = True

    # Level 2 — soft nudge on the Nth read of the same unchanged file.
    nudge_same_file_reads: int = 2

    # Level 3 — decision checkpoint after N consecutive read-only ITERATIONS
    # (gated on iterations, not per-file count, so legitimate multi-file
    # exploration doesn't trip it).
    checkpoint_readonly_iters: int = 4

    # Level 4 — escalate to the next fallback model if still read-only N
    # iterations after a checkpoint was issued.
    escalate_iters_after_checkpoint: int = 2

    # Edits landing within this many iterations of a nudge/checkpoint are
    # tagged "pressure_made" and must be verifier-routed by the caller.
    pressure_window_iters: int = 2

    # Log-only mode for threshold tuning against real traces.
    shadow: bool = False


class Action(Enum):
    PROCEED = auto()
    NUDGE = auto()
    CHECKPOINT = auto()
    ESCALATE_MODEL = auto()
    ABORT = auto()


@dataclass
class BatchVerdict:
    """Result of begin_iteration() — covers the whole tool-call batch."""
    short_circuits: dict[str, str] = field(default_factory=dict)   # tool_call id -> stub
    inject_message: str | None = None
    action: Action = Action.PROCEED
    reason: str = ""


# ── Prompts — decisions, never forced edits ─────────────────────────────────

_STUB_TEMPLATE = (
    "[UNCHANGED — read served from cache]\n"
    "File: {path}\n"
    "This file is identical (content hash match) to when you read it at "
    "iteration {first_iter}. Its full contents are already in your context. "
    "Re-reading it provides no new information."
)

_NUDGE_TEMPLATE = (
    "Observation: you have now requested `{path}` {count} times without it "
    "changing. You already have everything this file can tell you. Use the "
    "information you have gathered to make progress on the task."
)

# When the model's ORIGINAL read was compacted out of context (Feature 3 /
# jcode agentgrep), stubbing "already in your context" would be a lie -- it
# genuinely lost the content. Re-serve the REAL cached content (no provider
# round-trip, no rate-limit hit) framed honestly. This is legitimate state
# recovery, NOT dithering, so it does not grow the read-only streak.
_RESERVE_TEMPLATE = (
    "[RE-SERVED FROM CACHE — your earlier read of {path} (iteration {first_iter}) "
    "was compacted out of context, so here it is again]\n{content}"
)

# Two legitimate exits — plan-to-edit OR name-missing-information — so a
# model that genuinely lacks knowledge has an honest path that isn't
# "produce a patch under duress".
_CHECKPOINT_TEMPLATE = (
    "DECISION CHECKPOINT — respond to this before any other tool call.\n"
    "You have spent {n} consecutive iterations reading without making any "
    "change. Re-reading files you have already read is no longer productive.\n"
    "Reply with exactly one of:\n"
    "  (A) PLAN: the specific change you will make next — target file, "
    "location, and a one-sentence description of the edit and why it "
    "solves the task. Then perform that edit on your next action.\n"
    "  (B) BLOCKED: the specific information you still need, which file or "
    "source you have NOT yet read would provide it, and why.\n"
    "Do not rush. A careful (B) is a better answer than a careless (A)."
)


# ── Level 1: content-hash read cache (environment fix, not a nudge) ─────────

@dataclass
class _CacheEntry:
    content_hash: str
    first_iter:   int
    count:        int
    content:      str = ""        # stored so a compacted-away read can be RE-SERVED
    msg_index:    int = 0         # message position of the read (0 until the caller threads it)
    mtime:        float = 0.0     # file mtime at record time (external-writer detection)

    # Back-compat: old code/tests indexed the entry as a tuple (hash, first, count).
    def __getitem__(self, i: int):
        return (self.content_hash, self.first_iter, self.count)[i]


class ReadResultCache:
    """Short-circuits repeat reads of unchanged content.

    Also the quiet context-bloat fix: repeated full-file dumps are what
    blow up the prompt and force the aggressive truncation seen live on
    Cerebras/Groq -- fewer redundant dumps means truncation has to drop
    less real history per call.

    Feature 3 (jcode agentgrep port): entries now carry the real content, the
    read's message index, and the file mtime -- so a read that was compacted
    out of context can be RE-SERVED honestly instead of stubbed with a false
    "already in your context", and an external writer touching the file
    invalidates the stub.
    """

    def __init__(self) -> None:
        self._entries: dict[str, _CacheEntry] = {}

    @staticmethod
    def _canon(args: dict[str, Any]) -> str:
        path = args.get("path") or args.get("file_path") or args.get("file")
        if path:
            return str(path).strip()
        return json.dumps(args, sort_keys=True, default=str)

    @staticmethod
    def _hash(content: str) -> str:
        return hashlib.sha256(content.encode("utf-8", "replace")).hexdigest()

    @staticmethod
    def _file_mtime(key: str) -> float:
        try:
            return os.path.getmtime(key)
        except OSError:
            return 0.0

    def record(self, args: dict[str, Any], content: str, iteration: int, msg_index: int = 0) -> None:
        key = self._canon(args)
        h = self._hash(content)
        prev = self._entries.get(key)
        mtime = self._file_mtime(key)
        if prev and prev.content_hash == h:
            prev.count += 1
            prev.content = content
            prev.mtime = mtime
            if msg_index:
                prev.msg_index = msg_index
        else:
            # New key OR content genuinely changed (external touch) -- reset lineage.
            self._entries[key] = _CacheEntry(h, iteration, 1, content, msg_index, mtime)

    def lookup(self, args: dict[str, Any]) -> _CacheEntry | None:
        return self._entries.get(self._canon(args))

    def changed_externally(self, args: dict[str, Any]) -> bool:
        """True if the file on disk is newer than when we cached it -- an
        external process (or the user) wrote it, so any stub would be stale.
        Closes the hole where the guard assumed only VibeAI's own tools mutate
        files. ~zero cost (one stat)."""
        key = self._canon(args)
        entry = self._entries.get(key)
        if entry is None or entry.mtime == 0.0:
            return False
        current = self._file_mtime(key)
        return current > 0.0 and current > entry.mtime

    def invalidate(self, args: dict[str, Any]) -> None:
        """Drop any cached entry for this path. Called on a successful
        create_file/edit_file/delete_file/move_file so a later read_file on
        the SAME path can never be served a stale "unchanged" stub for
        content the guard's own tools just changed."""
        self._entries.pop(self._canon(args), None)

    def bump_and_stub(self, args: dict[str, Any]) -> tuple[str, int]:
        key = self._canon(args)
        e = self._entries[key]
        e.count += 1
        return _STUB_TEMPLATE.format(path=key, first_iter=e.first_iter), e.count

    def reserve_real(self, args: dict[str, Any]) -> str:
        """Re-serve the REAL cached content, honestly framed, for a read whose
        original was compacted away. No provider round-trip."""
        key = self._canon(args)
        e = self._entries[key]
        return _RESERVE_TEMPLATE.format(path=key, first_iter=e.first_iter, content=e.content)


# ── Levels 2-5: the detector / escalation ladder ─────────────────────────────

class ReadLoopGuard:
    """One instance per task. See module docstring for the integration contract."""

    def __init__(self, config: GuardConfig | None = None) -> None:
        self.cfg = config or GuardConfig()
        self.cache = ReadResultCache()
        self._consecutive_readonly_iters = 0
        self._checkpoint_issued_at: int | None = None
        self._last_nudge_iteration: int | None = None
        self._started = time.monotonic()

    def begin_iteration(
        self, iteration: int, tool_calls: list[dict], *, compaction_cutoff: int = 0,
    ) -> BatchVerdict:
        """Call ONCE per iteration, before executing this iteration's tool calls.

        `compaction_cutoff` (Feature 3): message index below which content has
        been compacted out of context (= CompactionState.covers_up_to_turn).
        A cached read whose msg_index < cutoff is RE-SERVED with real content
        instead of stubbed, since the model genuinely lost it. Default 0
        preserves exact prior behaviour until the agent loop threads the
        cutoff through (Feature 2 live wiring)."""
        names = [tc.get("name", "") for tc in tool_calls]
        has_mutation = any(n in self.cfg.mutating_tools for n in names)
        has_read     = any(n in self.cfg.read_tools for n in names)

        short_circuits: dict[str, str] = {}
        nudge_msg: str | None = None
        did_reserve = False
        if self.cfg.cache_short_circuit:
            for tc in tool_calls:
                if tc.get("name") not in self.cfg.read_tools:
                    continue
                args = tc.get("args", {})
                entry = self.cache.lookup(args)
                if entry is None:
                    continue
                # External writer touched the file -> stub would be stale; let
                # the real read happen (don't short-circuit).
                if self.cache.changed_externally(args):
                    self.cache.invalidate(args)
                    continue
                # Original read compacted out of context -> re-serve REAL content.
                if compaction_cutoff > 0 and entry.msg_index and entry.msg_index < compaction_cutoff:
                    short_circuits[tc["id"]] = self.cache.reserve_real(args)
                    did_reserve = True
                    continue
                stub, count = self.cache.bump_and_stub(args)
                short_circuits[tc["id"]] = stub
                if count >= self.cfg.nudge_same_file_reads and nudge_msg is None:
                    self._last_nudge_iteration = iteration
                    nudge_msg = _NUDGE_TEMPLATE.format(
                        path=ReadResultCache._canon(args), count=count)

        # A batch containing any mutation attempt is engagement, not
        # dithering, even if it also contains reads (e.g. read-then-fix in
        # the same turn) -- don't grow the read-only streak for it. A re-serve
        # is legitimate state recovery, also not dithering.
        if has_read and not has_mutation and not did_reserve:
            self._consecutive_readonly_iters += 1

        verdict = self._ladder_verdict(iteration, has_mutation, short_circuits, nudge_msg)

        if self.cfg.shadow:
            if verdict.action is not Action.PROCEED or verdict.short_circuits:
                logger.warning(
                    f"[read_loop_guard][SHADOW] would fire: {verdict.action.name} "
                    f"| {verdict.reason}"
                )
            return BatchVerdict(action=Action.PROCEED)
        return verdict

    def record_read_result(
        self, args: dict[str, Any], content: str, iteration: int, msg_index: int = 0,
    ) -> None:
        """Call AFTER a real (non-short-circuited) read succeeds. `msg_index`
        (optional) is the message position of this read -- pass len(messages)
        so Feature 3's compaction-cutoff re-serve can tell whether it was
        later summarized away."""
        self.cache.record(args, content, iteration, msg_index=msg_index)

    def on_mutation(self, iteration: int, args: dict[str, Any] | None = None) -> str:
        """Call AFTER a successful mutating tool call.

        `args` (the mutating call's own arguments, e.g. {"path": "App.jsx"})
        invalidates any cached read for that path -- otherwise a later
        read_file on the SAME path could be served a stale "unchanged" stub
        for content this very call just changed. Residual risk noted by
        design: this only covers paths the guard's OWN mutating tools
        touched directly (create_file/edit_file/delete_file/move_file all
        carry a clear path); `bash` can write arbitrary files with no
        reliable path to extract, so a bash-driven external write to a
        cached file is not caught here -- record_read_result's hash check
        still catches it on the NEXT real read of that path, this just means
        one extra stub could be served in between for that specific gap.

        Returns "pressure_made" if the edit landed within the pressure
        window after a nudge/checkpoint -- the caller MUST route those
        through the verifier battery rather than trusting them silently.
        """
        self._consecutive_readonly_iters = 0
        if args:
            self.cache.invalidate(args)
        pressured_since = self._checkpoint_issued_at or self._last_nudge_iteration
        self._checkpoint_issued_at = None
        if (pressured_since is not None
                and iteration - pressured_since <= self.cfg.pressure_window_iters):
            return "pressure_made"
        return "normal"

    def build_handoff(self, task_spec: str, messages: list[Any]) -> dict[str, Any]:
        """Compact packet for reassigning the task to the next fallback model."""
        files = {k: {"first_read_iter": v[1], "reads": v[2]}
                 for k, v in self.cache._entries.items()}
        return {
            "reason": "read_loop:model_capability_exhausted",
            "task_spec": task_spec,   # verbatim -- never summarized away
            "files_already_read": files,
            "elapsed_s": round(time.monotonic() - self._started, 1),
            "note": ("Previous model looped on reads without editing. File "
                     "contents are trustworthy as of the read iterations "
                     "listed; re-verify only files it may have misread."),
        }

    # -- internals ------------------------------------------------------------

    def _ladder_verdict(
        self, iteration: int, has_mutation: bool,
        short_circuits: dict[str, str], nudge_msg: str | None,
    ) -> BatchVerdict:
        cfg = self.cfg

        # Level 4/5 — checkpoint already issued, model went right back to
        # reading instead of answering it.
        if (self._checkpoint_issued_at is not None and not has_mutation
                and iteration - self._checkpoint_issued_at
                    >= cfg.escalate_iters_after_checkpoint):
            return BatchVerdict(
                short_circuits, action=Action.ESCALATE_MODEL,
                reason=(f"still read-only {iteration - self._checkpoint_issued_at} "
                        f"iters after decision checkpoint — capability exhausted"),
            )

        # Level 3 — sustained read-only streak: force a DECISION, not an edit.
        if (not has_mutation and self._checkpoint_issued_at is None
                and self._consecutive_readonly_iters >= cfg.checkpoint_readonly_iters):
            self._checkpoint_issued_at = iteration
            return BatchVerdict(
                short_circuits, action=Action.CHECKPOINT,
                inject_message=_CHECKPOINT_TEMPLATE.format(
                    n=self._consecutive_readonly_iters),
                reason=f"{self._consecutive_readonly_iters} consecutive read-only iterations",
            )

        if nudge_msg:
            return BatchVerdict(
                short_circuits, action=Action.NUDGE, inject_message=nudge_msg,
                reason="repeat read of unchanged file",
            )

        return BatchVerdict(short_circuits, action=Action.PROCEED)
