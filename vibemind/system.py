"""
vibemind/system.py
Real filesystem + installed-application access -- the "fully connect it with
the PC storage system and all the apps" part of the brief.

This is a LOCAL, single-user desktop assistant running on the user's own
machine, gated behind a localhost bind (+ optional bearer token). Within that
boundary it is meant to have genuine reach: browse anywhere, read/write files,
open things with their default app, and enumerate installed programs. Guards
here are about correctness and not-shooting-yourself-in-the-foot (path
normalization, size caps, never shell=True), not about sandboxing the user
away from their own computer.

Windows-first (the user's actual OS); the directory/file primitives are
cross-platform, and the OS-specific bits (drive discovery, Start Menu app
enumeration, os.startfile) degrade gracefully elsewhere.
"""
from __future__ import annotations

import os
import platform
import shutil
import subprocess
from dataclasses import dataclass, asdict
from pathlib import Path

from loguru import logger

_IS_WINDOWS = platform.system() == "Windows"
_MAX_READ_BYTES = 2_000_000   # 2 MB cap so "read a file" can't OOM on a huge blob


@dataclass
class Entry:
    name: str
    path: str
    is_dir: bool
    size: int
    modified: float


def _entry(p: Path) -> Entry | None:
    try:
        st = p.stat()
        return Entry(
            name=p.name or str(p),
            path=str(p),
            is_dir=p.is_dir(),
            size=st.st_size,
            modified=st.st_mtime,
        )
    except (OSError, PermissionError):
        return None


def list_drives() -> list[str]:
    """Available drive roots (Windows: C:\\, D:\\ ...; POSIX: just /)."""
    if not _IS_WINDOWS:
        return ["/"]
    drives = []
    for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
        root = f"{letter}:\\"
        if os.path.exists(root):
            drives.append(root)
    return drives


def home_dir() -> str:
    return str(Path.home())


def list_directory(path: str | None = None) -> dict:
    """List a directory's contents. `path=None` starts at the user's home.
    Returns {"path", "parent", "entries": [...]}; entries are dirs-first,
    then alphabetical. Unreadable children are skipped, not fatal."""
    target = Path(path).expanduser() if path else Path.home()
    try:
        target = target.resolve()
    except (OSError, RuntimeError):
        pass
    if not target.exists():
        return {"path": str(target), "parent": None, "entries": [], "error": "path does not exist"}
    if not target.is_dir():
        return {"path": str(target), "parent": str(target.parent), "entries": [],
                "error": "not a directory"}

    entries: list[Entry] = []
    try:
        for child in target.iterdir():
            e = _entry(child)
            if e is not None:
                entries.append(e)
    except PermissionError:
        return {"path": str(target), "parent": str(target.parent), "entries": [],
                "error": "permission denied"}

    entries.sort(key=lambda e: (not e.is_dir, e.name.lower()))
    parent = str(target.parent) if target.parent != target else None
    return {"path": str(target), "parent": parent, "entries": [asdict(e) for e in entries]}


def read_text_file(path: str) -> dict:
    """Read a text file (size-capped). Returns {"path", "content", "truncated"}."""
    p = Path(path).expanduser()
    if not p.exists() or not p.is_file():
        return {"path": str(p), "content": "", "error": "file not found"}
    try:
        data = p.read_bytes()
    except (OSError, PermissionError) as exc:
        return {"path": str(p), "content": "", "error": str(exc)}
    truncated = len(data) > _MAX_READ_BYTES
    text = data[:_MAX_READ_BYTES].decode("utf-8", errors="replace")
    return {"path": str(p), "content": text, "truncated": truncated}


def write_text_file(path: str, content: str) -> dict:
    """Write (create/overwrite) a text file anywhere. Creates parent dirs."""
    p = Path(path).expanduser()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return {"path": str(p), "ok": True, "bytes": len(content.encode("utf-8"))}
    except (OSError, PermissionError) as exc:
        return {"path": str(p), "ok": False, "error": str(exc)}


