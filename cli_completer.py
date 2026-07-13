"""
cli_completer.py
Slash-command autocomplete for the VibeAI REPL -- typing "/" pops up a
dropdown of every command (Claude Code-style); each further character
filters it live. A second level kicks in for commands that have their own
subcommands (/workspace, /chat) or a real registry to complete against
(/model <id>), rather than just matching top-level names.

Kept as its own module (not inline in cli.py, already large) since this is
pure input-UX and doesn't touch any business logic -- it only has to stay in
sync BY HAND with cli.py's dispatch table (handle_command / the REPL's
startswith() checks) and its `help` command text, which describe the same
commands from the execution side.
"""
from __future__ import annotations

from prompt_toolkit.completion import Completer, Completion

# (command, argument hint, one-line description)
_COMMANDS: list[tuple[str, str, str]] = [
    ("/model",     "<id>",           "switch the coding agent's model"),
    ("/routing",   "",               "show what adaptive routing memory has learned"),
    ("/m",         "<query>",        "full multi-team manager pipeline"),
    ("/mg",        "<query>",        "alias for /m"),
    ("/think",     "<problem>",      "direct VibeMind call (MoA + debate + verify)"),
    ("/vision",    "<path>",         "analyse a video or image through VisionTeam"),
    ("/context",   "<description>",  "set persistent project context"),
    ("/workspace", "",               "switch/save/load/list workspace folders"),
    ("/chat",      "",               "manage multiple conversations in this workspace"),
    ("/voice",     "[secs|file]",    "speak a task (Groq Whisper transcription)"),
]

_SUBCOMMANDS: dict[str, list[tuple[str, str]]] = {
    "/workspace": [
        ("use",    "<path>  switch the active folder"),
        ("recent", "        pick from recently-used folders"),
        ("save",   "<name>  zip the current workspace"),
        ("load",   "<name>  restore a saved workspace"),
        ("list",   "        list saved workspace snapshots"),
    ],
    "/chat": [
        ("list",   "        list chats in this workspace"),
        ("new",    "<name>  start a new conversation"),
        ("switch", "<name>  switch to another chat"),
        ("rename", "<name>  rename the current chat"),
        ("delete", "<name>  delete a chat"),
    ],
}


class SlashCommandCompleter(Completer):
    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        if not text.startswith("/"):
            return

        if " " not in text:
            # Top level: "/" or "/xy" -> filter command names by what follows "/".
            word = text[1:].lower()
            for cmd, args, desc in _COMMANDS:
                if cmd[1:].lower().startswith(word):
                    yield Completion(
                        cmd,
                        start_position=-len(text),
                        display=cmd + (f" {args}" if args else ""),
                        display_meta=desc,
                    )
            return

        head, _, rest = text.partition(" ")
        if " " in rest:
            return   # already past the first argument word -- nothing sane to suggest

        if head == "/model":
            from config.models_config import MODEL_REGISTRY
            for mid, mdef in MODEL_REGISTRY.items():
                if mid.lower().startswith(rest.lower()):
                    yield Completion(mid, start_position=-len(rest), display_meta=mdef.role)
            return

        subs = _SUBCOMMANDS.get(head)
        if subs:
            for name, desc in subs:
                if name.startswith(rest.lower()):
                    yield Completion(name, start_position=-len(rest), display_meta=desc)
