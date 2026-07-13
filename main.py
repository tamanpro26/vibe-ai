"""
main.py — VibeAI entry point

  python main.py              → interactive terminal CLI  (default)
  python main.py serve        → starts the API server on :8000
  python main.py check        → validates API keys and model config
  python main.py run "…"      → one-shot prompt, prints result and exits
  python main.py cli          → explicit alias for the interactive CLI
"""
from __future__ import annotations

import asyncio
import os
import sys

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# ── Default: no arguments → launch the terminal CLI ──────────────────────────
if len(sys.argv) == 1:
    from cli import main as _cli_main
    try:
        asyncio.run(_cli_main())
    except KeyboardInterrupt:
        from rich.console import Console
        Console().print("\n  [dim]Interrupted.[/dim]")
    sys.exit(0)

# ── Subcommands ───────────────────────────────────────────────────────────────
import typer
import uvicorn
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table

app     = typer.Typer(help="VibeAI — 31-Model Multi-Agent AI", add_completion=False)
console = Console()


@app.command()
def serve():
    """Start the FastAPI server on :8000."""
    from config.settings import settings
    console.print(f"[cyan]VibeAI server[/cyan] → http://localhost:{settings.api_port}")
    uvicorn.run(
        "api.server:app",
        host=settings.api_host,
        port=settings.api_port,
        log_level=settings.log_level.lower(),
    )


@app.command()
def run(prompt: str = typer.Argument(..., help="Prompt to send to the AI")):
    """Send one prompt, print the result, and exit."""
    async def _go():
        from core.state import state
        from manager.claude_manager import manager
        await state.init()
        await manager.startup()
        r = await manager.handle_user_request(prompt)
        console.print(Panel(Markdown(r), title="[cyan]VibeAI[/cyan]", border_style="cyan"))

    asyncio.run(_go())


@app.command()
def check():
    """Validate API keys and show which models are available."""
    from config.settings import settings

    console.print("\n[bold cyan]VibeAI — Configuration Check[/bold cyan]\n")

    table = Table(show_header=True, header_style="bold cyan")
    table.add_column("API Key",    style="bold")
    table.add_column("Status",     justify="center")
    table.add_column("Used for",   style="dim")

    checks = [
        ("ANTHROPIC_API_KEY",  settings.anthropic_api_key,  "Primary manager (optional — Free Team covers if missing)"),
        ("GEMINI_API_KEY",     settings.gemini_api_key,     "Brain / Vision / Free-team Dispatcher"),
        ("GROQ_API_KEY",       settings.groq_api_key,       "Brain / Code / Vision / Free-team Reviewer"),
        ("CEREBRAS_API_KEY",   settings.cerebras_api_key,   "Brain / Code / Router / Free-team Synthesizer"),
        ("OPENROUTER_API_KEY", settings.openrouter_api_key, "Prompt / Code / Vision / Router teams"),
        ("HF_TOKEN",           settings.hf_token,           "Design team — FLUX image generation"),
    ]
    for name, val, note in checks:
        status = "[green]✓  set[/green]" if val else "[red]✗ missing[/red]"
        table.add_row(name, status, note)

    console.print(table)
    console.print()

    required = [
        ("GEMINI_API_KEY",     settings.gemini_api_key),
        ("GROQ_API_KEY",       settings.groq_api_key),
        ("CEREBRAS_API_KEY",   settings.cerebras_api_key),
        ("OPENROUTER_API_KEY", settings.openrouter_api_key),
    ]
    missing = [k for k, v in required if not v]
    if missing:
        console.print(
            f"[yellow]⚠  Missing:[/yellow] {', '.join(missing)}\n"
            "  The Free Manager Team needs GEMINI + GROQ + CEREBRAS to operate.\n"
            "  Add them to [dim].env[/dim] — all are free, no credit card required.\n"
        )
    else:
        console.print("[green]✓  All required keys present — VibeAI is ready.[/green]\n")


@app.command()
def cli():
    """Launch the interactive terminal CLI (same as running with no args)."""
    from cli import main as cli_main
    try:
        asyncio.run(cli_main())
    except KeyboardInterrupt:
        console.print("\n  [dim]Interrupted.[/dim]")


if __name__ == "__main__":
    app()
