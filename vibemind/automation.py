"""
vibemind/automation.py
Real desktop control -- the capability the Manus-scaffolded jarvis-ai-assistant
never actually had (its "app-agent" only asked an LLM to narrate fictional log
lines like "launch_app: vscode"; nothing ever touched the OS). This module
does the real thing: launch processes, find/focus real windows, send real
keystrokes.

Windows-first (the user's actual machine) via pygetwindow + pyautogui.
Clipboard-paste is used for typing instead of pyautogui.write() char-by-char --
verified live that write() mishandles some punctuation, paste does not, and
it's also far faster for longer text.

Safety: launch_app() never uses shell=True (no shell-injection surface) and
resolves through a curated alias table first; unrecognized names are
attempted as a direct executable name via PATH lookup (still argv-list form,
never a shell string).
"""
from __future__ import annotations

import shutil
import subprocess
import time
import webbrowser
from dataclasses import dataclass

import pyautogui
import pygetwindow as gw
import pyperclip
from loguru import logger

pyautogui.FAILSAFE = True   # moving mouse to a screen corner aborts -- safety net
pyautogui.PAUSE = 0.05

# Common app name -> real launch command. Extend freely; this is a convenience
# table, not a hard allowlist -- launch_app() falls through to PATH lookup for
# anything not listed here (see module docstring on why that's still safe).
_APP_ALIASES: dict[str, list[str]] = {
    "vscode":       ["code"],
    "vs code":      ["code"],
    "visual studio code": ["code"],
    "notepad":      ["notepad.exe"],
    "terminal":     ["wt.exe"],
    "windows terminal": ["wt.exe"],
    "cmd":          ["cmd.exe"],
    "command prompt": ["cmd.exe"],
    "powershell":   ["powershell.exe"],
    "explorer":     ["explorer.exe"],
    "file explorer": ["explorer.exe"],
    "chrome":       ["chrome.exe"],
    "firefox":      ["firefox.exe"],
    "edge":         ["msedge.exe"],
    "calculator":   ["calc.exe"],
}


@dataclass
class ActionResult:
    ok:      bool
    detail:  str


def launch_app(name: str) -> ActionResult:
    """Launch an application by common name or executable. Never uses
    shell=True -- either a known alias's argv list, or a bare executable name
    resolved via PATH (subprocess still receives it as a single argv token,
    not a shell string, so no shell metacharacters are ever interpreted)."""
    key = name.strip().lower()
    argv = _APP_ALIASES.get(key)
    if argv is None:
        resolved = shutil.which(name) or name
        argv = [resolved]
    try:
        subprocess.Popen(argv, shell=False)
        return ActionResult(True, f"launched {argv[0]}")
    except FileNotFoundError:
        return ActionResult(False, f"'{name}' not found (not a known alias and not on PATH)")
    except Exception as exc:
        return ActionResult(False, f"launch failed: {exc}")


def open_url(url: str) -> ActionResult:
    """Open a URL in the default browser."""
    try:
        webbrowser.open(url)
        return ActionResult(True, f"opened {url}")
    except Exception as exc:
        return ActionResult(False, f"open_url failed: {exc}")


def list_windows() -> list[str]:
    """Titles of all currently open, titled windows."""
    return [t for t in gw.getAllTitles() if t.strip()]


def wait_for_window(title_substr: str, timeout: float = 10.0, poll: float = 0.4) -> str | None:
    """Poll for a window whose title contains `title_substr` (case-insensitive).
    Returns the matched title, or None if it never appears within `timeout`."""
    needle = title_substr.lower()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for title in list_windows():
            if needle in title.lower():
                return title
        time.sleep(poll)
    return None


def focus_window(title_substr: str) -> ActionResult:
    """Find a window by partial title and bring it to the foreground."""
    needle = title_substr.lower()
    matches = [w for w in gw.getWindowsWithTitle("") if needle in w.title.lower()]
    if not matches:
        # getWindowsWithTitle("") returns everything on some backends but not
        # all -- fall back to an explicit per-title lookup for robustness.
        for title in list_windows():
            if needle in title.lower():
                matches = gw.getWindowsWithTitle(title)
                break
    if not matches:
        return ActionResult(False, f"no window matching '{title_substr}'")
    win = matches[0]
    try:
        if win.isMinimized:
            win.restore()
        win.activate()
        return ActionResult(True, f"focused '{win.title}'")
    except Exception as exc:
        return ActionResult(False, f"focus failed: {exc}")


def type_text(text: str) -> ActionResult:
    """Type text into whatever window currently has focus, via clipboard-paste
    (more reliable than character-by-character key events for punctuation/
    unicode -- verified live, see module docstring).

    This necessarily clobbers the user's real clipboard for a moment -- if
    they had something genuinely important copied (a password, a token, ...)
    when this runs, it's overwritten and then restored. The restore delay
    used to be a bare 0.1s, which is not a safe margin for every app to
    actually finish reading the clipboard before it changes back (slower or
    busier apps, remote desktop, etc.) -- a real, silent way to either lose
    the user's original clipboard content or paste the WRONG thing if the
    timing races badly, and the old bare `except: pass` on the restore meant
    a failed restore left the user's clipboard silently replaced with our
    text with no record of it happening. 0.4s is a much safer empirical
    margin; the restore failure is now at least logged, not swallowed."""
    try:
        previous_clipboard = pyperclip.paste()
    except Exception:
        previous_clipboard = None
    try:
        pyperclip.copy(text)
        pyautogui.hotkey("ctrl", "v")
        return ActionResult(True, f"typed {len(text)} chars")
    except Exception as exc:
        return ActionResult(False, f"type_text failed: {exc}")
    finally:
        if previous_clipboard is not None:
            try:
                time.sleep(0.4)
                pyperclip.copy(previous_clipboard)
            except Exception as exc:
                logger.warning(f"[automation] failed to restore clipboard after type_text: {exc}")


def press_key(key: str) -> ActionResult:
    try:
        pyautogui.press(key)
        return ActionResult(True, f"pressed {key}")
    except Exception as exc:
        return ActionResult(False, f"press_key failed: {exc}")


def press_enter() -> ActionResult:
    return press_key("enter")


def press_hotkey(*keys: str) -> ActionResult:
    try:
        pyautogui.hotkey(*keys)
        return ActionResult(True, f"pressed {'+'.join(keys)}")
    except Exception as exc:
        return ActionResult(False, f"press_hotkey failed: {exc}")


def close_app(title_substr: str) -> ActionResult:
    needle = title_substr.lower()
    matches = [w for w in gw.getAllWindows() if needle in w.title.lower()]
    if not matches:
        return ActionResult(False, f"no window matching '{title_substr}'")
    try:
        matches[0].close()
        return ActionResult(True, f"closed '{matches[0].title}'")
    except Exception as exc:
        return ActionResult(False, f"close_app failed: {exc}")
