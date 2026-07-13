"""
vibemind/spotify.py
Two tiers, tried in order:

  1. Real playback -- requires SPOTIFY_CLIENT_ID + SPOTIFY_CLIENT_SECRET
     (free, self-service at developer.spotify.com/dashboard, no approval
     wait). Uses the Client Credentials flow (app-only auth -- proves the
     caller holds a registered app's ID+secret, no user login needed) to
     search the catalog for the exact track, then opens that track's
     `spotify:track:<id>` URI directly. Opening a SPECIFIC track URI is
     well-established Spotify behavior that actually starts playback --
     unlike the search URI below, which only navigates to a results list.
  2. Fallback -- `spotify:search:<query>`, verified live (2026-07-10):
     confirmed via tasklist + window enumeration that this opens the app
     directly to search results within about a second. One click from
     playing, no credentials required.

Why not blind keystroke automation to "click play on the top result" for
tier 1 instead of requiring credentials? Tried it live and rejected it: it
depends on window focus succeeding (Windows' foreground-lock intermittently
refuses SetForegroundWindow from a background process -- observed directly),
and it risks colliding with whatever the user is actually doing with their
own mouse/keyboard (pyautogui's fail-safe tripped mid-test here because the
user's cursor was genuinely in use at that moment). The Web API route needs
one-time setup but is completely deterministic once configured: no window
focus, no keystrokes, no guessing at UI layout.

Client Credentials auth is NOT sufficient for Spotify's playback-control
endpoints (/v1/me/player/play needs full user OAuth + a Premium account) --
that's exactly why this opens a spotify:track: URI to start playback rather
than calling the Web API's player endpoints; the URI does the whole job
without needing a bigger, Premium-gated OAuth integration.
"""
from __future__ import annotations

import base64
import os
import platform
import random
import time
import urllib.parse

import httpx
from loguru import logger

from vibemind.automation import ActionResult

_TOKEN_URL = "https://accounts.spotify.com/api/token"
_SEARCH_URL = "https://api.spotify.com/v1/search"
_ARTIST_TOP_TRACKS_URL = "https://api.spotify.com/v1/artists/{id}/top-tracks"

_cached_token: str | None = None
_cached_token_expiry: float = 0.0


def credentials_configured() -> bool:
    return bool(os.getenv("SPOTIFY_CLIENT_ID") and os.getenv("SPOTIFY_CLIENT_SECRET"))


async def _get_access_token() -> str | None:
    global _cached_token, _cached_token_expiry
    if _cached_token and time.time() < _cached_token_expiry:
        return _cached_token

    client_id = os.getenv("SPOTIFY_CLIENT_ID", "")
    client_secret = os.getenv("SPOTIFY_CLIENT_SECRET", "")
    basic = base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.post(
                _TOKEN_URL,
                headers={"Authorization": f"Basic {basic}",
                         "Content-Type": "application/x-www-form-urlencoded"},
                data={"grant_type": "client_credentials"},
            )
        if resp.status_code != 200:
            logger.warning(f"[spotify] token request failed: {resp.status_code} {resp.text[:150]}")
            return None
        data = resp.json()
        _cached_token = data["access_token"]
        _cached_token_expiry = time.time() + data.get("expires_in", 3600) - 60
        return _cached_token
    except Exception as exc:
        logger.warning(f"[spotify] token request errored: {str(exc)[:100]}")
        return None


async def _search_top_track(query: str) -> tuple[str, str] | None:
    """Returns (track_uri, human label) for the top match, or None."""
    token = await _get_access_token()
    if not token:
        return None
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(
                _SEARCH_URL,
                headers={"Authorization": f"Bearer {token}"},
                params={"q": query, "type": "track", "limit": 1},
            )
        if resp.status_code != 200:
            logger.warning(f"[spotify] search failed: {resp.status_code} {resp.text[:150]}")
            return None
        items = resp.json().get("tracks", {}).get("items", [])
        if not items:
            return None
        track = items[0]
        artists = ", ".join(a["name"] for a in track.get("artists", []))
        label = f"{track['name']} — {artists}" if artists else track["name"]
        return track["uri"], label
    except Exception as exc:
        logger.warning(f"[spotify] search errored: {str(exc)[:100]}")
        return None


async def _search_artist_id(name: str) -> tuple[str, str] | None:
    """Returns (artist_id, canonical artist name) for the top match, or None."""
    token = await _get_access_token()
    if not token:
        return None
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(
                _SEARCH_URL,
                headers={"Authorization": f"Bearer {token}"},
                params={"q": name, "type": "artist", "limit": 1},
            )
        if resp.status_code != 200:
            logger.warning(f"[spotify] artist search failed: {resp.status_code} {resp.text[:150]}")
            return None
        items = resp.json().get("artists", {}).get("items", [])
        if not items:
            return None
        return items[0]["id"], items[0]["name"]
    except Exception as exc:
        logger.warning(f"[spotify] artist search errored: {str(exc)[:100]}")
        return None


