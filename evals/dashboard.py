"""
evals/dashboard.py
Aggregates every historical run under evals/runs/*/report.json into a
pass-rate/timing summary — a benchmark view grounded in this project's own
real workloads (the tasks in evals/tasks.py), not a synthetic leaderboard
like MMLU. Directly extends the eval harness built earlier rather than
starting a new "benchmark" concept from scratch.

Deliberately NOT a live-updating public leaderboard server: that's a real
product decision (hosting, auth, who can see what) this script doesn't make
for you. It reads what's already on disk and renders it two ways:
  - a terminal table (rich), for `python -m evals.dashboard`
  - a self-contained static HTML file, for sharing without standing up a server

Usage:
    python -m evals.dashboard                    # terminal table
    python -m evals.dashboard --html out.html     # also write a static page
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

RUNS_DIR = Path(__file__).resolve().parent / "runs"


@dataclass
class TaskStats:
    slug:          str
    model_id:      str
    runs:          int   = 0
    passed:        int   = 0
    total_iters:   int   = 0
    total_ms:      float = 0.0
    last_status:   str   = "?"       # "PASS" | "FAIL" | "ERROR"
    last_run_id:   str   = ""
    findings_seen: int   = 0         # cumulative deterministic-verifier findings

    @property
    def pass_rate(self) -> float:
        return (self.passed / self.runs * 100) if self.runs else 0.0

    @property
    def avg_iterations(self) -> float:
        return (self.total_iters / self.runs) if self.runs else 0.0

    @property
    def avg_seconds(self) -> float:
        return (self.total_ms / self.runs / 1000) if self.runs else 0.0


def _iter_reports() -> list[tuple[str, list[dict]]]:
    """(run_id, report_entries) for every run that actually finished — a run
    interrupted mid-execution (no report.json written) is skipped, not
    counted as a failure it never actually recorded."""
    out = []
    if not RUNS_DIR.exists():
        return out
    for run_dir in sorted(RUNS_DIR.iterdir()):
        report = run_dir / "report.json"
        if not report.exists():
            continue
        try:
            data = json.loads(report.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue  # a corrupt report must not crash the whole dashboard
        if isinstance(data, list):
            out.append((run_dir.name, data))
    return out


def aggregate() -> dict[tuple[str, str], TaskStats]:
    """Keyed by (task_slug, model_id) so the same task run on different
    models is compared side by side, not blended into one misleading average."""
    stats: dict[tuple[str, str], TaskStats] = {}
    for run_id, entries in _iter_reports():
        for e in entries:
            slug  = e.get("slug", "?")
            model = e.get("model_id", "?")
            key   = (slug, model)
            s = stats.setdefault(key, TaskStats(slug=slug, model_id=model))
            s.runs        += 1
            s.passed      += 1 if e.get("passed") else 0
            s.total_iters += e.get("iterations", 0) or 0
            s.total_ms    += e.get("total_ms", 0) or 0
            s.findings_seen += len(e.get("verifier_findings") or [])
            s.last_status = "PASS" if e.get("passed") else ("ERROR" if e.get("error") else "FAIL")
            s.last_run_id = run_id
    return stats


def render_terminal(stats: dict[tuple[str, str], TaskStats]) -> None:
    from rich.console import Console
    from rich.table import Table

    # Windows consoles default to cp1252, which can't encode an em-dash —
    # verified live (2026-07-09, same class of bug as core/collab_viz.py's
    # encoding note in DECISIONS.md): a fancy unicode char in a string meant
    # for the terminal silently mangled the whole title. Plain hyphen here;
    # the HTML export is free to use real typography since it's not subject
    # to console codepage limits.
    console = Console(width=140)
    if not stats:
        console.print("[dim]No completed eval runs found under evals/runs/ yet - "
                       "run `python -m evals.run_eval` first.[/dim]")
        return

    table = Table(title="VibeAI Eval Benchmark - real workloads, not synthetic MMLU")
    # no_wrap + overflow="fold" on the identifier columns: the default
    # truncates long task/model names to fit console width (e.g.
    # "backend_cli_csv_tool" -> "backend_..."), which made every row look
    # identical and useless — verified live, this is what "Task"/"Model"
    # looked like before the fix. Folding wraps instead of losing the text.
    table.add_column("Task", overflow="fold")
    table.add_column("Model", overflow="fold")
    table.add_column("Runs", justify="right")
    table.add_column("Pass rate", justify="right")
    table.add_column("Avg iters", justify="right")
    table.add_column("Avg time", justify="right")
    table.add_column("Last result", overflow="fold")

    for (slug, model), s in sorted(stats.items()):
        color = "green" if s.pass_rate == 100 else ("yellow" if s.pass_rate > 0 else "red")
        last_color = {"PASS": "green", "FAIL": "yellow", "ERROR": "red"}.get(s.last_status, "white")
        table.add_row(
            slug, model, str(s.runs),
            f"[{color}]{s.pass_rate:.0f}%[/{color}]",
            f"{s.avg_iterations:.1f}",
            f"{s.avg_seconds:.1f}s",
            f"[{last_color}]{s.last_status}[/{last_color}] ({s.last_run_id})",
        )
    console.print(table)


def render_html(stats: dict[tuple[str, str], TaskStats], out_path: Path) -> None:
    rows = []
    for (slug, model), s in sorted(stats.items()):
        color = "#2e7d32" if s.pass_rate == 100 else ("#b8860b" if s.pass_rate > 0 else "#c62828")
        rows.append(
            f"<tr><td>{slug}</td><td>{model}</td><td>{s.runs}</td>"
            f"<td style='color:{color};font-weight:600'>{s.pass_rate:.0f}%</td>"
            f"<td>{s.avg_iterations:.1f}</td><td>{s.avg_seconds:.1f}s</td>"
            f"<td>{s.last_status} ({s.last_run_id})</td></tr>"
        )
    html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>VibeAI Eval Benchmark</title>
<style>
  body {{ font-family: -apple-system, Segoe UI, sans-serif; background:#0d1121; color:#e8eaf0; padding:2rem; }}
  h1 {{ font-weight: 600; }}
  p.sub {{ color: #9aa3b5; margin-top: -0.5rem; }}
  table {{ border-collapse: collapse; width: 100%; margin-top: 1.5rem; }}
  th, td {{ text-align: left; padding: 0.5rem 0.9rem; border-bottom: 1px solid #262b40; }}
  th {{ color: #9aa3b5; font-weight: 500; font-size: 0.85rem; text-transform: uppercase; }}
  tr:hover {{ background: #161b2e; }}
</style></head>
<body>
  <h1>VibeAI Eval Benchmark</h1>
  <p class="sub">Real workloads from evals/tasks.py, not synthetic MMLU-style scores.</p>
  <table>
    <tr><th>Task</th><th>Model</th><th>Runs</th><th>Pass rate</th>
        <th>Avg iterations</th><th>Avg time</th><th>Last result</th></tr>
    {"".join(rows) if rows else "<tr><td colspan=7>No completed eval runs yet.</td></tr>"}
  </table>
</body></html>"""
    out_path.write_text(html, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--html", type=str, default=None,
                        help="also write a static HTML dashboard to this path")
    args = parser.parse_args()

    stats = aggregate()
    render_terminal(stats)
    if args.html:
        render_html(stats, Path(args.html))
        print(f"\nStatic dashboard written to {args.html}")


if __name__ == "__main__":
    main()
