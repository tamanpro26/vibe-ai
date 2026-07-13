"""
vibemind/profile.py
The "get comfortable with the machine before working" step the user asked
for: a system profile built ONCE (at backend startup) and cached in memory,
rather than re-discovered from scratch on every single command.

Why this matters for latency, not just convenience: vibemind/system.py's
list_installed_apps() walks the Start Menu's .lnk tree AND probes a dozen
PATH names every time it's called. That's cheap once, but the real cost this
module attacks is different -- see vibemind/fastpath.py, which needs an
already-resolved "name -> launch path" map available INSTANTLY so it can
launch an app without waiting on anything, LLM or filesystem.

Fail-soft by design: if building the profile throws for any reason, the
assistant should still start and work (just without the warm cache) rather
than refuse to boot.
"""
from __future__ import annotations

import re
import time

from loguru import logger

_profile: "SystemProfile | None" = None


class SystemProfile:
    def __init__(self) -> None:
        from vibemind import system
        t0 = time.perf_counter()
        self.built_at = time.time()
        try:
            self.apps = system.list_installed_apps()
        except Exception as exc:
            logger.warning(f"[vibemind-profile] app discovery failed: {str(exc)[:80]}")
            self.apps = []
        try:
            info = system.system_info()
            self.os = info["os"]
            self.home = info["home"]
            self.drives = info["drives"]
        except Exception as exc:
            logger.warning(f"[vibemind-profile] system_info failed: {str(exc)[:80]}")
            self.os, self.home, self.drives = "unknown", system.home_dir(), []

        # name (lowercased) -> path, for instant fast-path lookups.
        self.app_by_name: dict[str, str] = {a["name"].lower(): a["path"] for a in self.apps}
        self.build_ms = (time.perf_counter() - t0) * 1000
        logger.info(
            f"[vibemind-profile] ready in {self.build_ms:.0f}ms — "
            f"{len(self.apps)} apps, {len(self.drives)} drive(s), home={self.home}"
        )

    def resolve_app(self, name: str) -> str | None:
        """Exact, then prefix, then substring match against the cached app
        list -- same precedence as brain.py's _match_installed_app, kept here
        too so the fast path doesn't need brain.py at all for this lookup."""
        low = name.strip().lower()
        if low in self.app_by_name:
            return self.app_by_name[low]
        for n, p in self.app_by_name.items():
            if n.startswith(low):
                return p
        for n, p in self.app_by_name.items():
            if low in n:
                return p
        # Reverse direction: the target has EXTRA words wrapped around the
        # real app name ("open the spotify app", "open spotify desktop") --
        # the three checks above only fire when `low` is a prefix/substring
        # of a LONGER cached name (e.g. "code" -> "Visual Studio Code"),
        # never the other way round, so a phrase like "spotify app" matched
        # nothing before this. Word-boundary matched (not a bare substring)
        # so a short name like "code" can't accidentally match inside an
        # unrelated word.
        for n, p in self.app_by_name.items():
            if re.search(rf"\b{re.escape(n)}\b", low):
                return p
        return None


def get_profile() -> SystemProfile:
    global _profile
    if _profile is None:
        _profile = SystemProfile()
    return _profile


def refresh_profile() -> SystemProfile:
    global _profile
    _profile = SystemProfile()
    return _profile
