"""
core/intent_router.py -- decide whether a user message is CONVERSATION or an
ACTION task, so the CLI stops launching the 25-iteration coding agent for a
plain "hello".

Live-caught (2026-07-23): typing just "hello" put the system into agent mode
(`Iteration 1/25`) and it started calling design_asset on the previous
session's Nimbus task -- a greeting treated as a build request, pulling in
stale context. Root cause: cli.py routes EVERY non-command message straight
to handle_agent_task with no triage.

Two clean modes instead of one messy default:
  chat  -> a normal conversational reply (single model call, NO tools, NO
           file writes), using recent history. Greetings, thanks, small talk,
           "who are you", plain informational questions.
  agent -> the autonomous coding agent (build / create / fix / run / edit).

Design bias: when genuinely unsure, choose AGENT -- misrouting a real build
request into chat (it just talks, never builds) is worse for this product than
answering a borderline question conversationally. The deterministic core here
is pure and unit-tested; a model classifier is an optional tiebreaker for the
ambiguous middle, and it fails toward AGENT.
"""
from __future__ import annotations

import re

# ---- deterministic signals --------------------------------------------------

# Clear conversation: greetings, acknowledgements, small talk, identity Qs.
_CHAT_EXACT = {
    "hi", "hii", "hello", "helo", "hey", "yo", "hiya", "sup", "wassup",
    "thanks", "thank you", "thankyou", "thx", "ty", "cheers",
    "ok", "okay", "k", "kk", "cool", "nice", "great", "awesome", "perfect",
    "yes", "no", "yep", "nope", "yeah", "sure", "bye", "goodbye", "gn",
    "good morning", "good evening", "good night", "good afternoon",
    "how are you", "how r u", "how's it going", "hows it going",
    "who are you", "what are you", "what can you do", "what do you do",
    "help me", "test", "testing", "ping",
}
_CHAT_PREFIXES = (
    "hi ", "hey ", "hello ", "thanks ", "thank you ", "good morning",
    "good evening", "good night", "nice to", "who are you", "what are you",
    "what can you", "how are you", "how do you",
)

# Clear action: imperative build/code/run verbs. Word-boundary matched so
# "increase" doesn't match "create", etc.
_ACTION_VERBS = (
    "build", "create", "make", "write", "code", "implement", "add", "fix",
    "debug", "refactor", "run", "install", "generate", "design", "deploy",
    "scaffold", "edit", "update", "modify", "change", "delete", "remove",
    "rename", "move", "setup", "set up", "configure", "test the", "compile",
    "push", "commit", "clone", "download", "convert", "parse", "render",
)
_ACTION_RE = re.compile(r"\b(" + "|".join(re.escape(v) for v in _ACTION_VERBS) + r")\b", re.IGNORECASE)

# Plain informational questions -> chat (no tools needed to answer "what is X").
# "how" belongs here too -- live-caught (2026-07-24): "how did Rohit Sharma
# perform in his recent matches?" had no action verb/code token so fell
# through this deterministic layer entirely (returned None, ambiguous), and
# the model tiebreak itself misjudged it as AGENT. Safe to add: any real
# "how do I build/fix X" task still has an action verb, so _ACTION_RE (which
# runs BEFORE this check) already catches it and returns "agent" first --
# this only widens the net for a "how" question with no action verb at all.
#
# Imperative info-request phrasings ("provide me", "give me", "tell me",
# "share", "let me know", "find out") belong here for the same reason --
# live-caught same day, SAME session, THIRD instance of this exact gap:
# "provide me the last score of rohit sharma of his latest match" also fell
# through to the model tiebreak, which again misjudged it as AGENT, this
# time landing on the DEFAULT workspace where a stale history-compaction
# summary ("[Earlier conversation summary]: ...Nimbus landing page...", see
# cli.py::_maybe_summarize_history) still exists -- agent mode re-created
# nimbus-landing.html from it. Deliberately biased toward widening this net:
# the asymmetric cost is a wrong CHAT reply (cheap, obvious, instantly
# visible, trivially re-askable with an explicit verb) vs. a real question
# being sent into a 25-iteration file-writing agent that can re-adopt stale
# history (expensive, slow, confusing). A genuine build ask phrased this way
# ("give me a website") still needs an action verb or code token to survive
# past _ACTION_RE/_CODEY (checked BEFORE this), so this only widens the net
# for a bare info-request with no such signal at all.
#
# First-person desire phrasings ("i want ...", "i need ...", "i'd like ...")
# belong here too, broadly -- live-caught same day, FOURTH instance: "i want
# the info of the scores of last match of world cup between argentina and
# france" wasn't covered by the earlier narrow "i want to know"/"i'd like to
# know" (it has no "to know"), fell through again, and the model tiebreak
# misjudged AGENT yet again -- which this time answered with a stale RAG-
# pipelines explanation pulled from history, unrelated to the actual
# question. Widened from the narrow "...to know" variant to the general
# "i want/need/'d like" lead, covering "i want info on X", "i need details
# about X", "i'd like X", etc. in one shot rather than one exact phrase at a
# time. Considered inverting the whole default (no action verb + no code
# token -> always chat) instead of continuing to grow this allowlist, but
# that would break the intentional ambiguous case "the landing page for my
# startup about dogs and cats" (an elliptical build request with no verb at
# all, meant to stay ambiguous) -- so the allowlist approach continues,
# widened to the general pattern rather than one instance at a time.
_QUESTION_LEAD = re.compile(
    r"^\s*(what|whats|what's|who|why|when|where|which|how|is|are|can|could|"
    r"would|does|do|explain|tell me|define|provide me|give me|share|"
    r"let me know|find out|i want|i need|i'd like|i would like)\b", re.IGNORECASE)

