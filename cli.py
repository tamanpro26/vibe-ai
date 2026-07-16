"""
cli.py — VibeAI Interactive Terminal

Run with:
  python main.py          (default)
  python cli.py           (direct)

Two execution modes:
  Agent mode  (default)  — autonomous coding agent with real-time tool-call display
                           Shows file creation, bash commands, web searches as they happen.
                           Identical feel to Claude Code.
  Manager mode (/m, /mg) — routes through the full multi-team pipeline:
                           5-stage prompt refiner → team dispatch → synthesis.
                           Better for general questions, analysis, multi-team tasks.

Commands:
  help         show this help panel
  status       show active manager and model stats
  clear        clear the screen
  workspace    list files in the current workspace
  models       list available coding models
  /m <task>    run task through the full multi-team manager pipeline
  /model <id>  switch the coding agent model (e.g. /model gpt_oss_120b_coder)
  exit / q     quit
"""
from __future__ import annotations

import asyncio
import os
import re
import sys
import time
from pathlib import Path

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Keep the terminal clean: HuggingFace/transformers print progress bars and
# load reports straight to the console when the memory system loads its
# embedding model. Must be set before those libraries are imported.
os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
os.environ.setdefault("HF_HUB_VERBOSITY", "error")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

from rich.columns import Columns
from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.rule import Rule
from rich.style import Style
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text
from rich import print as rprint

try:
    from prompt_toolkit import PromptSession
    from prompt_toolkit.history import InMemoryHistory
    from prompt_toolkit.styles import Style as PTStyle
    HAS_PROMPT_TOOLKIT = True
except ImportError:
    HAS_PROMPT_TOOLKIT = False

console = Console(highlight=False)

# ── Typewriter-style response printing ────────────────────────────────────────
# Reveals the final response progressively (Claude/ChatGPT-style streaming)
# instead of dumping the whole block at once. Total reveal time is capped so a
# long response doesn't make the user wait ages for a cosmetic effect; short
# ones still read as "typed" rather than a single instant paste.
_TYPEWRITER_MAX_DURATION_S = 1.6
_TYPEWRITER_FRAME_S = 0.02