def open_path(path: str) -> dict:
    """Open a file or folder with the OS default handler (double-click
    equivalent). No shell string is ever constructed."""
    p = Path(path).expanduser()
    if not p.exists():
        return {"path": str(p), "ok": False, "error": "path does not exist"}
    try:
        if _IS_WINDOWS:
            os.startfile(str(p))  # type: ignore[attr-defined]
        elif platform.system() == "Darwin":
            subprocess.Popen(["open", str(p)], shell=False)
        else:
            subprocess.Popen(["xdg-open", str(p)], shell=False)
        return {"path": str(p), "ok": True}
    except Exception as exc:
        return {"path": str(p), "ok": False, "error": str(exc)}


def search_files(root: str, query: str, limit: int = 100) -> dict:
    """Case-insensitive filename substring search under `root` (bounded).
    Skips unreadable subtrees; stops at `limit` hits so a search of C:\\
    can't run forever."""
    base = Path(root).expanduser()
    if not base.exists() or not base.is_dir():
        return {"root": str(base), "matches": [], "error": "root not found"}
    needle = query.lower()
    matches: list[dict] = []
    for dirpath, dirnames, filenames in os.walk(base):
        # Skip the usual noise so a search stays responsive.
        dirnames[:] = [d for d in dirnames if d not in
                       {"node_modules", ".git", "__pycache__", "$Recycle.Bin", "Windows"}]
        for name in filenames:
            if needle in name.lower():
                full = Path(dirpath) / name
                e = _entry(full)
                if e:
                    matches.append(asdict(e))
                    if len(matches) >= limit:
                        return {"root": str(base), "matches": matches, "truncated": True}
    return {"root": str(base), "matches": matches, "truncated": False}


def _windows_installed_apps() -> list[dict]:
    """Enumerate Start Menu shortcuts (.lnk) -- the closest thing to "the
    list of installed apps" without touching the registry. Each entry is
    {name, path(.lnk)} that open_path() can launch."""
    apps: dict[str, str] = {}
    roots = [
        Path(os.environ.get("ProgramData", r"C:\ProgramData")) / "Microsoft" / "Windows" / "Start Menu" / "Programs",
        Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs",
    ]
    for root in roots:
        if not root.exists():
            continue
        for lnk in root.rglob("*.lnk"):
            name = lnk.stem
            if name and name not in apps:
                apps[name] = str(lnk)

    # Start Menu .lnk scanning misses Store/MSIX-packaged apps -- verified live
    # (2026-07-10): Spotify is installed at
    # %LOCALAPPDATA%\Microsoft\WindowsApps\Spotify.exe with NO .lnk anywhere,
    # so it was silently absent from this list despite being a real, common,
    # PATH-resolvable app. Cross-check a curated set of well-known command
    # names via PATH and merge in whatever the .lnk scan didn't already find.
    _known_cmd_names = {
        "Spotify": "spotify", "Discord": "discord", "Steam": "steam",
        "WhatsApp": "whatsapp", "Visual Studio Code": "code",
        "Google Chrome": "chrome", "Microsoft Edge": "msedge",
        "Firefox": "firefox", "Slack": "slack", "Zoom": "zoom",
    }
    have_lower = {n.lower() for n in apps}
    for display_name, cmd in _known_cmd_names.items():
        if display_name.lower() in have_lower:
            continue
        resolved = shutil.which(cmd)
        if resolved:
            apps[display_name] = resolved

    return [{"name": n, "path": p} for n, p in sorted(apps.items(), key=lambda kv: kv[0].lower())]


def list_installed_apps() -> list[dict]:
    """Best-effort list of launchable applications. Windows: Start Menu
    shortcuts. Other OSes: a small set of PATH-resolvable common binaries
    (kept honest -- we don't pretend to fully enumerate on Linux/mac here)."""
    if _IS_WINDOWS:
        return _windows_installed_apps()
    common = ["code", "firefox", "google-chrome", "chromium", "gnome-terminal", "nautilus", "gedit"]
    found = []
    for name in common:
        resolved = shutil.which(name)
        if resolved:
            found.append({"name": name, "path": resolved})
    return found


def system_info() -> dict:
    """Drives + free/total space, OS, home dir -- a compact snapshot for a
    'system' dashboard panel."""
    drives = []
    for root in list_drives():
        try:
            usage = shutil.disk_usage(root)
            drives.append({
                "root": root,
                "total": usage.total,
                "used": usage.used,
                "free": usage.free,
            })
        except OSError:
            continue
    return {
        "os": f"{platform.system()} {platform.release()}",
        "home": home_dir(),
        "drives": drives,
    }
