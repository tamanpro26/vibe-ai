"""
core/compact_style.py — terse internal-reasoning style, adapted from the
"caveman mode" idea (Claude Code plugin): drop articles/filler/hedging from
PROSE, keep every technical detail exact, never touch code.

Why this exists
----------------
This session's whole debugging arc (Cerebras tools-schema budget bug, Groq
TPM truncation, read-loop dithering) was about context/token budget getting
eaten by things other than the actual task. A quieter contributor: internal
reasoning text -- verifier findings, leader-review verdicts, comparison-
judge strategy explanations, the coding agent's own narration -- is often
padded with filler that gets echoed back into context on every subsequent
call. Observed live (2026-07-16, same session): the coding agent's own
final_response was "I read the file `./src/main.jsx` and found its current
content is as follows: ... Now, I will use this exact content to replace
the old string ... I'll call edit_file again with the correct old_str and
new_str" -- five sentences of narration for zero technical content beyond
what a single tool call already conveys, and a direct violation of this
same prompt's own "NEVER explain what you are going to do without
immediately calling the tool" rule.

Scope (deliberately narrow)
----------------------------
Applied ONLY to internal reasoning/verdict/finding text that gets fed back
into a later prompt -- NOT to code, NOT to the manager/brain team's final
answer to an end user. A real user reading VibeAI's response should get a
normal, helpful answer; a verifier's internal "reasoning" field feeding the
next fix-cycle prompt should not carry filler that costs tokens on every
future call. See core/agent_loop.py's _AGENT_SYSTEM, core/
confidence_cascade.py's _VERIFIER_SYSTEM, teams/leadership.py's
_LEADER_SYSTEM, and core/comparison_judge.py for the call sites.
"""
from __future__ import annotations

COMPACT_STYLE_SUFFIX = """

RESPONSE STYLE (internal reasoning only — this does not apply to code you \
write or to any final answer meant for an end user): be terse in any prose \
you write. Drop articles (a/an/the), filler words (just/really/basically/ \
actually/simply), pleasantries, and hedging. Fragments are fine. Do not \
narrate what you are about to do — do it, or state the finding directly. \
Never drop or paraphrase code, file paths, exact identifiers, numbers, or \
error text — reproduce those verbatim. Do not restate context already \
given to you."""


def with_compact_style(system: str) -> str:
    """Append the terse-internal-reasoning suffix to a system prompt."""
    return system + COMPACT_STYLE_SUFFIX