async def _print_typewriter(text: str) -> None:
    if not text:
        return
    if os.getenv("VIBE_NO_TYPEWRITER") == "1" or not console.is_terminal:
        _print_response_block(text)
        return
    total_frames = max(1, int(_TYPEWRITER_MAX_DURATION_S / _TYPEWRITER_FRAME_S))
    chars_per_frame = max(1, -(-len(text) // total_frames))   # ceil division, no extra import
    shown = 0
    with Live(console=console, refresh_per_second=1 / _TYPEWRITER_FRAME_S, transient=False) as live:
        while shown < len(text):
            shown = min(len(text), shown + chars_per_frame)
            live.update(_render_response_block(text[:shown]))
            await asyncio.sleep(_TYPEWRITER_FRAME_S)


def _render_response_block(text: str):
    """Same rendering rule used at every response print site: real Markdown
    when the text contains a fenced code block, plain indented lines otherwise."""
    if "```" in text:
        return Markdown(text)
    return Text("\n".join(f"  {line}" for line in text.split("\n")))


def _print_response_block(text: str) -> None:
    console.print(_render_response_block(text))


# ── Colour constants ──────────────────────────────────────────────────────────
C_AI    = "cyan"
C_USER  = "green"
C_THINK = "dim italic"
C_TOOL  = "yellow"
C_CMD   = "magenta"
C_OK    = "green"
C_ERR   = "red"
C_DIM   = "dim"
C_TITLE = "bold cyan"

# ── Default coding model ──────────────────────────────────────────────────────
# Promoted 2026-06-30: GLM-4.7 @ Cerebras earned primary empirically — 67 logged
# calls with ZERO failures, 1M tokens/day free, and it produced the session's
# best build solo. The old default (Gemini 2.5 Flash) has a 20 req/day free cap
# (34 rate-limit failures logged) — as primary it survived ~1 iteration per task.
_DEFAULT_MODEL = "glm_47_cerebras"   # GLM-4.7 @ Cerebras
_active_model  = _DEFAULT_MODEL
# False until the user runs /model — lets the adaptive routing memory guide the
# DEFAULT model without ever overriding an explicit choice (core/routing_memory.py).
_model_explicit = False

# ── Workspace + multi-chat session ────────────────────────────────────────────
# The active workspace folder and per-chat conversation history live in a
# WorkspaceSession (core/workspace_session.py), not a bare global list — this
# is what lets the user switch folders and keep several chats per folder.
# _session is initialised in main(); helpers below tolerate it being None so
# module-import order and tests never depend on it.
_MAX_HISTORY_MESSAGES = 20   # ~10 turns
_session = None              # type: ignore  (WorkspaceSession | None)
_LAUNCH_WORKSPACE = None     # type: ignore  (Path | None) — set by the entry point / --workspace
_project_context: str = ""   # persistent context set by /context command


def _history() -> list[dict]:
    return _session.history() if _session is not None else []


async def _maybe_summarize_history() -> None:
    """When history nears the limit, summarize the oldest half instead of hard-cutting."""
    if _session is None:
        return
    msgs = _session.history()
    if len(msgs) < _MAX_HISTORY_MESSAGES - 2:
        return
    to_summarize = msgs[:10]
    keep         = msgs[10:]
    try:
        from models.registry import generate_resilient
        text    = "\n".join(
            f"{m['role'].upper()}: {str(m.get('content', ''))[:300]}"
            for m in to_summarize
            if m.get("role") in ("user", "assistant")
        )
        summary = await generate_resilient(
            "glm_47_cerebras",
            prompt=(
                "Summarize this conversation history in 3–5 sentences, "
                "capturing what was built and the key decisions made:\n\n" + text
            ),
            max_tokens=300,
            temperature=0.3,
        )
        _session.replace_history(
            [{"role": "system", "content": f"[Earlier conversation summary]: {summary}"}] + keep
        )
        console.print("  [dim]↩ Earlier context summarized to preserve memory[/dim]")
    except Exception:
        _session.replace_history(msgs[-_MAX_HISTORY_MESSAGES:])


# ═══════════════════════════════════════════════════════════════════════════════
# BANNER & INTRO
# ═══════════════════════════════════════════════════════════════════════════════

BANNER = """[bold cyan]
  ██╗   ██╗██╗██████╗ ███████╗ █████╗ ██╗
  ██║   ██║██║██╔══██╗██╔════╝██╔══██╗██║
  ██║   ██║██║██████╔╝█████╗  ███████║██║
  ╚██╗ ██╔╝██║██╔══██╗██╔══╝  ██╔══██║██║
   ╚████╔╝ ██║██████╔╝███████╗██║  ██║██║
    ╚═══╝  ╚═╝╚═════╝ ╚══════╝╚═╝  ╚═╝╚═╝[/bold cyan]
[dim]  Multi-Provider AI Orchestration  ·  Terminal Edition[/dim]
"""

def show_banner() -> None:
    console.print(BANNER)
    console.print(Rule(style="cyan dim"))


def show_intro(manager_name: str) -> None:
    console.print()
    console.print(Panel(
        f"[{C_AI}]Hello! I'm VibeAI — a multi-provider team of free AI models working together.[/{C_AI}]\n\n"
        f"[dim]Active manager:[/dim] [{C_AI}]{manager_name}[/{C_AI}]\n\n"
        "Here's what I can do:\n"
        f"  [{C_OK}]✦[/{C_OK}]  Write complete projects and run them to verify they work\n"
        f"  [{C_OK}]✦[/{C_OK}]  Create files, fix errors, install packages automatically\n"
        f"  [{C_OK}]✦[/{C_OK}]  Run terminal commands and show you live output\n"
        f"  [{C_OK}]✦[/{C_OK}]  Search the web for docs, examples, and current info\n"
        f"  [{C_OK}]✦[/{C_OK}]  Analyse screenshots and video recordings\n"
        f"  [{C_OK}]✦[/{C_OK}]  General questions via full multi-team pipeline (prefix: [cyan]/m[/cyan])\n\n"
        "[dim]Just describe what you want — I'll handle everything else.[/dim]",
        title="[bold cyan]VibeAI[/bold cyan]",
        border_style="cyan",
        padding=(1, 2),
    ))
    console.print()
    console.print(
        "  [dim]Commands:[/dim]  "
        "[cyan]help[/cyan]  "
        "[cyan]status[/cyan]  "
        "[cyan]models[/cyan]  "
        "[cyan]workspace[/cyan]  "
        "[cyan]clear[/cyan]  "
        "[cyan]/m <query>[/cyan]  "
        "[cyan]exit[/cyan]"
    )
    console.print(Rule(style="dim"))
    console.print()


# ═══════════════════════════════════════════════════════════════════════════════
# LIVE DISPLAY — real-time agent progress (agent mode)
# ═══════════════════════════════════════════════════════════════════════════════

class LiveDisplay:
    """
    Manages the Rich Live display during an agent run.
    Callbacks update the internal state, which re-renders automatically.
    """

    def __init__(self):
        self.iteration     = 0
        self.model_name    = ""
        self.thinking_text = ""
        self.tool_calls    = []   # [name, args, done, output, success]
        self.done          = False
        self._result       = None
        self._start_time   = time.perf_counter()
        # Live activity trace fed by pipeline logs (model calls, failovers…)
        self.thinker       = ThinkingPanel()
        self.thinker.stage = "Thinking"

    def _tool_icon(self, name: str) -> str:
        return {
            "create_file":    "📄",
            "read_file":      "📖",
            "edit_file":      "✏️ ",
            "delete_file":    "🗑️ ",
            "list_dir":       "📁",
            "bash":           "💻",
            "search_web":     "🌐",
            "fetch_url":      "🔗",
            "vision_analyze": "🎬",
        }.get(name, "⚡")

    def render(self) -> Text:
        lines = Text()
        lines.append(
            f"\n  ┌─ Agent  │  Iteration {self.iteration}/25"
            f"  │  {self.model_name[:30]}\n",
            style="dim",
        )

        if self.thinker.thoughts:
            lines.append("\n")
            for t_line in self.thinker.thoughts[-8:]:
                lines.append_text(t_line)
                lines.append("\n")

        if self.thinking_text:
            lines.append("\n  🧠 Thinking:\n", style=f"bold {C_AI}")
            for line in self.thinking_text[:400].split("\n")[:6]:
                if line.strip():
                    lines.append(f"  │  {line}\n", style=C_THINK)
            if len(self.thinking_text) > 400:
                lines.append("  │  ...\n", style=C_THINK)

        if self.tool_calls:
            lines.append("\n  ⚡ Actions:\n", style=f"bold {C_TOOL}")
            for name, args, done, output, success in self.tool_calls:
                icon    = self._tool_icon(name)
                arg_str = _fmt_args(name, args)
                if done:
                    lines.append("  ")
                    lines.append("✓" if success else "✗",
                                 style=f"bold {C_OK}" if success else f"bold {C_ERR}")
                    lines.append(f"  {icon} ")
                    lines.append(f"{name}", style=f"bold {C_TOOL}")
                    lines.append(f"  {arg_str}\n", style=C_DIM)
                    if output and name == "bash":
                        for ol in output.strip().split("\n")[:4]:
                            lines.append(f"      {ol}\n", style=C_DIM)
                        if len(output.strip().split("\n")) > 4:
                            lines.append("      ...\n", style=C_DIM)
                else:
                    lines.append("  ")
                    lines.append("⟳", style="yellow")
                    lines.append(f"  {icon} ")
                    lines.append(f"{name}", style=f"bold {C_TOOL}")
                    lines.append(f"  {arg_str}\n", style=C_DIM)

        if self.done and self._result:
            r  = self._result
            ms = r.total_ms
            lines.append(f"\n  ✓ ", style=f"bold {C_OK}")
            lines.append(
                f"Done — {r.iterations} iter  ·  {ms/1000:.1f}s  ·  "
                f"{len(r.files_created)} files  ·  {len(r.commands_run)} commands\n",
                style=C_DIM,
            )
        elif not self.done:
            elapsed = time.perf_counter() - self._start_time
            frame   = _SPINNER_FRAMES[int(elapsed * 10) % len(_SPINNER_FRAMES)]
            lines.append(f"\n  {frame} ", style="bold cyan")
            lines.append(f"{self.thinker.stage}… ", style="bold")
            lines.append(f"({elapsed:.0f}s)\n", style=C_DIM)
        return lines

    def __rich_console__(self, console, options):
        """Makes LiveDisplay directly usable as a Rich renderable in Live()."""
        yield from console.render(self.render(), options)

    # ── Callbacks ──────────────────────────────────────────────────────────────

    async def cb_iteration(self, n: int, model: str):
        self.iteration  = n
        self.model_name = model
        self.thinking_text = ""
        self.thinker.stage = "Thinking"
        self.thinker._last = ""   # allow per-iteration "asking model…" lines

    async def cb_thinking(self, text: str):
        self.thinking_text = text

    async def cb_tool_call(self, name: str, args: dict):
        self.tool_calls.append([name, args, False, "", True])
        self.thinker.stage = f"Running {name}"

    async def cb_tool_result(self, result):
        for entry in reversed(self.tool_calls):
            if entry[0] == result.tool_name and not entry[2]:
                entry[2] = True
                entry[3] = result.output
                entry[4] = result.success
                break
        self.thinker.stage = "Thinking"

    async def cb_final(self, result):
        self.done    = True
        self._result = result


# ═══════════════════════════════════════════════════════════════════════════════
# AGENT MODE — direct AgentLoop (Claude-Code-style, tool calls visible)
# ═══════════════════════════════════════════════════════════════════════════════

async def handle_agent_task(task: str, model_id: str | None = None) -> None:
    """Run task through AgentLoop with live tool-call display."""
    from core.agent_loop import AgentLoop, AgentCallbacks
    from tools.agent_tools import DEFAULT_WORKSPACE

    model_id  = model_id or _active_model
    display   = LiveDisplay()
    callbacks = AgentCallbacks(
        on_iteration   = display.cb_iteration,
        on_thinking    = display.cb_thinking,
        on_tool_call   = display.cb_tool_call,
        on_tool_result = display.cb_tool_result,
        on_final       = display.cb_final,
    )

    workspace = _session.workspace if _session is not None else DEFAULT_WORKSPACE
    workspace.mkdir(parents=True, exist_ok=True)

    console.print()
    _chat_note = f"  ·  chat: {_session.chat_name}" if _session is not None else ""
    console.print(
        f"  [{C_AI}]VibeAI[/{C_AI}]  [dim]→ agent mode  ·  model: {model_id}"
        f"  ·  {workspace.name or workspace}{_chat_note}[/dim]"
    )

    # Tell the user UP FRONT which model quotas are exhausted, instead of letting
    # them find out through silently degraded output. The circuit breaker tracks
    # every endpoint that recently failed on quota/rate-limit, with the provider's
    # own stated reset time where available.
    try:
        from models.circuit_breaker import broken_models
        _broken = broken_models()
        if _broken:
            for endpoint, secs in sorted(_broken.items(), key=lambda kv: -kv[1]):
                h, rem = divmod(int(secs), 3600)
                m = rem // 60
                eta = f"{h}h{m:02d}m" if h else f"{m}m" if m else f"{int(secs)}s"
                console.print(
                    f"  [yellow]⚠ tokens exhausted:[/yellow] [bold]{endpoint}[/bold] "
                    f"[dim](retries resume in ~{eta} — fallback models will be used)[/dim]"
                )
    except Exception:
        pass
    console.print()

    from loguru import logger as _logger
    sink_id = _logger.add(display.thinker.feed, level="INFO")
    try:
        with Live(display, refresh_per_second=10, console=console,
                  vertical_overflow="visible") as live:
            loop   = AgentLoop(workspace=workspace)
            result = await loop.run(
                task=task,
                model_id=model_id,
                task_type="coding",
                context=_project_context,
                history=_history(),
                callbacks=callbacks,
                model_explicit=_model_explicit,
            )
            live.update(display.render())
            await asyncio.sleep(0.3)
    finally:
        _logger.remove(sink_id)

    # ── Update conversation history ───────────────────────────────────────────
    if result.final_response and _session is not None:
        _session.append("user", task)
        _session.append("assistant", result.final_response)
        await _maybe_summarize_history()

    # ── Final response ────────────────────────────────────────────────────────
    console.print()
    console.print(Rule("[cyan]Response[/cyan]", style="dim"))
    console.print()

    if result.final_response:
        await _print_typewriter(result.final_response)

    console.print()

    # Show the actual document content, not just "file created" -- found live
    # (2026-07-12): asked to write a letter, got it, but had to open the .txt
    # file manually to read it since final_response is just the model's
    # closing remark. Only populated for plain-text deliverables (.txt/.md);
    # "" for code/web projects, where the summary table below is the right view.
    if result.document_preview:
        console.print(Panel(result.document_preview, title="📄 Document", border_style="green", expand=False))
        console.print()

    # ── Summary table ─────────────────────────────────────────────────────────
    if result.files_created or result.commands_run or result.files_edited:
        table = Table(box=None, show_header=False, padding=(0, 1))
        table.add_column("", style="dim", width=2)
        table.add_column("", style="dim")
        table.add_column("")

        for i, f in enumerate(result.files_created[:8]):
            table.add_row("", "  📄 created" if i == 0 else "", f"[green]{f}[/green]")
        if len(result.files_created) > 8:
            table.add_row("", "", f"[dim]… and {len(result.files_created)-8} more[/dim]")

        for i, f in enumerate(result.files_edited[:4]):
            table.add_row("", "  ✏️  edited" if i == 0 else "", f"[yellow]{f}[/yellow]")

        for i, cmd in enumerate(result.commands_run[:6]):
            table.add_row("", "  💻 ran" if i == 0 else "", f"[magenta]{cmd}[/magenta]")

        table.add_row("", "  📁 workspace", f"[dim]{result.workspace}[/dim]")
        console.print(table)

    console.print()
    console.print(Rule(style="dim"))


# ═══════════════════════════════════════════════════════════════════════════════
# THINKING PANEL — live pipeline activity for manager mode (Claude-style)
# ═══════════════════════════════════════════════════════════════════════════════

_SPINNER_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


class ThinkingPanel:
    """
    Live "what the AI is thinking" trace for the manager pipeline.

    A loguru sink feeds the pipeline's raw log lines in; they are translated
    into friendly activity lines (which team is working, which model answered,
    failovers, reviews…) and rendered under an animated spinner with elapsed
    time — the same feel as Claude Code's activity display.
    """

    MAX_VISIBLE = 12

    def __init__(self) -> None:
        self.thoughts: list[Text] = []
        self.stage = "Reading your request"
        self.done  = False
        self._t0   = time.perf_counter()
        self._last = ""
        self._structured = False   # True once collab_viz events start flowing
        self._had_stage  = False   # connector arrows only BETWEEN stages

    # ── collab_viz events (structured team-collaboration flow) ────────────────
    # Preferred source when available: richer than log-scraping (it carries
    # every model call + explicit pipeline stages). Log translation below
    # stays as the fallback if instrumentation ever goes quiet.

    def feed_event(self, event: dict) -> None:
        from core.collab_viz import format_event
        fmt = format_event(event)
        if fmt is None:
            return
        text, style = fmt
        self._structured = True
        if style.startswith("stage"):
            if self._had_stage:
                self.thoughts.append(Text("        ↓", style="dim"))
            self._had_stage = True
            self.stage = event.get("label", "")[:70]
            rich_style = {"stage_done": "bold green",
                          "stage_fail": "bold red"}.get(style, f"bold {C_AI}")
        else:
            rich_style = "red dim" if style == "model_fail" else "dim"
        self._push(Text("  " + text, style=rich_style))

    # ── loguru sink ────────────────────────────────────────────────────────────

    def feed(self, message) -> None:
        if self._structured:
            return  # structured flow is active — skip log-scraped duplicates
        record = message.record
        raw    = record["message"]
        if "FREE MANAGER COUNCIL" in raw:
            self._push(self._line("🏛", "Free Manager Council taking over (Claude unavailable)", "yellow"))
            return
        text = raw.split("\n")[0].strip()
        if not text:
            return
        friendly = self._translate(text, record["level"].name)
        if friendly is not None:
            self._push(friendly)

    def _push(self, line: Text) -> None:
        if line.plain == self._last:
            return
        self._last = line.plain
        self.thoughts.append(line)
        del self.thoughts[:-60]

    @staticmethod
    def _line(icon: str, body: str, style: str = C_THINK) -> Text:
        out = Text(f"  {icon}  ")
        out.append(body, style=style)
        return out

    # ── raw log → friendly thought ─────────────────────────────────────────────

    def _translate(self, msg: str, level: str) -> Text | None:
        t = self._line

        m = re.search(r"\[manager\] .*fast path: (.*)", msg)
        if m:
            self.stage = "Answering directly"
            return t("⚡", f"Simple request — answering directly: {m.group(1)[:70]}")
        if "[prompt_enhancer] enhancing" in msg:
            m = re.search(r"original=(\d+) chars", msg)
            chars = m.group(1) if m else ""
            self.stage = "Enhancing your prompt"
            return t("✨", f"Enhancing prompt{f' ({chars} chars)' if chars else ''} — 3 specialist models in parallel")
        if "[prompt_enhancer] angles done" in msg:
            m = re.search(r"angles done: (.+?) —", msg)
            angles = m.group(1) if m else ""
            return t("🔗", f"All angles ready{f': {angles}' if angles else ''} — synthesising")
        if "[prompt_enhancer] done" in msg:
            m = re.search(r"(\d+) → (\d+) chars \| boost=([\d.]+)x", msg)
            if m:
                return t("✅", f"Prompt enhanced: {m.group(1)} → {m.group(2)} chars  ({m.group(3)}x richer)", "green dim")
            return t("✅", "Prompt enhanced", "green dim")
        if "[prompt_enhancer] short prompt" in msg:
            return t("⚡", "Short prompt — enhancement skipped (fast enough already)")
        if "[prompt_enhancer] all angles failed" in msg:
            return t("⚠", "Prompt enhancement unavailable — using original", "yellow dim")
        if "[prompt_refiner] starting" in msg:
            self.stage = "Refining your prompt"
            return t("🔍", "Refining your prompt")
        if "stages 1-4 in parallel" in msg:
            return t("🔍", "4 analysts examining the request in parallel")
        if "[prompt_refiner] stage 5/5" in msg:
            self.stage = "Planning the task"
            return t("📋", "Compiling the task plan")
        m = re.search(r"\[prompt_refiner\] done \| task_id=\S+ \| type=(\w+) \| active_teams=\[(.*)\]", msg)
        if m:
            self.stage = "Dispatching teams"
            return t("📋", f"Task type: {m.group(1)} — teams: {m.group(2).replace(chr(39), '')}")
        m = re.search(r"\[(brain|code|vision|design|router)\] starting \| task=\S+ \| iter=(\d+)", msg)
        if m:
            self.stage = f"{m.group(1).capitalize()} team working"
            return t("🚀", f"{m.group(1).capitalize()} team working (iteration {m.group(2)})")
        m = re.search(r"\[brain\] planning with (\w+)", msg)
        if m:
            return t("🧠", f"Brain team planning with {m.group(1)}")
        if "[brain] deep reasoning via VibeMind" in msg:
            self.stage = "VibeMind reasoning"
            return t("🧠", "VibeMind engaged — fleet reasoning as one network")
        if "[agent] hard reasoning detected" in msg:
            self.stage = "VibeMind pre-reasoning"
            return t("🧠", "Hard reasoning detected — VibeMind pre-analysis engaged")
        if "[agent] VibeMind plan injected" in msg:
            return t("🎯", "VibeMind plan ready — agent executing", "green dim")
        if "[brain] VibeMind failed" in msg:
            return t("♻", "VibeMind unavailable — single-model planner stepping in", "yellow dim")
        if "[brain] flash reasoning" in msg:
            return t("⚡", "Flash VibeMind (refinement pass — Cerebras-first, early exit)")
        if "[flash_vibemind] early exit" in msg:
            return t("⚡", "Early consensus — aggregation skipped (fast path)", "green dim")
        if "[flash_vibemind]" in msg:
            return t("⚡", "Flash VibeMind — 3 fast proposers running")
        if "[manager] creative task" in msg:
            self.stage = "Creative synthesis"
            return t("✨", "Creative task — 5-step synthesis pipeline engaged")
        if "[creative] step 1" in msg:
            return t("📋", "Analysing task — building creative brief")
        if "[creative] step 2" in msg:
            return t("✍", "3 drafters writing in parallel (temp=0.9)")
        if "[creative] step 3" in msg:
            return t("🔎", "Selecting best draft against the brief")
        if "[creative] step 4" in msg:
            return t("✨", "Precision polish pass on winning draft")
        if "[creative] pipeline complete" in msg:
            return t("✓", "Creative synthesis complete", "green dim")
        if "[domain_retriever]" in msg:
            m = re.search(r"domain=(\w+)", msg)
            dom = m.group(1) if m else "domain"
            return t("🌐", f"Retrieving current {dom} knowledge from web")
        if "[vibemind] socratic probe" in msg:
            m = re.search(r"(\d+) gaps", msg)
            n = m.group(1) if m else ""
            return t("❓", f"Knowledge gaps identified{f': {n}' if n else ''} — searching")
        if "[vibemind] probe gaps filled" in msg:
            return t("📚", "Knowledge gaps filled from web")
        if "[vibemind] session cache hit" in msg:
            return t("⚡", "Session cache hit — reusing answer from earlier in conversation", "green dim")
        if "[vibemind] semantic cache hit" in msg:
            m = re.search(r"similarity ([\d.]+)", msg)
            sim = f" ({m.group(1)} similarity)" if m else ""
            return t("⚡", f"Semantic cache hit{sim} — near-identical past answer retrieved", "green dim")
        if "[vibemind] deduplication" in msg:
            m = re.search(r"(\d+) proposals → (\d+) unique", msg)
            if m:
                saved = int(m.group(1)) - int(m.group(2))
                return t("⚡", f"Proposal deduplication: {saved} duplicate{'s' if saved != 1 else ''} collapsed — aggregator sees fewer tokens", "cyan dim")
        if "[vibemind] single aggregator" in msg:
            m = re.search(r"agreement ([\d]+)%", msg)
            pct = m.group(1) if m else ""
            return t("⚡", f"Strong consensus ({pct}%) — single aggregator engaged (50% token saving)", "cyan dim")
        if "[vibemind] lazy verifier" in msg:
            return t("⚡", "100% numeric consensus — code execution skipped (lazy verifier)", "cyan dim")
        if "[vibemind] execution certain" in msg:
            return t("✅", "Execution verified ≥95% — finalizer skipped (answer already confirmed)", "green dim")
        if "[vibemind] internal call" in msg:
            return t("⚡", "Internal reasoning call — finalizer skipped (saving ~3500 tokens)", "cyan dim")
        if "[vibemind] adaptive debate" in msg:
            m = re.search(r"(\d+) rounds", msg)
            rounds = m.group(1) if m else ""
            return t("⚔", f"High disagreement — running {rounds}-round debate" if rounds else "Adaptive debate rounds")
        m = re.search(r"\[vibemind\] layer 1: (\d+) units", msg)
        if m:
            self.stage = "VibeMind reasoning"
            return t("🧠", f"Layer 1 — {m.group(1)} units reasoning independently")
        m = re.search(r"\[vibemind\] layer (\d+): (\d+) aggregators building on (\d+) proposals \(budget=(\d+)\)", msg)
        if m:
            return t("🔗", f"Layer {m.group(1)} — building on best reasoning chain across {m.group(3)} proposals (budget={m.group(4)})")
        m = re.search(r"\[vibemind\] layer (\d+): (\d+) units aggregating (\d+)", msg)
        if m:
            return t("🔗", f"Layer {m.group(1)} — aggregating {m.group(3)} solutions")
        m = re.search(r"\[vibemind\] debate round (\d+): (\d+) units", msg)
        if m:
            self.stage = "Units debating"
            return t("⚔", f"Debate round {m.group(1)} — units cross-examining each other")
        m = re.search(r"\[vibemind\] debate round (\d+): agreement (\d+)% → (\d+)%", msg)
        if m:
            return t("🤝", f"Debate converged: {m.group(2)}% → {m.group(3)}% agreement", "green dim")
        m = re.search(r"\[vibemind\] consensus: '(.+?)' \((\d+)%", msg)
        if m:
            return t("🎯", f"Consensus: {m.group(1)} ({m.group(2)}% of units agree)", "green dim")
        if "[vibemind] format-constrained task" in msg:
            m = re.search(r"format-constrained task: (\w+)", msg)
            fmt = m.group(1).upper() if m else "format"
            lang_m = re.search(r"\((\w+)\)", msg)
            lang = f" / {lang_m.group(1)}" if lang_m else ""
            self.stage = "Format-constrained task"
            return t("📐", f"Format constraint detected: {fmt}{lang} — using format-aware proposers + judge selection")
        if "[vibemind] complex task detected" in msg:
            self.stage = "Complex task — decomposing"
            return t("🔩", "Complex task detected — adaptive budget (2× tokens) + decomposition")
        m = re.search(r"\[vibemind\] decomposed: \[(.+)\]", msg)
        if m:
            return t("📋", f"Decomposed into sub-tasks: {m.group(1)[:80]}")
        m = re.search(r"\[vibemind\] running (\d+) sub-tasks in parallel", msg)
        if m:
            self.stage = f"Running {m.group(1)} sub-tasks"
            return t("🔀", f"Running {m.group(1)} sub-tasks in parallel — each solved independently")
        m = re.search(r"\[vibemind\] integrating (\d+)/(\d+) sub-task results", msg)
        if m:
            return t("🔗", f"Integrating {m.group(1)} of {m.group(2)} sub-task results via Gemini 2.5 Flash")
        if "[vibemind] decomposition failed" in msg:
            return t("♻", "Sub-task decomposition failed — falling back to single-pass", "yellow dim")
        m = re.search(r"\[vibemind\] format path → judge selecting best of (\d+)", msg)
        if m:
            return t("⚖", f"Format path: judge selecting best of {m.group(1)} candidates (no blending)")
        m = re.search(r"\[vibemind\] judge selected candidate (\d+)", msg)
        if m:
            return t("✅", f"Judge selected candidate {m.group(1)} — best format match", "green dim")
        m = re.search(r"\[vibemind\] complex mode: depth=(\d+) \| prop_budget=(\d+) \| agg_budget=(\d+)", msg)
        if m:
            return t("🔩", f"Complex mode: depth={m.group(1)} | proposer budget={m.group(2)} | aggregator budget={m.group(3)}")
        if "[vibemind] output layer" in msg:
            return t("✨", "Synthesising one coherent answer")
        if "[verifier] verifying by execution" in msg:
            self.stage = "Verifying by execution"
            return t("🔬", "Verifying the answer by running code")
        if "[verifier] verifying by cross-model" in msg:
            return t("🔬", "Cross-checking the answer with independent units")
        m = re.search(r"\[verifier\] execution: computed=(.+?) verified=(\w+)", msg)
        if m:
            ok = m.group(2) == "True"
            return t("✅" if ok else "🔧",
                     f"Execution {'confirmed' if ok else 'corrected'}: {m.group(1)[:40]}",
                     "green dim" if ok else "yellow dim")
        if "[verifier] execution: problem has NO solution" in msg:
            return t("🚫", "Execution proved the problem has no solution", "yellow dim")
        m = re.search(r"\[(brain|code|vision|design|router)\] done \| output_len=(\d+)", msg)
        if m:
            return t("✓", f"{m.group(1).capitalize()} team finished ({m.group(2)} chars)", "green dim")
        m = re.search(r"\[code\] best-of-(\d+)", msg)
        if m:
            return t("⚖", f"Comparing {m.group(1)} candidate solutions")
        if "[code] verification failed" in msg:
            return t("🔧", "Code failed checks — auto-repairing")
        if msg.startswith("[executor]"):
            return t("💻", "Executed code in the sandbox")
        m = re.search(r"\[manager\] reviewing (\w+) output \(iteration (\d+)\)", msg)
        if m:
            self.stage = "Reviewing quality"
            return t("🔎", f"Quality review of {m.group(1)} output (iteration {m.group(2)})")
        if "[manager] synthesising" in msg:
            self.stage = "Writing the final response"
            return t("✨", "Writing the final response")
        if "[critic]" in msg:
            return t("🔎", "Adversarial critic challenging the output")
        m = re.search(r"\[registry\] (\w+) down \S+ (\w+) answered instead", msg)
        if m:
            return t("♻", f"{m.group(1)} unavailable — {m.group(2)} stepped in", "yellow dim")
        m = re.search(r"\[manager\] media detected: (\w+) → (.+)", msg)
        if m:
            self.stage = "Vision pipeline"
            return t("🎬", f"Media detected: {m.group(1)} file '{m.group(2)}' — routing to VisionTeam")
        if "[manager] VisionTeam force-activated" in msg:
            return t("🎬", "VisionTeam activated by manager — media routing engaged", "cyan dim")
        if "[vision] video pipeline starting" in msg:
            self.stage = "Video pipeline"
            return t("🎬", "Video pipeline started — converting to .frames")
        if "[vision] converted to .frames" in msg:
            return t("🎞", "Video converted — analysing motion and key frames")
        if "[vision] analysis done" in msg:
            m = re.search(r"events=(\d+).*keyframes=(\d+)", msg)
            if m:
                return t("📊", f"Motion analysis done — {m.group(1)} events, {m.group(2)} key frames")
        if "[vision] transcribing audio" in msg:
            return t("🎙", "Transcribing audio via Whisper Large v3")
        if "[vision] audio transcript" in msg:
            m = re.search(r"(\d+) chars", msg)
            return t("🎙", f"Audio transcribed ({m.group(1)} chars)" if m else "Audio transcribed")
        if "[vision] image pipeline" in msg:
            self.stage = "Image pipeline"
            return t("🖼", "Image pipeline — Gemini 2.5 Flash + Gemini 2.0 Flash analysing")
        if "[vision] .frames analysis failed" in msg:
            return t("⚠", ".frames load failed (buffer alignment) — switching to direct OpenCV extraction", "yellow dim")
        if "[vision] cv2 fallback: sampling" in msg:
            m = re.search(r"sampling (\d+) frames from (.+)", msg)
            if m:
                return t("🎞", f"OpenCV fallback: sampling {m.group(1)} frames directly from {m.group(2)}")
        if "[vision] cv2 fallback: extracted" in msg:
            m = re.search(r"extracted (\d+) frames", msg)
            return t("✅", f"Frames extracted ({m.group(1) if m else '?'}) — sending to vision models", "green dim")
        if "[vision] cv2 fallback also failed" in msg:
            return t("✗", "Both .frames and OpenCV frame extraction failed — video unreadable", "red")
        if "[vision] synthesising" in msg:
            m = re.search(r"synthesising (\d+)", msg)
            n = m.group(1) if m else ""
            self.stage = "Synthesising vision output"
            return t("🔗", f"Merging {n} visual analyses into one coherent answer" if n else "Synthesising vision analyses")
        if "[vision] synthesis failed" in msg:
            return t("⚠", "Vision synthesis failed — returning raw model outputs", "yellow dim")
        if "[vision] all UI models failed" in msg:
            return t("♻", "UI vision models down — video models handling image as fallback", "yellow dim")
        if "[vision] audio transcription failed" in msg:
            return t("⚠", "Audio transcription failed — video-only analysis", "yellow dim")
        if "[code] session cache hit" in msg:
            return t("⚡", "Code cache hit — returning cached result (5-min TTL)", "green dim")
        if "[code] best-of-N: structural agreement" in msg:
            return t("⚡", "Best-of-N: all candidates agree — judge pass skipped", "cyan dim")
        if "[code] review pass triggered" in msg:
            return t("🔍", "Incomplete markers found — running review pass with qwen3")
        if "[code] parallel debug: differing diagnoses" in msg:
            return t("🔀", "Two debuggers give different diagnoses — merging into best fix")
        m2 = re.search(r"\[design\] intent=(\w+) \| prompt expanded: (\d+) → (\d+)", msg)
        if m2:
            self.stage = "Design generation"
            return t("✨", f"Design intent: {m2.group(1)} | Prompt: {m2.group(2)} → {m2.group(3)} chars")
        if "[design] all primary models failed" in msg:
            return t("♻", "Design models failed — flux_asset fallback engaged", "yellow dim")
        if "[design] animation generation failed" in msg:
            return t("⚠", "Animation model failed — static keyframe only", "yellow dim")
        if "[memory] stored" in msg:
            return t("🧷", "Saved this solution to memory")
        m = re.search(r"\[collective_memory\] retrieved (\d+) memor", msg)
        if m:
            n = m.group(1)
            self.stage = "Recalling past reasoning"
            return t("🧠", f"Recalling {n} relevant past reasoning {'memory' if n == '1' else 'memories'} from collective", "cyan dim")
        if "[collective_memory] stored reasoning_success" in msg:
            return t("💾", "Verified reasoning path saved to collective memory", "green dim")
        if "[collective_memory] stored debate_insight" in msg:
            return t("💾", "Winning debate argument memorised for the fleet", "green dim")
        if "[collective_memory] stored failure_pattern" in msg:
            return t("💾", "Failure pattern recorded — fleet will avoid this approach", "yellow dim")
        if level in ("WARNING", "ERROR"):
            return t("⚠", msg[:90], "yellow dim" if level == "WARNING" else "red")
        m = re.search(r"\[([\w.]+)\] generate \| max_tokens=\d+", msg)
        if m:
            return t("·", f"asking {m.group(1)}…")
        return None

    # ── rendering ──────────────────────────────────────────────────────────────

    def finish(self) -> None:
        self.done = True

    def __rich_console__(self, console_, options):
        out = Text()
        out.append("\n")
        for line in self.thoughts[-self.MAX_VISIBLE:]:
            out.append_text(line)
            out.append("\n")
        elapsed = time.perf_counter() - self._t0
        if self.done:
            out.append(f"\n  ✓ Done in {elapsed:.1f}s\n", style=f"bold {C_OK}")
        else:
            frame = _SPINNER_FRAMES[int(elapsed * 10) % len(_SPINNER_FRAMES)]
            out.append(f"\n  {frame} ", style="bold cyan")
            out.append(f"{self.stage}… ", style="bold")
            out.append(f"({elapsed:.0f}s)\n", style=C_DIM)
        yield out


# ═══════════════════════════════════════════════════════════════════════════════
# MANAGER MODE — full multi-team pipeline (/m prefix)
# ═══════════════════════════════════════════════════════════════════════════════

async def handle_manager_task(task: str) -> None:
    """Route task through the full ClaudeManager → multi-team pipeline."""
    from loguru import logger as _logger

    console.print()
    console.print(f"  [{C_AI}]VibeAI[/{C_AI}]  [dim]→ manager mode  ·  multi-team pipeline[/dim]")

    panel   = ThinkingPanel()
    sink_id = _logger.add(panel.feed, level="INFO")
    from core.collab_viz import subscribe as _viz_sub, unsubscribe as _viz_unsub
    _viz_sub(panel.feed_event)

    try:
        from manager.claude_manager import manager as mgr
        from core.state import state

        await state.init()
        await mgr.startup()

        with Live(panel, refresh_per_second=10, console=console,
                  vertical_overflow="visible"):
            response = await mgr.handle_user_request(task)
            panel.finish()
            await asyncio.sleep(0.15)

    except Exception as exc:
        console.print(f"\n  [{C_ERR}]Manager error:[/{C_ERR}] {exc}")
        return
    finally:
        _logger.remove(sink_id)
        _viz_unsub(panel.feed_event)

    console.print()
    console.print(Rule("[cyan]Response[/cyan]", style="dim"))
    console.print()

    await _print_typewriter(response)

    console.print()
    console.print(Rule(style="dim"))


# ═══════════════════════════════════════════════════════════════════════════════
# BUILT-IN COMMANDS
# ═══════════════════════════════════════════════════════════════════════════════

async def handle_command(cmd: str) -> None:
    global _active_model, _model_explicit
    cmd = cmd.strip()
    cmd_lower = cmd.lower()

    # ── /model <id> ───────────────────────────────────────────────────────────
    if cmd_lower.startswith("/model"):
        parts = cmd.split(None, 1)
        if len(parts) < 2:
            console.print(f"\n  Current model: [cyan]{_active_model}[/cyan]")
            console.print("  Usage: [dim]/model <model_id>[/dim]  (see [cyan]models[/cyan] command)\n")
        else:
            new_id = parts[1].strip()
            from config.models_config import MODEL_REGISTRY
            if new_id not in MODEL_REGISTRY:
                console.print(f"\n  [{C_ERR}]Unknown model:[/{C_ERR}] {new_id}  (run [cyan]models[/cyan] to list valid IDs)\n")
            else:
                _active_model = new_id
                _model_explicit = True   # explicit choice — routing memory won't override it
                md = MODEL_REGISTRY[new_id]
                console.print(f"\n  ✓  Switched to [cyan]{new_id}[/cyan]  [{C_DIM}]({md.api_model})[/{C_DIM}]\n")
        return

    # ── /routing — inspect what the adaptive routing memory has learned ───────
    if cmd_lower == "/routing" or cmd_lower.startswith("/routing"):
        from core.routing_memory import get_memory
        rows = get_memory().summary()
        if not rows:
            console.print("\n  [dim]Routing memory is empty — it learns which model succeeds "
                          "for which kind of task from your real runs.[/dim]\n")
            return
        table = Table(title="Adaptive Routing Memory — learned from your real runs",
                      box=None, padding=(0, 2), header_style="bold cyan")
        table.add_column("Category", style="cyan")
        table.add_column("Model")
        table.add_column("Confidence", justify="right")
        table.add_column("✓ / ✗", justify="right", style="dim")
        for r in rows[:20]:
            col = "green" if r["confidence"] >= 0.6 else ("yellow" if r["confidence"] >= 0.45 else "red")
            table.add_row(r["category"], r["model_id"],
                          f"[{col}]{r['confidence']:.0%}[/{col}]",
                          f"{r['success']}/{r['failure']}")
        console.print()
        console.print(table)
        console.print("\n  [dim]≥60% + ≥3 runs → the default model auto-routes there for that task type.[/dim]\n")
        return

    if cmd_lower == "help":
        console.print(Panel(
            "[bold]Agent mode (default) — autonomous coding with real-time tool display:[/bold]\n"
            "[dim]  Build a REST API with FastAPI, SQLite, and pytest tests\n"
            "  Create a React todo app with TypeScript\n"
            "  Debug this Python error: [paste your error]\n"
            "  Write a web scraper for Hacker News\n"
            "  Build a CLI tool that converts JSON to CSV[/dim]\n\n"
            "[bold]Manager mode (/m prefix) — full multi-team pipeline:[/bold]\n"
            "[dim]  /m Explain the difference between transformers and RNNs\n"
            "  /m What's the best approach to building a RAG system?\n"
            "  /m Review this architecture and suggest improvements\n\n"
            "  Pipeline: Prompt Enhancer → Prompt Refiner → Team Dispatch → Review → Synthesis[/dim]\n\n"
            "[bold]Commands:[/bold]\n"
            "  [cyan]/m <query>[/cyan]               — full multi-team manager pipeline\n"
            "  [cyan]/think <problem>[/cyan]          — direct VibeMind call (MoA + debate + verify)\n"
            "  [cyan]/vision <path>[/cyan]            — analyse a video or image through VisionTeam\n"
            "  [cyan]/vision <path> -- <prompt>[/cyan] — vision analysis with a specific question\n"
            "  [cyan]/model <id>[/cyan]               — switch coding agent model\n"
            "  [cyan]/routing[/cyan]                  — show what routing memory has learned (which model wins per task type)\n"
            "  [cyan]/voice [secs|file][/cyan]        — speak a task (Groq Whisper transcription)\n"
            "  [cyan]/context <description>[/cyan]    — set persistent project context\n"
            "  [cyan]/workspace use <path>[/cyan]     — switch the active folder (agent works here)\n"
            "  [cyan]/workspace recent[/cyan]         — pick from recently-used folders\n"
            "  [cyan]/workspace save|load|list[/cyan] — zip/restore/list named snapshots\n"
            "  [cyan]/chat new <name>[/cyan]          — start a new conversation in this folder\n"
            "  [cyan]/chat switch <name>[/cyan]       — switch between chats (each keeps its own history)\n"
            "  [cyan]/chat list[/cyan]                — list chats in this workspace\n"
            "  [cyan]models[/cyan]                   — list available coding models\n"
            "  [cyan]status[/cyan]                   — show manager health and API call counts\n"
            "  [cyan]/ceo[/cyan]                      — on-demand AI-organization health report (slow, ~20s)\n"
            "  [cyan]workspace[/cyan]                — list files in workspace\n"
            "  [cyan]clear[/cyan]                    — clear the screen\n"
            "  [cyan]exit / q[/cyan]                 — quit VibeAI",
            title="[bold cyan]Help[/bold cyan]",
            border_style="cyan",
            padding=(1, 2),
        ))

    elif cmd_lower == "models":
        from config.models_config import MODEL_REGISTRY
        table = Table(title="Available Coding Models", box=None, padding=(0, 2), header_style="bold cyan")
        table.add_column("ID",          style="cyan")
        table.add_column("API Model",   style="dim")
        table.add_column("Provider")
        table.add_column("Role",        style="dim")
        code_models = [
            (mid, md) for mid, md in MODEL_REGISTRY.items()
            if md.team in ("code", "brain")
        ]
        for mid, md in code_models:
            marker = " ◀ active" if mid == _active_model else ""
            table.add_row(mid + marker, md.api_model, md.provider, md.role)
        console.print()
        console.print(table)
        console.print(f"\n  Switch model: [cyan]/model <id>[/cyan]\n")

    elif cmd_lower == "status":
        from tools.manager_fallback import fallback_chain
        from models.registry import get_call_stats
        report   = fallback_chain.status_report()
        free_s   = report.get("free_team", {})
        claude_s = report.get("claude", {})
        stats    = get_call_stats()

        table = Table(title="System Status", box=None, padding=(0, 2), header_style="bold")
        table.add_column("Component", style="bold")
        table.add_column("Status")
        table.add_column("Details", style="dim")

        table.add_row(
            "Active manager",
            f"[cyan]{report['active_manager']}[/cyan]",
            "backup active" if report["using_free_team"] else "primary",
        )
        table.add_row(
            "Claude Sonnet 4.6",
            f"[{'green' if not report['using_free_team'] else 'dim'}]{'✓ online' if not report['using_free_team'] else '○ on cooldown'}[/]",
            f"calls: {claude_s.get('calls', 0)}  fails: {claude_s.get('fails', 0)}",
        )
        table.add_row(
            "Free Manager Council",
            "[yellow]active[/yellow]" if free_s.get("active") else "[green]standby[/green]",
            f"total calls: {free_s.get('total_calls', 0)}",
        )
        table.add_row(
            "Agent model",
            f"[cyan]{_active_model}[/cyan]",
            "use /model <id> to switch",
        )
        if _session is not None:
            table.add_row(
                "Workspace",
                f"[cyan]{_session.workspace.name or _session.workspace}[/cyan]",
                str(_session.workspace),
            )
            table.add_row(
                "Chat",
                f"[cyan]{_session.chat_name}[/cyan]",
                f"{len(_session.list_chats())} chat(s) in this workspace — /chat to manage",
            )
        _hist = _history()
        table.add_row(
            "History",
            f"[cyan]{len(_hist)}/{_MAX_HISTORY_MESSAGES}[/cyan] messages",
            "auto-summarized when full" if _hist else "empty",
        )
        if _project_context:
            table.add_row(
                "Project context",
                "[green]set[/green]",
                _project_context[:60] + ("…" if len(_project_context) > 60 else ""),
            )
        if stats:
            call_str = "  ".join(f"{p}: {n}" for p, n in sorted(stats.items()))
            table.add_row("API calls (session)", "[dim]tracked[/dim]", call_str)
        console.print()
        console.print(table)
        console.print()

    elif cmd_lower == "workspace":
        workspace = _session.workspace if _session is not None else DEFAULT_WORKSPACE
        workspace.mkdir(parents=True, exist_ok=True)
        files = [f for f in sorted(workspace.rglob("*")) if ".vibeai" not in f.parts]
        console.print(f"\n  Workspace: [cyan]{workspace}[/cyan]")
        if files:
            table = Table(box=None, show_header=False, padding=(0, 1))
            table.add_column("", style="dim")
            table.add_column("")
            for item in files[:30]:
                rel   = item.relative_to(workspace)
                icon  = "📁" if item.is_dir() else "📄"
                table.add_row(icon, str(rel))
            if len(files) > 30:
                console.print(f"  [dim]… and {len(files)-30} more[/dim]")
            console.print(table)
        else:
            console.print("  [dim](empty — files will appear here after your first task)[/dim]")
        console.print()

    elif cmd_lower == "clear":
        if _session is not None:
            _session.clear_current()
        console.clear()
        show_banner()

    elif cmd_lower in ("exit", "quit", "q", "bye"):
        console.print()
        console.print(
            f"  [{C_AI}]VibeAI:[/{C_AI}] Goodbye!  "
            f"[dim]Workspace: {DEFAULT_WORKSPACE}[/dim]"
        )
        console.print()
        sys.exit(0)

    else:
        console.print(f"  [dim]Unknown command. Type [cyan]help[/cyan] for a list.[/dim]")


# ═══════════════════════════════════════════════════════════════════════════════
# /think — direct VibeMind call with live ThinkingPanel
# ═══════════════════════════════════════════════════════════════════════════════

async def handle_think(problem: str) -> None:
    """Route directly through VibeMind (MoA + debate + execution verification)."""
    from loguru import logger as _logger

    console.print()
    console.print(f"  [{C_AI}]VibeMind[/{C_AI}]  [dim]→ MoA reasoning  ·  5 proposers  ·  debate  ·  verification[/dim]")

    panel   = ThinkingPanel()
    panel.stage = "Proposers reasoning"
    sink_id = _logger.add(panel.feed, level="INFO")

    bb = None
    try:
        with Live(panel, refresh_per_second=10, console=console, vertical_overflow="visible"):
            from core.reasoning_core import reasoning_core
            bb = await reasoning_core.reason(problem=problem, depth=1, max_tokens=3000)
            panel.finish()
            await asyncio.sleep(0.15)
    except Exception as exc:
        console.print(f"\n  [{C_ERR}]VibeMind error:[/{C_ERR}] {exc}")
        return
    finally:
        _logger.remove(sink_id)

    console.print()
    console.print(Rule("[cyan]VibeMind Answer[/cyan]", style="dim"))
    console.print()

    if bb and bb.final:
        await _print_typewriter(bb.final)

    if bb:
        v = bb.verdict
        meta_parts = []
        if bb.consensus:
            meta_parts.append(f"[cyan]{bb.agreement:.0%} of units agreed[/cyan]")
        if v and v.method:
            if v.method in ("execution", "constraints"):
                label = "✓ verified by execution" if v.verified else "⚠ corrected by execution"
                meta_parts.append(f"[green]{label}[/green]")
            elif v.method == "debate":
                meta_parts.append(f"[yellow]cross-examined via debate[/yellow]")
        if meta_parts:
            console.print()
            console.print(f"  " + "  ·  ".join(meta_parts))

    console.print()
    console.print(Rule(style="dim"))


# ═══════════════════════════════════════════════════════════════════════════════
# /ceo — on-demand aggregate oversight report (manager/ceo.py)
# ═══════════════════════════════════════════════════════════════════════════════

async def handle_ceo_report() -> None:
    """Ask the CEO model to synthesize a health report from real Council/
    team-leader/status signal already logged on disk. Deliberately slow
    (~20s, big model) and only run when explicitly invoked -- see
    manager/ceo.py's module docstring for why this never runs per-request.
    """
    console.print()
    console.print(f"  [{C_AI}]CEO[/{C_AI}]  [dim]→ reading Council + team-leader logs  ·  nemotron-3-ultra-550b  ·  ~20s[/dim]")

    try:
        with console.status("[dim]Synthesizing oversight report...[/dim]", spinner="dots"):
            from manager.ceo import generate_oversight_report
            report = await generate_oversight_report()
    except Exception as exc:
        console.print(f"\n  [{C_ERR}]CEO report failed:[/{C_ERR}] {exc}\n")
        return

    console.print()
    console.print(Rule("[cyan]CEO Oversight Report[/cyan]", style="dim"))
    console.print()
    console.print(report)
    console.print()
    console.print(Rule(style="dim"))


# ═══════════════════════════════════════════════════════════════════════════════
# /vision — direct VisionTeam call for video/image analysis
# ═══════════════════════════════════════════════════════════════════════════════

async def handle_vision(path: str, instruction: str = "") -> None:
    """Analyse a video or image file through the VisionTeam pipeline."""
    from loguru import logger as _logger
    from pathlib import Path as _Path

    path = path.strip().strip('"').strip("'")
    if not _Path(path).exists():
        console.print(f"\n  [{C_ERR}]File not found:[/{C_ERR}] {path}\n")
        return

    ext = _Path(path).suffix.lower()
    is_video = ext in (".mp4", ".mov", ".avi", ".mkv", ".webm", ".frames")
    is_image = ext in (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")

    if not is_video and not is_image:
        console.print(f"\n  [{C_ERR}]Unsupported file type:[/{C_ERR}] {ext}  "
                      f"(expected video or image)\n")
        return

    console.print()
    kind = "video" if is_video else "image"
    console.print(
        f"  [{C_AI}]VisionTeam[/{C_AI}]  "
        f"[dim]→ {kind} pipeline  ·  {_Path(path).name}[/dim]"
    )

    panel   = ThinkingPanel()
    panel.stage = "Analysing"
    sink_id = _logger.add(panel.feed, level="INFO")

    result = None
    try:
        with Live(panel, refresh_per_second=10, console=console,
                  vertical_overflow="visible"):
            from core.imcp import (
                TaskJSON, Classification, TaskType, Complexity,
                TaskContext, TeamActivation, Priority,
            )

            desc = instruction or f"Analyse this {kind} in detail — describe what is happening, the visual content, any audio or speech, and anything notable."
            task_json = TaskJSON(
                original_prompt=desc,
                refined_prompt=desc,
                classification=Classification(
                    primary_type=TaskType.VIDEO_ANALYSIS if is_video else TaskType.UI_DESIGN,
                    complexity=Complexity.MODERATE,
                ),
                success_criteria=[f"Clear, detailed description of {kind} content"],
                active_teams={
                    "vision": TeamActivation(
                        active=True,
                        models=["gemini_flash_vision", "nemotron_vl", "llama4_maverick"],
                        instruction=desc,
                        priority=Priority.HIGH,
                    )
                },
                team_instructions={"vision": desc},
                context=TaskContext(),
            )

            extra = (
                {"video_path": path} if ext != ".frames"
                else {"frames_path": path}
            ) if is_video else {}

            if is_image:
                import base64 as _b64
                with open(path, "rb") as _f:
                    extra = {"image_b64": _b64.b64encode(_f.read()).decode()}

            from teams.vision import VisionTeam
            team   = VisionTeam()
            result = await team.run(
                task_json=task_json,
                instruction=instruction or f"Analyse this {kind} in detail.",
                extra=extra,
            )
            panel.finish()
            await asyncio.sleep(0.15)

    except Exception as exc:
        console.print(f"\n  [{C_ERR}]Vision error:[/{C_ERR}] {exc}")
        return
    finally:
        _logger.remove(sink_id)

    console.print()
    console.print(Rule("[cyan]Vision Analysis[/cyan]", style="dim"))
    console.print()

    if result:
        await _print_typewriter(result)

    console.print()
    console.print(Rule(style="dim"))


# ═══════════════════════════════════════════════════════════════════════════════
# /workspace — save and load named workspaces
# ═══════════════════════════════════════════════════════════════════════════════

async def handle_workspace_cmd(cmd: str) -> None:
    """Handle /workspace use|save|load|list|recent."""
    import zipfile
    from tools.agent_tools import DEFAULT_WORKSPACE

    parts = cmd.strip().split(None, 2)
    # parts: ['/workspace', 'use'|'save'|'load'|'list'|'recent', <arg>]
    sub   = parts[1].lower() if len(parts) > 1 else ""
    name  = parts[2].strip() if len(parts) > 2 else ""

    # The session's CURRENT workspace is what save/load act on — not the fixed
    # default — so these follow the user wherever /workspace use points them.
    current_ws = _session.workspace if _session is not None else DEFAULT_WORKSPACE

    # ── /workspace use <path> — switch the active folder ──────────────────────
    if sub == "use":
        if _session is None:
            console.print(f"\n  [{C_ERR}]Session not ready.[/{C_ERR}]\n")
            return
        if not name:
            console.print("\n  [dim]Usage: [cyan]/workspace use <path>[/cyan]  "
                          "(a number from [cyan]/workspace recent[/cyan] also works)[/dim]\n")
            return
        # Allow choosing by number from the recent list
        target = name
        if name.isdigit():
            from core.workspace_session import WorkspaceSession
            recent = WorkspaceSession.recent_workspaces()
            idx = int(name) - 1
            if 0 <= idx < len(recent):
                target = recent[idx]
            else:
                console.print(f"\n  [{C_ERR}]No recent workspace #{name}.[/{C_ERR}]\n")
                return
        try:
            _session.switch_workspace(target)
            console.print(
                f"\n  ✓ Workspace → [cyan]{_session.workspace}[/cyan]  "
                f"[dim](chat: {_session.chat_name})[/dim]\n"
            )
        except ValueError as exc:
            console.print(f"\n  [{C_ERR}]{exc}[/{C_ERR}]\n")
        return

    # ── /workspace recent — list recently-used folders, numbered ──────────────
    if sub == "recent":
        from core.workspace_session import WorkspaceSession
        recent = WorkspaceSession.recent_workspaces()
        if not recent:
            console.print("\n  [dim]No recent workspaces yet.[/dim]\n")
            return
        console.print("\n  [dim]Recent workspaces — [cyan]/workspace use <number>[/cyan]:[/dim]")
        for i, p in enumerate(recent, 1):
            marker = " ◀ active" if _session is not None and str(_session.workspace) == p else ""
            console.print(f"    [cyan]{i}[/cyan]  {p}{marker}")
        console.print()
        return

    saves_dir = Path("./logs/workspaces")
    saves_dir.mkdir(parents=True, exist_ok=True)

    if sub == "save":
        if not name:
            console.print("\n  [dim]Usage: [cyan]/workspace save <name>[/cyan][/dim]\n")
            return
        zip_path = saves_dir / f"{name}.zip"
        try:
            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                for f in current_ws.rglob("*"):
                    if f.is_file():
                        zf.write(f, f.relative_to(current_ws))
            files = len(list(current_ws.rglob("*")))
            console.print(f"\n  ✓ Saved workspace [cyan]{name}[/cyan] ({files} files → {zip_path})\n")
        except Exception as exc:
            console.print(f"\n  [{C_ERR}]Save failed:[/{C_ERR}] {exc}\n")

    elif sub == "load":
        if not name:
            console.print("\n  [dim]Usage: [cyan]/workspace load <name>[/cyan][/dim]\n")
            return
        zip_path = saves_dir / f"{name}.zip"
        if not zip_path.exists():
            saved = [p.stem for p in saves_dir.glob("*.zip")]
            console.print(
                f"\n  [{C_ERR}]Not found:[/{C_ERR}] {name}  "
                f"(available: {', '.join(saved) or 'none'})\n"
            )
            return
        try:
            import shutil
            shutil.rmtree(current_ws, ignore_errors=True)
            current_ws.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(zip_path) as zf:
                zf.extractall(current_ws)
            console.print(f"\n  ✓ Loaded workspace [cyan]{name}[/cyan] into {current_ws}\n")
        except Exception as exc:
            console.print(f"\n  [{C_ERR}]Load failed:[/{C_ERR}] {exc}\n")

    elif sub == "list":
        saves = list(saves_dir.glob("*.zip"))
        if not saves:
            console.print("\n  [dim]No saved workspaces. Use [cyan]/workspace save <name>[/cyan][/dim]\n")
        else:
            console.print()
            for z in sorted(saves):
                size = z.stat().st_size // 1024
                console.print(f"  📦 [cyan]{z.stem}[/cyan]  [{C_DIM}]{size} KB[/{C_DIM}]")
            console.print()

    else:
        console.print(
            "\n  [dim]Usage:[/dim]  "
            "[cyan]/workspace use <path>[/cyan]  "
            "[cyan]/workspace recent[/cyan]  "
            "[cyan]/workspace save <name>[/cyan]  "
            "[cyan]/workspace load <name>[/cyan]  "
            "[cyan]/workspace list[/cyan]\n"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# /chat — multiple conversations per workspace
# ═══════════════════════════════════════════════════════════════════════════════

async def handle_chat_cmd(cmd: str) -> None:
    """Handle /chat list|new|switch|delete|rename — separate conversation
    threads scoped to the current workspace folder."""
    if _session is None:
        console.print(f"\n  [{C_ERR}]Session not ready.[/{C_ERR}]\n")
        return

    parts = cmd.strip().split(None, 2)
    sub   = parts[1].lower() if len(parts) > 1 else "list"
    arg   = parts[2].strip() if len(parts) > 2 else ""

    if sub == "list":
        chats = _session.list_chats()
        console.print(f"\n  [dim]Chats in [cyan]{_session.workspace.name or _session.workspace}[/cyan]:[/dim]")
        if not chats:
            console.print("    [dim](none yet)[/dim]")
        for c in chats:
            marker = " ◀ active" if c.name == _session.chat_name else ""
            console.print(f"    💬 [cyan]{c.name}[/cyan]  [{C_DIM}]{c.messages} msg[/{C_DIM}]{marker}")
        console.print(f"\n  [dim]/chat new <name> · /chat switch <name> · "
                      f"/chat rename <name> · /chat delete <name>[/dim]\n")

    elif sub == "new":
        if not arg:
            console.print("\n  [dim]Usage: [cyan]/chat new <name>[/cyan][/dim]\n")
            return
        try:
            _session.new_chat(arg)
            console.print(f"\n  ✓ New chat [cyan]{_session.chat_name}[/cyan] (empty) — you're now in it.\n")
        except ValueError as exc:
            console.print(f"\n  [{C_ERR}]{exc}[/{C_ERR}]\n")

    elif sub == "switch":
        if not arg:
            console.print("\n  [dim]Usage: [cyan]/chat switch <name>[/cyan][/dim]\n")
            return
        try:
            _session.switch_chat(arg)
            n = len(_session.history())
            console.print(f"\n  ✓ Switched to [cyan]{_session.chat_name}[/cyan] "
                          f"[dim]({n} message(s) of history)[/dim]\n")
        except ValueError as exc:
            console.print(f"\n  [{C_ERR}]{exc}[/{C_ERR}]\n")

    elif sub == "delete":
        if not arg:
            console.print("\n  [dim]Usage: [cyan]/chat delete <name>[/cyan][/dim]\n")
            return
        try:
            _session.delete_chat(arg)
            console.print(f"\n  ✓ Deleted chat [cyan]{arg}[/cyan]  "
                          f"[dim](now in: {_session.chat_name})[/dim]\n")
        except ValueError as exc:
            console.print(f"\n  [{C_ERR}]{exc}[/{C_ERR}]\n")

    elif sub == "rename":
        # /chat rename <newname>  → renames the CURRENT chat
        if not arg:
            console.print("\n  [dim]Usage: [cyan]/chat rename <new-name>[/cyan]  (renames the current chat)[/dim]\n")
            return
        try:
            old = _session.chat_name
            _session.rename_chat(old, arg)
            console.print(f"\n  ✓ Renamed [cyan]{old}[/cyan] → [cyan]{_session.chat_name}[/cyan]\n")
        except ValueError as exc:
            console.print(f"\n  [{C_ERR}]{exc}[/{C_ERR}]\n")

    else:
        console.print(
            "\n  [dim]Usage:[/dim]  "
            "[cyan]/chat list[/cyan]  [cyan]/chat new <name>[/cyan]  "
            "[cyan]/chat switch <name>[/cyan]  [cyan]/chat rename <name>[/cyan]  "
            "[cyan]/chat delete <name>[/cyan]\n"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN LOOP
# ═══════════════════════════════════════════════════════════════════════════════

async def main() -> None:
    # Raw logs go to a file — the terminal shows only the friendly live UI.
    # (The ThinkingPanel and LiveDisplay render pipeline activity instead.)
    from loguru import logger as _logger
    _logger.remove()
    Path("./logs").mkdir(exist_ok=True)
    _logger.add("logs/vibeai_cli.log", level="DEBUG", rotation="5 MB", retention=3)

    show_banner()

    # ── Initialise ────────────────────────────────────────────────────────────
    manager_name = "VibeAI Free Team"
    try:
        from tools.manager_fallback import fallback_chain
        manager_name = fallback_chain.active_name

        try:
            from manager.claude_manager import manager
            from core.state import state
            await state.init()
            await manager.startup()
        except Exception:
            pass

    except Exception as exc:
        console.print(f"[red]Startup error: {exc}[/red]")
        console.print("[dim]Make sure you've set up your .env file. Run: python main.py check[/dim]")
        return

    # ── Workspace + chat session ──────────────────────────────────────────────
    # Start in the launch workspace (VIBE_WORKSPACE / --workspace / default),
    # on the folder's "main" chat. /workspace use and /chat switch it at runtime.
    global _session
    try:
        from core.workspace_session import WorkspaceSession
        from tools.agent_tools import DEFAULT_WORKSPACE
        _session = WorkspaceSession(_LAUNCH_WORKSPACE or DEFAULT_WORKSPACE)
    except Exception as exc:
        console.print(f"[yellow]Workspace session unavailable ({str(exc)[:60]}) — using default folder.[/yellow]")

    show_intro(manager_name)

    # ── Input session ─────────────────────────────────────────────────────────
    # prompt_toolkit needs a real console (Win32 screen buffer / TTY). When
    # stdin is piped or the terminal is dumb (CI, `echo ... | vibeai`, an IDE's
    # embedded console), it crashes with NoConsoleScreenBufferError. Fall back
    # to plain input() in that case so the CLI is scriptable, not just
    # interactive. VIBE_PLAIN_INPUT=1 forces the fallback for testing.
    import sys as _sys
    _use_pt = (
        HAS_PROMPT_TOOLKIT
        and os.getenv("VIBE_PLAIN_INPUT") != "1"
        and _sys.stdin is not None and _sys.stdin.isatty()
    )
    if _use_pt:
        from cli_completer import SlashCommandCompleter
        session = PromptSession(
            history=InMemoryHistory(),
            completer=SlashCommandCompleter(),
            complete_while_typing=True,
            style=PTStyle.from_dict({
                "prompt": "ansigreen bold",
                "":       "ansiwhite",
                "completion-menu.completion":          "bg:#1a1a1a #87d7ff",
                "completion-menu.completion.current":  "bg:#00afd7 #000000",
                "completion-menu.meta.completion":     "bg:#1a1a1a #808080",
                "completion-menu.meta.completion.current": "bg:#00afd7 #303030",
            }),
        )
        async def _get_input() -> str:
            return await session.prompt_async([("class:prompt", " You › ")])
    else:
        async def _get_input() -> str:
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(None, lambda: input(" You › "))

    # ── REPL ──────────────────────────────────────────────────────────────────
    while True:
        try:
            user_input = await _get_input()
        except (KeyboardInterrupt, EOFError):
            console.print()
            console.print(
                f"\n  [{C_AI}]VibeAI:[/{C_AI}] "
                f"[dim]Use [cyan]exit[/cyan] to quit.[/dim]\n"
            )
            continue

        user_input = user_input.strip()
        if not user_input:
            continue

        # Built-in commands and /model switch
        cmd_lower = user_input.lower()
        if cmd_lower in ("help", "status", "clear", "workspace", "models",
                         "exit", "quit", "q", "bye"):
            await handle_command(user_input)
            continue

        if user_input.startswith("/model") or user_input.lower().startswith("/routing"):
            await handle_command(user_input)
            continue

        # Manager mode: /m or /mg prefix
        if user_input.startswith("/m ") or user_input.startswith("/mg "):
            task = re.sub(r"^/m[g]?\s+", "", user_input, count=1)
            console.print(
                f"\n  [{C_DIM}]── {time.strftime('%H:%M:%S')} ─── manager ─────────────[/{C_DIM}]"
            )
            await handle_manager_task(task)
            continue

        # /think — direct VibeMind call (MoA + debate + verification)
        if user_input.startswith("/think "):
            problem = user_input[7:].strip()
            await handle_think(problem)
            continue

        # /ceo — on-demand aggregate oversight report across Council + teams
        if cmd_lower == "/ceo":
            await handle_ceo_report()
            continue

        # /vision — VisionTeam pipeline for video/image files
        if user_input.lower().startswith("/vision "):
            rest  = user_input[8:].strip()
            # Optional: /vision <path> -- <instruction>
            if " -- " in rest:
                file_part, instr = rest.split(" -- ", 1)
            else:
                file_part, instr = rest, ""
            await handle_vision(file_part.strip(), instr.strip())
            continue

        # /context — set persistent project description
        if user_input.lower().startswith("/context"):
            parts = user_input.split(None, 1)
            if len(parts) < 2:
                if _project_context:
                    console.print(f"\n  Current context: [cyan]{_project_context}[/cyan]\n")
                else:
                    console.print("\n  [dim]No context set. Usage: [cyan]/context <description>[/cyan][/dim]\n")
            else:
                _project_context = parts[1].strip()
                console.print(f"\n  ✓ Project context set: [cyan]{_project_context[:80]}[/cyan]\n")
            continue

        # /workspace use|recent|save|load|list
        if user_input.lower().startswith("/workspace"):
            await handle_workspace_cmd(user_input)
            continue

        # /chat — multiple conversations per workspace
        if cmd_lower == "/chat" or cmd_lower.startswith("/chat "):
            await handle_chat_cmd(user_input)
            continue

        # /voice — speak a task instead of typing it.
        #   /voice            record 8s from the microphone
        #   /voice 15         record 15s
        #   /voice <file>     transcribe an existing audio file
        if cmd_lower == "/voice" or cmd_lower.startswith("/voice "):
            arg = user_input[6:].strip()
            try:
                from tools.voice_input import record_microphone, transcribe_file
                if arg and not arg.isdigit():
                    audio_path = arg
                else:
                    secs = int(arg) if arg.isdigit() else 8
                    console.print(f"\n  [cyan]● Recording {secs}s — speak now...[/cyan]")
                    audio_path = record_microphone(seconds=secs)
                console.print("  [dim]Transcribing (Groq Whisper)...[/dim]")
                text = await transcribe_file(audio_path)
                if not text:
                    console.print("  [yellow]Heard nothing intelligible — try again closer to the mic.[/yellow]\n")
                    continue
                console.print(f"\n  [bold]You said:[/bold] [cyan]{text}[/cyan]\n")
                console.print(
                    f"\n  [{C_DIM}]── {time.strftime('%H:%M:%S')} ─── agent (voice) ──────[/{C_DIM}]"
                )
                await handle_agent_task(text, model_id=_active_model)
            except RuntimeError as exc:
                console.print(f"\n  [yellow]{exc}[/yellow]\n")
            except Exception as exc:
                console.print(f"\n  [red]Voice input failed: {str(exc)[:120]}[/red]\n")
            continue

        # /image <prompt> — standalone "generate me a picture" feature.
        # Distinct from the design_asset tool the coding agent uses
        # internally for website assets: this is the direct path a user
        # reaches for a bare "generate an image of X" ask, no coding
        # project or manager review pipeline involved.
        if cmd_lower == "/image" or cmd_lower.startswith("/image "):
            prompt = user_input[6:].strip()
            if not prompt:
                console.print("\n  [dim]Usage: [cyan]/image <description>[/cyan][/dim]\n")
                continue
            console.print(f"\n  [cyan]● Generating image...[/cyan] [dim]{prompt[:70]}[/dim]")
            from tools.image_gen import generate_image
            result = await generate_image(prompt)
            if result.ok:
                console.print(f"  [green]✓ saved:[/green] {result.path}\n")
            else:
                console.print(f"  [red]Image generation failed:[/red] {result.error}\n")
            continue

        # Agent mode (default)
        console.print(
            f"\n  [{C_DIM}]── {time.strftime('%H:%M:%S')} ─── agent ──────────────[/{C_DIM}]"
        )
        await handle_agent_task(user_input, model_id=_active_model)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _fmt_args(tool_name: str, args: dict) -> str:
    if tool_name in ("create_file", "read_file", "edit_file", "delete_file"):
        return args.get("path", "")
    if tool_name == "bash":
        cmd = args.get("command", "")
        return cmd[:60] + ("…" if len(cmd) > 60 else "")
    if tool_name == "search_web":
        return args.get("query", "")[:50]
    if tool_name == "fetch_url":
        return args.get("url", "")[:50]
    return str(args)[:50]


try:
    from tools.agent_tools import DEFAULT_WORKSPACE
except ImportError:
    DEFAULT_WORKSPACE = Path("./workspace")


# ── Entry point ───────────────────────────────────────────────────────────────

def run() -> None:
    """Console-script entry point (`vibeai` after `pip install`).

    Supports:
        vibeai                       # start in the default workspace
        vibeai --workspace ./myapp   # start in a chosen folder
        vibeai ./myapp               # positional shorthand for the same
    """
    global _LAUNCH_WORKSPACE
    import argparse
    parser = argparse.ArgumentParser(
        prog="vibeai",
        description="VibeAI — multi-agent AI coding team in your terminal.",
    )
    parser.add_argument("workspace", nargs="?", default=None,
                        help="folder to work in (default: ./workspace or $VIBE_WORKSPACE)")
    parser.add_argument("--workspace", "-w", dest="workspace_flag", default=None,
                        help="folder to work in (same as the positional argument)")
    args = parser.parse_args()
    chosen = args.workspace_flag or args.workspace
    if chosen:
        _LAUNCH_WORKSPACE = Path(chosen).expanduser().resolve()
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        console.print("\n  [dim]Interrupted.[/dim]")


if __name__ == "__main__":
    run()