# Math / physics / logic WORD-PROBLEMS ask you to REASON, not to touch files.
# They read as long descriptive narratives whose only "action verbs" are
# physical description ("the car will MOVE upwards", "the block was MADE to
# fall") -- which the coarse _ACTION_RE below matches, misrouting the whole
# problem into the 25-iteration coding agent. Live-caught (2026-07-24): a
# Bramah-press derivation ("derive the algebraic expression for the
# acceleration ...") routed to AGENT on the word "move" and answered "I'll
# create the Nimbus landing page". Route to CHAT when a reasoning verb pairs
# with a math/physics quantity and there is no code/file token -- so "derive a
# class from Base" (no math noun) and "solve this bug in main.py" (codey) both
# still go to the agent.
_REASON_VERB_RE = re.compile(
    r"\b(derive|derivation|prove|proof|calculate|compute|evaluate|determine)\b",
    re.IGNORECASE)
_MATHY_RE = re.compile(
    r"\b(acceleration|velocity|momentum|kinetic|potential energy|newton|joule|"
    r"algebraic|expression for|equation|formula|integral|derivative|probability|"
    r"magnitude|vector|hypotenuse|coefficient|quadratic|polynomial|matrix|"
    r"theorem|pressure|piston|mass\b|force\b|energy\b)", re.IGNORECASE)

# File / path / tech-shaped tokens that push an ambiguous message toward AGENT.
_CODEY = re.compile(r"[\w-]+\.(py|js|jsx|ts|tsx|html|css|json|md|txt|sh|yml|yaml|c|cpp|java|go|rs)\b"
                    r"|https?://|/\w+/|```", re.IGNORECASE)


def classify_intent_deterministic(message: str) -> str | None:
    """Return "chat" | "agent" | None (None = ambiguous, ask the model).
    Pure and side-effect-free -- the unit-testable core."""
    m = (message or "").strip()
    if not m:
        return "chat"                       # empty / whitespace -> nothing to do
    low = re.sub(r"\s+", " ", m.lower()).strip(" .!?")

    if low in _CHAT_EXACT or low.startswith(_CHAT_PREFIXES):
        # ...unless it ALSO carries an action verb ("hey build me a site").
        if not _ACTION_RE.search(m):
            return "chat"

    # A math/physics word-problem (reasoning verb + a quantity, no code token)
    # is a question to answer, not a build task -- beat the coarse action-verb
    # match on incidental words like "move"/"fall" further down.
    if not _CODEY.search(m) and _REASON_VERB_RE.search(m) and _MATHY_RE.search(m):
        return "chat"

    if _ACTION_RE.search(m) or _CODEY.search(m):
        return "agent"

    words = low.split()
    # Very short, no action verb, not codey -> conversational.
    if len(words) <= 3:
        return "chat"
    # A plain question with no action verb / code token -> informational chat.
    if _QUESTION_LEAD.match(m):
        return "chat"
    return None                             # ambiguous -> model tiebreak


