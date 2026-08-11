"""
core/skills.py
Curated, task-triggered guidance modules ("skills") injected into the agent's
context when a task matches. Each skill encodes a REAL, previously-shipped
defect from this project's own live testing (see DECISIONS.md) as a concrete
bad -> good example — the exact bugs an agent working on this kind of task
has actually produced before, not generic textbook advice.

Deliberately small and curated rather than exhaustive: a skill only earns a
place here once it's caused a real, verified defect. Add a skill by
appending to SKILLS; there is no registration step elsewhere.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class SkillExample:
    bad:  str
    good: str


@dataclass(frozen=True)
class Skill:
    name:     str
    triggers: tuple[str, ...]   # regex patterns (case-insensitive); >=1 match activates
    guidance: str               # the rules, with WHY each one exists
    examples: tuple[SkillExample, ...] = ()


def _render(skill: Skill) -> str:
    lines = [f"### Skill: {skill.name}", skill.guidance.strip()]
    if skill.examples:
        lines.append("Examples:")
        for ex in skill.examples:
            lines.append(f"- BAD:  {ex.bad}")
            lines.append(f"  GOOD: {ex.good}")
    return "\n".join(lines)


SKILLS: list[Skill] = [
    Skill(
        name="react-frontend-discipline",
        triggers=(r"\breact\b", r"\bjsx\b", r"\bvite\b", r"\bfrontend\b",
                  r"\bcomponent\b", r"\blanding page\b", r"\bwebsite\b", r"\bui\b"),
        guidance=(
            "Every className used in JSX MUST have a matching rule in a stylesheet before "
            "you finish — an orphaned className renders as unstyled browser-default markup, "
            "and this is the single most common defect this project has shipped. When "
            "generating a decorative/background AI image, NEVER put text, a wordmark, or a "
            "logo in the generation prompt — text-to-image models cannot reliably render "
            "legible text and will return garbled letters; put real branding as HTML text "
            "overlaid on top of the image instead. When fixing a reported styling bug in an "
            "EXISTING component, prefer edit_file on the specific broken rule over create_file "
            "rewriting the whole component — a full rewrite lets the JSX and CSS class names "
            "drift apart again, often reintroducing a new version of the same bug. No "
            "placeholder text ('Feature 1', 'Lorem ipsum', 'This is the hero section') ever "
            "ships in a final answer."
        ),
        examples=(
            SkillExample(
                bad='className="feature-card" written in JSX with no .feature-card rule anywhere in the stylesheet',
                good="add .feature-card { ... } to the stylesheet in the SAME edit that introduces the className",
            ),
            SkillExample(
                bad='design_asset(description="hero banner with the word MERIDIAN in bold letters")',
                good='design_asset(description="abstract dark gradient hero background, no text or logos") + real "Meridian" as an <h1> overlay',
            ),
        ),
    ),
    Skill(
        name="python-backend-correctness",
        triggers=(r"\bapi\b", r"\bfastapi\b", r"\bbackend\b", r"\bendpoint\b",
                  r"\bdatabase\b", r"\bsqlite\b", r"\bdict(?:ionary)?\b"),
        guidance=(
            "Use dict.get(key, default) instead of dict[key] whenever the key might not "
            "already exist — indexing a missing key raises KeyError. Iterating a dict directly "
            "(`for k in d`) yields KEYS ONLY, never (key, value) pairs — use d.items() or "
            "d.values() explicitly, or you will get a TypeError or ValueError from mis-"
            "unpacking. A simple email regex commonly misses real-world edge cases (e.g. "
            "consecutive dots in the local part) — test against them explicitly, not just the "
            "obvious valid/invalid examples. Never rely on a coarse-grained timestamp column "
            "(e.g. SQLite's CURRENT_TIMESTAMP, 1-second resolution) to order rows inserted in "
            "quick succession — two inserts within the same second get an identical timestamp "
            "and an arbitrary result order; order by the auto-increment id instead, which is "
            "always monotonic regardless of timing."
        ),
        examples=(
            SkillExample(
                bad="self.items[name] = self.items[name] + qty",
                good="self.items[name] = self.items.get(name, 0) + qty",
            ),
            SkillExample(
                bad="total = 0\nfor qty in self.items:\n    total += qty",
                good="total = 0\nfor qty in self.items.values():\n    total += qty",
            ),
            SkillExample(
                bad="ORDER BY created_at DESC   -- ties silently break insertion order",
                good="ORDER BY id DESC   -- monotonic regardless of timestamp resolution",
            ),
        ),
    ),
    Skill(
        name="debugging-methodology",
        triggers=(r"\bdebug\b", r"\bfix\b", r"\bbug\b", r"\bbroken\b", r"\berror\b",
                  r"\bcrash(?:es|ed)?\b", r"doesn.?t work"),
        guidance=(
            "Reproduce the reported error FIRST — read the relevant file(s) and confirm you "
            "can see exactly why it happens — before proposing any fix. Prefer edit_file (a "
            "surgical change to the specific broken line/rule) over create_file (rewriting the "
            "whole file) when fixing something inside an otherwise-working file: a full rewrite "
            "risks reintroducing the same class of bug in a new shape, or discarding unrelated "
            "content that was already correct. After making the fix, re-run the exact command "
            "that originally failed (the build, the test suite, the reported repro step) to "
            "confirm it actually resolves — do not assume a plausible-looking change worked."
        ),
    ),
]


def select_skills(task: str, max_skills: int = 2) -> str:
    """
    Returns formatted skill guidance text for the skills whose trigger
    patterns match this task, capped at max_skills so an unrelated task
    doesn't pay context budget for irrelevant guidance. Empty string when
    nothing matches — callers should skip injecting the section entirely
    in that case.
    """
    matched = [s for s in SKILLS if any(re.search(p, task, re.IGNORECASE) for p in s.triggers)]
    if not matched:
        return ""
    rendered = "\n\n".join(_render(s) for s in matched[:max_skills])
    return f"\n\nRELEVANT SKILLS (hard-won lessons from this project's own live testing):\n{rendered}\n"


def combine_skill_context(legacy_context: str, capability_context: str, max_chars: int = 16_000) -> str:
    """Combine static project lessons with the separately policy-bounded Hub context."""
    return (legacy_context + capability_context)[:max_chars]
