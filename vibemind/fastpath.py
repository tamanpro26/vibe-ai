"""
vibemind/fastpath.py
Two tiers that both exist to fix one measured, real problem: a plain "open
notepad" cost ~13 seconds end-to-end through the full plan_task() +
desktop-agent tool-loop pipeline (one Gemma 4 planning call over the network
at ~6s, then two Ollama tool-calling iterations at ~5s and ~2.4s -- timed
live, 2026-07-10, see DECISIONS.md) to do something that is actually just
`subprocess.Popen(["notepad.exe"])`, a sub-second operation.

  1. try_fast_path() -- deterministic regex matching, zero model calls, for
     the handful of exact phrasings enumerated below. Free and instant when
     it hits, but it's still pattern matching: it will always miss SOME
     phrasing no matter how many patterns get added (found live, repeatedly,
     2026-07-11 -- see DECISIONS.md), and every miss used to fall straight
     back to the full 13s pipeline. That's a real regression for a product
     whose whole pitch is "AI assistant" -- regex failing silently and
     dropping to a slow, sometimes WRONG pipeline (see the file-search
     incident in DECISIONS.md) looks like the AI isn't involved at all.
  2. try_ai_intent() -- for anything tier 1 misses, ONE short classification
     call to the local Ollama model (qwen2.5:3b-instruct) instead of
     Gemma 4's full multi-step planner + multi-iteration tool loop. This is
     genuine model understanding (handles arbitrary phrasing correctly,
     verified live against a real battery of rephrasings -- see
     DECISIONS.md), not another layer of patterns, and warm it runs in
     ~400-500ms -- 25-30x faster than the full pipeline, because it skips
     both the network-bound Gemma 4 planning call AND the "keep asking
     until the model stops requesting tools" iteration loop that a full
     tool-calling round trip needs even for one action.

Both tiers only ever fire on unambiguous, single-action commands; anything
genuinely multi-step or needing real judgement returns handled=False and
falls through to the full brain.py pipeline completely unchanged -- these
are a fast lane, not a replacement for planning.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field

from loguru import logger

_LAUNCH_RE = re.compile(r"^(?:open|launch|start)\s+(.+)$", re.IGNORECASE)
# "on <app>" AND "in <app>" -- users say both ("play X in youtube").
_ON_APP_RE = re.compile(r"\b(?:on|in)\s+(\w+)\s*$", re.IGNORECASE)

# "play", but also the other common verbs people actually use for "start
# some music" -- missed live (2026-07-11): "run sonu nigam's music" uses
# "run", not "play", and matched NOTHING below, so a bare verb swap alone
# was enough to fall through to the full LLM pipeline.
_PLAY_VERB = r"(?:play|run|put\s+on)"
_PLAY_VERB_RE = re.compile(rf"^{_PLAY_VERB}\s+(.+)$", re.IGNORECASE)

# "play/run/put on {a/any} random {song/track} {by/of/from} <artist> {on
# spotify}" -- distinct from a plain "play <song title>" request. Verified
# live (2026-07-10) that forwarding the WHOLE phrase as a literal search
# query ("a random song of sonu nigam") just searches that noisy sentence
# instead of actually picking a track -- this pulls the artist name out so
# play_random_by_artist() can pick a REAL random track, not fuzzy-match junk.
# "...on spotify" but ALSO "...in spotify" / "...in the spotify" -- the
# user's real phrasing this morning (2026-07-12, from the message DB) was
# "can you play any song of your taste IN spotify?" and every pattern here
# only knew "on spotify", which was one of the two gaps that let the whole
# sentence fall through to a literal search.
_SPOTIFY_TAIL = r"(?:\s+(?:on|in)\s+(?:the\s+)?spotify)?"

_RANDOM_BY_ARTIST_RE = re.compile(
    rf"^{_PLAY_VERB}\s+(?:a\s+|any\s+)?random\s+(?:song|track)\s+(?:by|of|from)\s+(.+?)"
    rf"{_SPOTIFY_TAIL}$",
    re.IGNORECASE,
)
# "play/run/put on [some] <artist>['s] {music|songs|tracks} [of your taste]
# [on spotify]" -- the much more common way people actually ask for an
# artist's music WITHOUT the word "random" in it. Missed live (2026-07-11):
# "can you run sonu nigam's music of your taste?" matched neither this nor
# the random-by-artist pattern above, fell through to the full LLM pipeline,
# which then decided to SEARCH THE LOCAL FILESYSTEM for "Sonu Nigam music
# files" (found nothing, burned all 8 tool-call iterations, returned
# nonsense) instead of just using the already-working play_random_by_artist()
# -- a functional failure, not just a slow one.
_ARTIST_MUSIC_RE = re.compile(
    rf"^{_PLAY_VERB}\s+(?:some\s+|a\s+bit\s+of\s+)?(.+?)(?:'s)?\s+(?:music|songs?|tracks?)"
    r"(?:\s+(?:of|that)\s+(?:your|the)\s+(?:taste|choice|choosing|liking))?"
    rf"{_SPOTIFY_TAIL}$",
    re.IGNORECASE,
)

_LEADING_SOME_RE = re.compile(r"^(?:some|a\s+bit\s+of|a\s+little)\s+", re.IGNORECASE)
_MULTI_STEP_MARKERS_RE = re.compile(
    r"\b(?:and\s+then|then|after\s+that|afterwards|before\s+that)\b", re.IGNORECASE
)

# "play any song" / "play something" / "play some music" -- the user left
# the choice OPEN; there is no artist or track to extract. Found live
# (2026-07-11): these were falling into the artist/track patterns below and
# being searched LITERALLY (artist="any", artist="some", query="something"),
# which reads as the assistant visibly not understanding -- the exact
# opposite of the AI-driven feel this product promises. These route to
# play_anything(), where the AI genuinely picks a specific song itself.
_GENERIC_MUSIC_RE = re.compile(
    r"^(?:me\s+)?(?:some\s+|any\s+|a\s+|an?other\s+)?"
    r"(?:random\s+|nice\s+|good\s+|great\s+|new\s+)?"
    r"(?:music|songs?|tracks?|something|anything|tunes?)"
    r"(?:\s+(?:nice|good|great|cool|random))?"
    # "of your taste/choice" -- the open-ended phrasing the user actually
    # uses (2026-07-12 message DB: "any song of your taste in spotify").
    r"(?:\s+(?:of|that|to)\s+(?:your|the)\s+(?:taste|choice|choosing|liking))?"
    rf"{_SPOTIFY_TAIL}$",
    re.IGNORECASE,
)

# Words that mean the user left the choice open / delegated it to the AI.
# If the specific patterns above didn't catch such a request, tier 1 must
# NOT fall through to a literal search of the sentence -- that's how
# 'Opened Spotify and searched for "any song of your taste in spotify"'
# shipped (2026-07-12, observed in the live message DB, on a build that
# already handled "play any song"). The dumb tier refuses; the AI tier
# (try_ai_intent) actually understands these and classifies them properly.
_OPEN_ENDED_RE = re.compile(
    r"\b(?:any|some|something|anything|whatever|random"
    r"|your\s+(?:taste|choice|choosing|liking|favou?rites?|pick)"
    r"|you\s+(?:like|want|prefer|choose|pick))\b",
    re.IGNORECASE,
)

# Guard on _ARTIST_MUSIC_RE's capture: "play a sad song" would otherwise be
# extracted as artist="a sad" and searched literally -- same visible-failure
# shape as the "any" bug. A capture starting with a determiner, or that IS a
# bare mood word, is not an artist name; return unhandled so the AI tier
# (which understands arbitrary mood phrasing) classifies it instead.
_NOT_AN_ARTIST_RE = re.compile(
    r"^(?:a|an|the|some|any)\b"
    r"|^(?:sad|happy|chill|romantic|upbeat|slow|party|relaxing|workout|energetic|calm|lo-?fi|old|new)$",
    re.IGNORECASE,
)

# Sentence boundaries, used to peel an unrelated leading clause off a
# compound message (see the fallback loop in try_fast_path). Commas count
# too -- "hey there, open chrome" / "thanks, now open spotify" never contain
# a `.!?`  at all, so a comma-blind split would never break them apart.
_CLAUSE_SPLIT_RE = re.compile(r"[.!?,]+\s+")

# Conversational wrapping that a voice/chat command routinely carries but
# that carries no meaning for THIS matcher -- "please open spotify for me"
# means exactly the same thing as "open spotify". Found live (2026-07-11):
# without stripping these, anything but the bare terse form
# ("open spotify") missed every pattern above and silently fell through to
# the full LLM pipeline -- i.e. the ~13s-round-trip bug this module exists
# to eliminate (see module docstring) would come right back for completely
# normal phrasing, which is exactly what was reported ("taking so much time
# to open just spotify" after this fast path already shipped). Stripped in a
# loop since these can stack ("hey, can you please open spotify for me?").
_LEADING_FILLERS = [
    re.compile(r"^(?:hi|hello|hey|ok(?:ay)?|yo|greetings)[,.!\s]+", re.IGNORECASE),
    re.compile(r"^(?:jarvis|vibemind)[,.!\s]+", re.IGNORECASE),
    re.compile(r"^(?:please|pls)[,.!\s]+", re.IGNORECASE),
    re.compile(r"^(?:can|could|would)\s+you\s+(?:please\s+)?", re.IGNORECASE),
    re.compile(r"^i\s+(?:want|need)\s+(?:you\s+)?to\s+", re.IGNORECASE),
    # Discourse markers left dangling after the clause-split fallback peels
    # off a leading clause ("thanks, now open spotify" -> splitting on the
    # comma alone still leaves "now open spotify").
    re.compile(r"^(?:now|then|so|alright|anyway)[,.!\s]+", re.IGNORECASE),
]
_TRAILING_FILLERS = [
    re.compile(r"[.!?]+$"),
    re.compile(r"\s+(?:please|pls|now|thanks|thank\s+you)$", re.IGNORECASE),
    re.compile(r"\s+for\s+me$", re.IGNORECASE),
]


@dataclass
class FastPathResult:
    handled: bool
    reply: str = ""
    action: str = ""
    detail: str = ""
    ok: bool = True
    sub_actions: list[dict] = field(default_factory=list)


def _strip_trailing_on_spotify(query: str) -> str:
    return re.sub(rf"{_SPOTIFY_TAIL}\s*$", "", query, flags=re.IGNORECASE).strip()


def _normalize(message: str) -> str:
    """Strip conversational wrapping so the patterns below see the same bare
    command regardless of how politely/naturally it was phrased. Loops until
    stable so stacked filler ("hey jarvis, could you please open spotify for
    me?") is fully peeled, not just the first layer."""
    text = message.strip()
    changed = True
    while changed:
        changed = False
        for pat in _LEADING_FILLERS:
            new_text = pat.sub("", text)
            if new_text != text:
                text, changed = new_text, True
        for pat in _TRAILING_FILLERS:
            new_text = pat.sub("", text).strip()
            if new_text != text:
                text, changed = new_text, True
    return text.strip()


async def try_fast_path(message: str) -> FastPathResult:
    """Returns handled=False immediately (no work done) if nothing matches."""
    result = await _match_normalized(message)
    if result.handled:
        return result

    # Compound messages ("hi. launch spotify please") bury the actual command
    # after an unrelated leading clause that _normalize()'s filler list can't
    # anticipate ("hi" is covered now, but small talk / "thanks, now open
    # spotify" / anything else never enumerated isn't). Rather than keep
    # growing that list forever, split on sentence boundaries and retry
    # against just the trailing clause(s), working backward from the end --
    # the actual command is virtually always closest to the end of a
    # compound message. Verified live (2026-07-11): before this, "hi. launch
    # spotify please" fell through entirely, costing the full ~13s LLM
    # round-trip for what should be a sub-second launch.
    clauses = [c for c in _CLAUSE_SPLIT_RE.split(message.strip()) if c.strip()]
    for start in range(len(clauses) - 1, 0, -1):
        result = await _match_normalized(" ".join(clauses[start:]))
        if result.handled:
            return result

    return FastPathResult(handled=False)


async def _match_normalized(message: str) -> FastPathResult:
    text = _normalize(message)

    # Found live (2026-07-11): "play some music and then open notepad" has
    # no comma/period for the clause logic to lean on, so it reached the
    # generic play-fallback below whole, which happily sent "music and then
    # open notepad" to Spotify as a search string and silently DROPPED the
    # "open notepad" half -- a two-step command truncated to one step with
    # no error, no indication anything was missed. A genuine multi-step
    # instruction needs the real planner, not this fast lane; bail out
    # before any pattern below gets a chance to swallow half of it.
    if _MULTI_STEP_MARKERS_RE.search(text):
        return FastPathResult(handled=False)

    play_m = _PLAY_VERB_RE.match(text)
    if play_m:
        rest = play_m.group(1).strip()

        # Open-ended request FIRST -- before the artist/track extraction
        # below gets a chance to mangle "any song" into artist="any".
        if _GENERIC_MUSIC_RE.match(rest):
            return await _play_anything("")

        random_m = _RANDOM_BY_ARTIST_RE.match(text)
        if random_m:
            artist = random_m.group(1).strip()
            if artist:
                return await _play_random_by_artist(artist)

        artist_music_m = _ARTIST_MUSIC_RE.match(text)
        if artist_music_m:
            artist = artist_music_m.group(1).strip()
            if artist and _NOT_AN_ARTIST_RE.match(artist):
                # "a sad", "the", bare mood words -- not an artist. Let the
                # AI tier classify this properly (mood hint -> play_any).
                return FastPathResult(handled=False)
            if artist:
                return await _play_random_by_artist(artist)

        m = _ON_APP_RE.search(rest.lower())
        # A bare "play x" defaults to Spotify (that's what this fast path
        # exists for); "play x on <somethingElse>" is NOT ours to handle.
        if m and m.group(1).lower() != "spotify":
            return FastPathResult(handled=False)
        # "put on some sonu nigam" without a "music"/"songs" word never
        # matches _ARTIST_MUSIC_RE above (nothing to anchor on) -- strip the
        # same leading quantifier words here too, so the search query sent
        # to Spotify is "sonu nigam", not noise like "some sonu nigam".
        query = _LEADING_SOME_RE.sub("", _strip_trailing_on_spotify(rest))
        if query:
            # Structural rule learned the hard way (2026-07-12): this
            # literal-search fallback must NEVER swallow an open-ended
            # request the patterns above failed to recognize. Searching the
            # user's own words back at them ("any song of your taste") is
            # the single most AI-discrediting failure this product has
            # shipped, twice. If the request delegates the choice, decline
            # here so the smarter AI tier classifies it instead.
            if _OPEN_ENDED_RE.search(query):
                return FastPathResult(handled=False)
            return await _play_on_spotify(query)

    m = _LAUNCH_RE.match(text)
    if m:
        target = m.group(1).strip()
        # Keep this to short app names -- a longer instruction ("open the
        # file at C:\... and rename it") needs real planning, not this path.
        if target and len(target.split()) <= 4 and "://" not in target and "\\" not in target:
            return await _launch_app(target)

    return FastPathResult(handled=False)


async def _launch_app(name: str) -> FastPathResult:
    from vibemind.profile import get_profile
    import vibemind.automation as auto
    import vibemind.system as system

    t0 = time.perf_counter()
    resolved = get_profile().resolve_app(name)
    if resolved:
        r = system.open_path(resolved)
        ok, detail = bool(r.get("ok")), (f"launched {name}" if r.get("ok") else str(r.get("error")))
    else:
        # Not in the warm cache (e.g. a generic name like "cmd") -- fall back
        # to the alias table + PATH lookup, same resolution launch_app()
        # already used before the fast path existed.
        ar = auto.launch_app(name)
        ok, detail = ar.ok, ar.detail

    elapsed_ms = (time.perf_counter() - t0) * 1000
    logger.info(f"[fastpath] launch '{name}' -> ok={ok} in {elapsed_ms:.0f}ms")
    reply = f"Launched {name}." if ok else f"Couldn't launch '{name}': {detail}"
    return FastPathResult(
        handled=True, reply=reply, action=f"Launch {name}", detail=detail, ok=ok,
        sub_actions=[{"name": "launch_app", "args": {"name": name}, "result": detail, "ok": ok}],
    )


async def _play_random_by_artist(artist: str) -> FastPathResult:
    from vibemind.spotify import play_random_by_artist

    t0 = time.perf_counter()
    result = await play_random_by_artist(artist)
    elapsed_ms = (time.perf_counter() - t0) * 1000
    logger.info(f"[fastpath] spotify random-by-artist '{artist}' -> ok={result.ok} in {elapsed_ms:.0f}ms")
    if result.ok and result.detail.startswith("now playing:"):
        reply = result.detail[0].upper() + result.detail[1:] + "."
    elif result.ok:
        reply = f'Opened Spotify and searched for "{artist}" -- pick any track to play.'
    else:
        reply = f"Couldn't open Spotify: {result.detail}"
    return FastPathResult(
        handled=True, reply=reply, action=f"Play a random track by {artist}",
        detail=result.detail, ok=result.ok,
        sub_actions=[{"name": "play_random_by_artist", "args": {"artist": artist},
                      "result": result.detail, "ok": result.ok}],
    )


async def _play_anything(hint: str) -> FastPathResult:
    from vibemind.spotify import play_anything

    t0 = time.perf_counter()
    result = await play_anything(hint)
    elapsed_ms = (time.perf_counter() - t0) * 1000
    logger.info(f"[fastpath] play-anything (hint='{hint}') -> ok={result.ok} in {elapsed_ms:.0f}ms")
    if result.ok and result.detail.startswith("my pick:"):
        # "my pick: Title — Artist (now playing: ...)" -> a reply that makes
        # the assistant's own choice visible, which is the whole point.
        choice = result.detail[len("my pick:"):].split("(")[0].strip()
        reply = f"I picked {choice} for you."
    elif result.ok:
        reply = result.detail
    else:
        reply = f"Couldn't play music: {result.detail}"
    action = f"Pick and play a song{f' ({hint})' if hint else ''}"
    return FastPathResult(
        handled=True, reply=reply, action=action, detail=result.detail, ok=result.ok,
        sub_actions=[{"name": "play_anything", "args": {"hint": hint},
                      "result": result.detail, "ok": result.ok}],
    )


async def _play_on_spotify(query: str) -> FastPathResult:
    from vibemind.spotify import play_on_spotify

    t0 = time.perf_counter()
    result = await play_on_spotify(query)
    elapsed_ms = (time.perf_counter() - t0) * 1000
    logger.info(f"[fastpath] spotify play '{query}' -> ok={result.ok} in {elapsed_ms:.0f}ms")
    if result.ok and result.detail.startswith("now playing:"):
        reply = result.detail[0].upper() + result.detail[1:] + "."
    elif result.ok:
        reply = f'Opened Spotify and searched for "{query}" -- hit play on the top result.'
    else:
        reply = f"Couldn't open Spotify: {result.detail}"
    return FastPathResult(
        handled=True, reply=reply, action=f"Play '{query}' on Spotify",
        detail=result.detail, ok=result.ok,
        sub_actions=[{"name": "play_on_spotify", "args": {"query": query}, "result": result.detail, "ok": result.ok}],
    )


# ── Tier 2: AI intent classification ────────────────────────────────────
# See module docstring. LOCAL_INTENT_MODEL matches vibemind/brain.py's
# LOCAL_AGENT_MODEL deliberately -- same model, same reason (100% local via
# Ollama, free, already required for the desktop-agent tool loop), just
# used here for a single yes/no/which classification instead of a full
# multi-iteration tool-calling session.
_INTENT_MODEL = "qwen25_3b_ollama"

_INTENT_SYSTEM = """Classify the user's message as ONE simple, single-step action, or none.

The message must contain EXACTLY ONE instruction. If it asks for more than one thing -- "do X and then do Y", "do X, then Y", any second instruction after the first, even if the first part looks like a clear action below -- respond "none". Never extract just the first part and ignore the rest.

Actions:
- launch_app: open/start exactly one application, nothing else requested. value = the app name only.
- play_artist: play/listen to a NAMED artist's music generally, not one specific song (e.g. "play sonu nigam", "put on some arijit singh", "a random song by X"), nothing else requested. value = the artist name only. A genre, mood, era, or style ("romantic bollywood", "80s rock", "lofi") is NOT an artist -- that is play_any.
- play_track: play one specific NAMED song/track/album (e.g. "play alone part 2", "play bohemian rhapsody"), nothing else requested. value = the song/track name (plus artist if given).
- play_any: play music WITHOUT naming any artist or song -- the user leaves the choice open (e.g. "play any song", "play something", "put on some music", "surprise me with a song", "play a sad song"). value = the mood/genre/era hint if one was given (e.g. "sad", "80s rock", "romantic bollywood"), else "".
- none: anything else at all -- multi-step requests (even if part of it looks simple), questions, conversation, file operations, coding, or anything not a single clear action above. Playing music happens on Spotify: if the user names a DIFFERENT app or platform for playback (youtube, soundcloud, a browser...), respond none.

Respond with ONLY compact JSON on one line, nothing else: {"action": "launch_app|play_artist|play_track|play_any|none", "value": "..."}"""

_VALID_INTENT_ACTIONS = {"launch_app", "play_artist", "play_track", "play_any"}


def _extract_json_object(text: str) -> dict:
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("no JSON object in model output")
    return json.loads(text[start:end + 1])


async def try_ai_intent(message: str) -> FastPathResult:
    """Tier 2: when try_fast_path()'s regexes miss, ask the local model to
    classify in ONE short call rather than dropping straight to the full
    plan_task() + multi-iteration tool loop (~13s). Verified live
    (2026-07-11): a warm qwen2.5:3b-instruct call classifies correctly in
    ~400-500ms, including correctly returning "none" (or malformed,
    unparseable output that the strict check below treats identically) for
    genuinely compound requests like "play some music and then open
    notepad" -- it isn't fooled into only doing half the job the way the
    regex fallback in _match_normalized() was before that bug was fixed.

    Skips instantly (no delay) if Ollama isn't reachable, so a machine
    without it installed pays nothing extra and just falls through to the
    existing pipeline exactly as before this tier existed."""
    # Cheap, free, 100%-certain pre-filter before spending a model call:
    # found live (2026-07-11) that "open notepad and then write hello world
    # in it" got misclassified as a bare launch_app, the model extracting
    # just the first half and dropping the rest -- the same silent-half-
    # completion failure mode already fixed for tier 1. Don't rely on
    # prompt wording alone to catch the obvious case when a regex already
    # catches it for free.
    if _MULTI_STEP_MARKERS_RE.search(message):
        return FastPathResult(handled=False)

    from models.connectors.ollama import is_reachable
    if not await is_reachable():
        return FastPathResult(handled=False)

    from models.registry import registry
    t0 = time.perf_counter()
    try:
        connector = registry.get(_INTENT_MODEL)
        raw = await connector.generate(
            prompt=message, system=_INTENT_SYSTEM,
            max_tokens=60, temperature=0.0, task_type="classification",
        )
        data = _extract_json_object(raw)
        action = data.get("action")
        value = str(data.get("value", "")).strip()
    except Exception as exc:
        logger.info(f"[fastpath] AI intent classification skipped: {str(exc)[:100]}")
        return FastPathResult(handled=False)
    elapsed_ms = (time.perf_counter() - t0) * 1000

    # play_any is the one action where an empty value is legitimate (it just
    # means "no mood hint given") -- every other action needs a real target.
    if action not in _VALID_INTENT_ACTIONS or (not value and action != "play_any"):
        logger.info(f"[fastpath] AI intent: no simple action ({elapsed_ms:.0f}ms)")
        return FastPathResult(handled=False)

    logger.info(f"[fastpath] AI intent: {action}('{value}') in {elapsed_ms:.0f}ms")
    if action == "launch_app":
        return await _launch_app(value)
    if action == "play_artist":
        return await _play_random_by_artist(value)
    if action == "play_any":
        # The model sometimes echoes a generic word/phrase ("any", "music",
        # "your taste") as the value instead of leaving it empty -- that's
        # not a mood hint, and passing it through would put junk in the
        # pick prompt ("Pick a your taste song.").
        junk = value.lower() in {"any", "music", "song", "songs", "something", "anything"}
        hint = "" if junk or _OPEN_ENDED_RE.fullmatch(value) or "taste" in value.lower() else value
        return await _play_anything(hint)
    return await _play_on_spotify(value)  # play_track


async def warm_up_intent_model() -> None:
    """Fire one throwaway classification call at server startup so the
    model is already loaded in Ollama's memory before a real user message
    arrives. Verified live (2026-07-11): the very FIRST call to a cold
    Ollama model took ~10.4s (model load time) vs. ~400-500ms once warm --
    without this, whichever user message happens to need tier 2 first pays
    that 10s penalty, which would look exactly like the slow-pipeline bug
    this whole module exists to eliminate. Best-effort: any failure (Ollama
    not installed, not running, ...) is swallowed, since tier 2 already
    degrades gracefully per-request via is_reachable()."""
    try:
        from models.connectors.ollama import is_reachable
        if not await is_reachable():
            return
        from models.registry import registry
        t0 = time.perf_counter()
        connector = registry.get(_INTENT_MODEL)
        await connector.generate(
            prompt="open notepad", system=_INTENT_SYSTEM,
            max_tokens=60, temperature=0.0, task_type="classification",
        )
        logger.info(f"[fastpath] intent model warmed up in {(time.perf_counter() - t0) * 1000:.0f}ms")
    except Exception as exc:
        logger.info(f"[fastpath] intent model warm-up skipped: {str(exc)[:100]}")