# ── Live-info detector (chat-mode search augmentation) ───────────────────────
# A CHAT-routed question can still need information that changes after the
# model's training cutoff -- a sports score, a price, "who won", today's
# weather. handle_chat has no tool access at all (by design: it must never
# write files or run commands), so without this it can only answer from
# static training data and gives stale/wrong answers for exactly this class
# of question. Live-caught (2026-07-24): "what is rohit sharma's last score"
# routed to chat (correctly -- it's a plain question, not a build task) and
# got answered from memory alone, because chat had no way to look anything
# up. Real assistants (Claude, GPT) solve this by giving their normal answer
# path a search tool for exactly these questions -- not by routing them into
# a heavyweight agent loop. This is deliberately biased toward over-triggering
# (a false positive just costs one extra ~5-10s search call before answering;
# a false negative silently falls back to the old stale-answer behavior).
_LIVE_INFO_RE = re.compile(
    r"\b(latest|current(?:ly)?|today|tonight|right now|this week|this month|"
    r"this year|recent(?:ly)?|live|breaking|just (?:happened|announced|released)|"
    r"score|scoreline|result|standings|schedule|fixture|"
    r"stock price|share price|exchange rate|crypto|bitcoin|"
    r"weather|forecast|news|headline|election|"
    r"release date|latest version)",
    re.IGNORECASE,
)

# "who won" and "last/next X" as RIGID adjacent phrases miss natural word
# insertions -- live-caught (2026-07-27) twice the same day: "who actually
# won it though" broke on "actually"; "rohit sharma's last CRICKET match"
# broke on "cricket" sitting between "last" and "match" (needs_live_search
# returned False, so a query that had been failing all session for an
# unrelated reason -- DDG soft-blocking the topic, since fixed by replacing
# the whole backend -- STILL silently failed, for a completely different,
# previously-undetected reason: the trigger phrase itself never matched).
# Allow a few words between in both.
_WHO_WON_RE = re.compile(r"\bwho\b[\w\s]{0,20}\bwon\b", re.IGNORECASE)
_LAST_NEXT_EVENT_RE = re.compile(
    r"\b(?:last|next)\b[\w\s]{0,20}\b(?:match|game|innings|fight|race|episode|fixture)\b",
    re.IGNORECASE,
)

# Naming a specific dated event (a year + an event-shaped noun) benefits
# from live grounding REGARDLESS of whether the model thinks it's past or
# future -- live-caught same day: "tell me about the 2026 wimbledon mens
# final" has no score/result/latest keyword at all, so it never reached the
# checks above, and the model confidently claimed the event "hasn't happened
# yet" (wrong -- it had, relative to the app's actual current date; the
# model was reasoning from its OWN training-cutoff notion of "future"
# instead of checking). A model cannot reliably self-assess whether real
# time has passed a date it was trained before -- searching is cheap
# insurance even for a plainly-completed event.
_YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")
_EVENT_NOUN_RE = re.compile(
    r"\b(final|match|game|tournament|championship|championships|election|"
    r"world cup|olympics|grand prix|super bowl|world series|playoff|playoffs|"
    r"summit|conference|festival)\b",
    re.IGNORECASE,
)


def needs_live_search(message: str) -> bool:
    """True if a CHAT-routed message likely needs post-training-cutoff
    information. Pure pattern match, no network -- offline-testable."""
    m = message or ""
    if _LIVE_INFO_RE.search(m) or _WHO_WON_RE.search(m) or _LAST_NEXT_EVENT_RE.search(m):
        return True
    return bool(_YEAR_RE.search(m) and _EVENT_NOUN_RE.search(m))


_CLASSIFIER_MODEL = "llama31_8b_router"
_CLASSIFIER_SYS = (
    "Classify the user's message as CHAT or AGENT for a coding assistant. "
    "CHAT = greeting, small talk, thanks, or a question answerable in words "
    "with no file/code action. AGENT = a request to build, create, write, fix, "
    "run, edit, or otherwise DO something with code or files. When unsure, "
    "answer AGENT. Reply with exactly one word: CHAT or AGENT."
)


async def classify_intent(message: str) -> str:
    """Full classification: deterministic first, model tiebreak for the
    ambiguous middle, failing toward AGENT (never strand a real task in chat)."""
    det = classify_intent_deterministic(message)
    if det is not None:
        return det
    try:
        from models.registry import generate_resilient
        raw = await generate_resilient(
            _CLASSIFIER_MODEL, prompt=message[:500], system=_CLASSIFIER_SYS,
            max_tokens=4, temperature=0.0,
        )
        return "chat" if "CHAT" in (raw or "").upper() else "agent"
    except Exception:
        return "agent"                      # fail toward the current behavior