async def _artist_top_tracks(artist_id: str) -> list[dict]:
    """Up to ~10 tracks -- Spotify's own "top tracks" endpoint, not a full
    catalog, but real per-artist data (not just a market-wide top chart)."""
    token = await _get_access_token()
    if not token:
        return []
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            resp = await client.get(
                _ARTIST_TOP_TRACKS_URL.format(id=artist_id),
                headers={"Authorization": f"Bearer {token}"},
                params={"market": "US"},
            )
        if resp.status_code != 200:
            logger.warning(f"[spotify] top-tracks failed: {resp.status_code} {resp.text[:150]}")
            return []
        return resp.json().get("tracks", [])
    except Exception as exc:
        logger.warning(f"[spotify] top-tracks errored: {str(exc)[:100]}")
        return []


async def play_random_by_artist(artist_name: str) -> ActionResult:
    """Actually picks a random track BY that artist and plays it -- distinct
    from play_on_spotify(), which plays/searches for a specific query
    verbatim. Needs credentials (Client Credentials search + the artist
    top-tracks endpoint); without them, falls back to searching the artist
    NAME alone (still much better than passing the whole "a random song of
    X" sentence to search verbatim, which is the bug this fixes -- see
    DECISIONS.md)."""
    if platform.system() != "Windows":
        return ActionResult(False, "spotify: URI launch is only implemented for Windows")

    if credentials_configured():
        artist_match = await _search_artist_id(artist_name)
        if artist_match:
            artist_id, canonical_name = artist_match
            tracks = await _artist_top_tracks(artist_id)
            if tracks:
                track = random.choice(tracks)
                try:
                    os.startfile(track["uri"])  # type: ignore[attr-defined]
                    return ActionResult(True, f"now playing: {track['name']} — {canonical_name}")
                except Exception as exc:
                    logger.warning(f"[spotify] track URI open failed: {str(exc)[:100]}")
            else:
                logger.info(f"[spotify] no top tracks for artist '{canonical_name}' -- falling back to search")
        else:
            logger.info(f"[spotify] no artist match for '{artist_name}' -- falling back to search")

    # Fallback: search the ARTIST NAME alone (not the full "random song of
    # X" sentence) -- lands on the artist's own page, one click from any
    # of their songs, instead of a noisy literal-phrase search.
    uri = f"spotify:search:{urllib.parse.quote(artist_name)}"
    try:
        os.startfile(uri)  # type: ignore[attr-defined]
        return ActionResult(True, f"opened Spotify, searched for artist '{artist_name}'")
    except Exception as exc:
        return ActionResult(False, f"spotify URI failed: {exc}")


async def play_on_spotify(query: str) -> ActionResult:
    if platform.system() != "Windows":
        return ActionResult(False, "spotify: URI launch is only implemented for Windows")

    if credentials_configured():
        match = await _search_top_track(query)
        if match:
            track_uri, label = match
            try:
                os.startfile(track_uri)  # type: ignore[attr-defined]
                return ActionResult(True, f"now playing: {label}")
            except Exception as exc:
                logger.warning(f"[spotify] track URI open failed: {str(exc)[:100]}")
                # fall through to search-only below
        else:
            logger.info(f"[spotify] no track match for '{query}' -- falling back to search")

    uri = f"spotify:search:{urllib.parse.quote(query)}"
    try:
        os.startfile(uri)  # type: ignore[attr-defined]
        return ActionResult(True, f"opened Spotify, searched for '{query}'")
    except Exception as exc:
        return ActionResult(False, f"spotify URI failed: {exc}")


# ── "Play anything" -- the AI makes the choice itself ────────────────────
# Found live (2026-07-11): "play any song" was being parsed as artist="any"
# and searched literally -- the assistant visibly NOT understanding, exactly
# the embarrassing outcome the user called out. When the user leaves the
# choice open ("any song", "something", "some music", "surprise me"), the
# genuinely-AI behavior is to MAKE a choice: pick one real, specific song
# (honoring a mood/genre hint if given) and play that, saying what it picked.

# Injected into the pick prompt when the user gave no hint, so repeated
# "play something" requests get variety instead of the model's single most
# probable answer every time. random.choice() supplies the variety; the
# model supplies the actual taste within the lane it's dealt.
_PICK_SPICE = [
    "a timeless classic", "an upbeat recent hit", "a great rock track",
    "a soulful R&B track", "a classic bollywood song", "a feel-good pop song",
    "an iconic 80s track", "a chill acoustic song", "a legendary hip-hop track",
    "an unforgettable 90s song",
]

_PICK_SYSTEM = """You pick ONE real, well-known song for the user to listen to.
Respond with ONLY compact JSON on one line, nothing else:
{"title": "<song title>", "artist": "<artist name>"}
The song must be real and popular enough to be on Spotify. The artist must be
the song's actual original performer. Never invent songs. If a mood or genre
was requested, the song must genuinely match it."""


async def _ai_pick_song(hint: str = "") -> tuple[str, str] | None:
    """One short local-model call: the AI names a real (title, artist).
    Returns None if Ollama is unavailable or output doesn't parse -- callers
    fall back rather than fail."""
    import json as _json
    from models.connectors.ollama import is_reachable
    if not await is_reachable():
        return None
    from models.registry import registry
    want = f"a {hint.strip()} song" if hint.strip() else random.choice(_PICK_SPICE)
    try:
        connector = registry.get("qwen25_3b_ollama")
        raw = await connector.generate(
            prompt=f"Pick {want}.", system=_PICK_SYSTEM,
            max_tokens=60, temperature=1.0, task_type="classification",
        )
        start, end = raw.find("{"), raw.rfind("}")
        data = _json.loads(raw[start:end + 1])
        title = str(data.get("title", "")).strip()
        artist = str(data.get("artist", "")).strip()
        if title and artist:
            return title, artist
    except Exception as exc:
        logger.info(f"[spotify] AI song pick failed: {str(exc)[:100]}")
    return None


async def play_anything(hint: str = "") -> ActionResult:
    """The user asked for music without naming a song or artist. Pick one
    and play it. Tiers: (1) local AI picks a real song, played through the
    normal exact-track path; (2) no Ollama but Spotify credentials -- pick a
    random track from Spotify's new releases (real API data, still a real
    choice); (3) neither -- a random pick from a small curated list, played
    via the search URI. Every tier ends with a SPECIFIC song, never a
    literal search for words like 'any'."""
    if platform.system() != "Windows":
        return ActionResult(False, "spotify: URI launch is only implemented for Windows")

    pick = await _ai_pick_song(hint)
    if pick:
        title, artist = pick
        result = await play_on_spotify(f"{title} {artist}")
        if result.ok:
            # Surface the choice as the assistant's own. When the real
            # Spotify API resolved the track, report ITS canonical
            # "Title — Artist" label rather than the model's own claim --
            # observed live (2026-07-11): the 3B model sometimes
            # misattributes a real song to the wrong artist ("All I Want
            # For Christmas Is You — Sarah McLachlan"), and the search
            # quietly lands on the right track anyway, so the API label is
            # the truthful one to show.
            if result.detail.startswith("now playing:"):
                label = result.detail[len("now playing:"):].strip()
                return ActionResult(True, f"my pick: {label} (now playing)")
            # Search-only fallback (no credentials): there's no API label to
            # verify the model's artist attribution against, and the 3B
            # model gets attribution wrong often enough ("Peaches — Halsey",
            # observed live) that showing its unverified claim looks bad.
            # The title alone is what the model reliably gets right, and the
            # title+artist query still lands the search in the right place.
            return ActionResult(True, f"my pick: {title} ({result.detail})")
        return result

    if credentials_configured():
        token = await _get_access_token()
        if token:
            try:
                async with httpx.AsyncClient(timeout=8.0) as client:
                    resp = await client.get(
                        "https://api.spotify.com/v1/browse/new-releases",
                        headers={"Authorization": f"Bearer {token}"},
                        params={"limit": 50},
                    )
                albums = resp.json().get("albums", {}).get("items", []) if resp.status_code == 200 else []
                if albums:
                    album = random.choice(albums)
                    artists = ", ".join(a["name"] for a in album.get("artists", []))
                    try:
                        os.startfile(album["uri"])  # type: ignore[attr-defined]
                        return ActionResult(True, f"my pick: {album['name']} — {artists} (from new releases)")
                    except Exception as exc:
                        logger.warning(f"[spotify] album URI open failed: {str(exc)[:100]}")
            except Exception as exc:
                logger.info(f"[spotify] new-releases pick failed: {str(exc)[:100]}")

    # Last resort: no AI, no API -- still land on a specific song.
    title, artist = random.choice([
        ("Bohemian Rhapsody", "Queen"), ("Blinding Lights", "The Weeknd"),
        ("Kal Ho Naa Ho", "Sonu Nigam"), ("Shape of You", "Ed Sheeran"),
        ("Tum Hi Ho", "Arijit Singh"), ("Billie Jean", "Michael Jackson"),
        ("Levitating", "Dua Lipa"), ("Channa Mereya", "Arijit Singh"),
    ])
    result = await play_on_spotify(f"{title} {artist}")
    if result.ok:
        return ActionResult(True, f"my pick: {title} — {artist} ({result.detail})")
    return result
