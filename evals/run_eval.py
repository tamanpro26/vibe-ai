"""
evals/run_eval.py
Formalizes the manual "2-question exam" (one design task, one backend/
critical-thinking task) into a repeatable, scored script instead of re-typing
prompts into the CLI and grading the output by eye each time.

What this does and doesn't do, on purpose:
  - Runs the real agent loop end-to-end (`core.agent_loop.run_agent`) against
    each task in `evals/tasks.py`, in a clean workspace per run.
  - Grades everything that CAN be graded mechanically: build/test success and
    the same zero-token verifier battery the agent uses on itself
    (`core.verifiers.run_all`) — broken imports, unstyled classNames,
    placeholder content, dead image links, Python code smells.
  - For design tasks, also runs the same vision-QA pass the agent runs on
    itself (screenshots -> vision model -> concrete-defects-only critique),
    reported but NOT part of the pass/fail gate — it's one model's opinion of
    pixels, not ground truth, and treating it as a scored benchmark would
    repeat the earlier mistake of presenting a soft signal as a hard number.
  - Does NOT invent a weighted "quality score out of 10". A build that fails
    or ships a broken import is an objective fail; anything past that
    (does the copy read well, is the palette actually moody) still needs a
    human or a separate LLM-judge pass to read the transcript and the files —
    this script's job is to make that review fast, not to replace it.

Usage:
    python -m evals.run_eval                         # both tasks, default model
    python -m evals.run_eval --model glm_47_cerebras
    python -m evals.run_eval --task backend_rate_limiter
    python -m evals.run_eval --no-vision              # skip the vision-QA pass
"""
from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.agent_loop import AgentLoop, run_agent
from core.verifiers import run_all as run_verifiers
from evals.tasks import TASKS, EvalTask

RUNS_DIR = Path(__file__).resolve().parent / "runs"
_BUILD_TIMEOUT_S = 180


@dataclass
class TaskReport:
    slug:              str
    category:          str
    model_id:          str
    passed:            bool
    build_ok:          bool | None       # None = nothing buildable/testable was found
    build_output_tail: str
    verifier_findings: list[str]
    vision_findings:   list[str]
    iterations:        int
    total_ms:          float
    files_created:     int
    files_edited:      int
    error:             str = ""


def _find_project_root(workspace: Path) -> Path:
    """First dir (workspace itself or a subdir) containing package.json or
    a .py file — mirrors the heuristic the agent loop uses at runtime."""
    if (workspace / "package.json").exists():
        return workspace
    for child in sorted(workspace.iterdir()):
        if child.is_dir() and (child / "package.json").exists():
            return child
    if any(workspace.glob("*.py")) or any(workspace.glob("**/*.py")):
        return workspace
    return workspace


def _run_build_or_tests(project_root: Path) -> tuple[bool | None, str]:
    """Returns (ok, output_tail). ok=None when there's nothing to run."""
    if (project_root / "package.json").exists():
        try:
            subprocess.run(
                "npm install", shell=True, cwd=project_root,
                capture_output=True, text=True, timeout=_BUILD_TIMEOUT_S,
            )
            proc = subprocess.run(
                "npm run build", shell=True, cwd=project_root,
                capture_output=True, text=True, timeout=_BUILD_TIMEOUT_S,
            )
            ok = proc.returncode == 0
            return ok, (proc.stdout + proc.stderr)[-1500:]
        except subprocess.TimeoutExpired:
            return False, "build timed out"
        except Exception as exc:
            return False, f"build check errored: {exc}"

    has_tests = any(project_root.glob("tests/test_*.py")) or any(project_root.glob("test_*.py"))
    if has_tests:
        try:
            proc = subprocess.run(
                [sys.executable, "-m", "pytest", "-q"], cwd=project_root,
                capture_output=True, text=True, timeout=_BUILD_TIMEOUT_S,
            )
            ok = proc.returncode == 0
            return ok, (proc.stdout + proc.stderr)[-1500:]
        except subprocess.TimeoutExpired:
            return False, "pytest timed out"
        except Exception as exc:
            return False, f"pytest errored: {exc}"

    return None, ""


async def _run_vision_qa(project_root: Path) -> list[str]:
    try:
        loop = AgentLoop(workspace=project_root)
        return await loop._vision_qa("")
    except Exception as exc:
        return [f"[vision-qa skipped: {exc}]"]


async def run_task(task: EvalTask, model_id: str, run_dir: Path, use_vision: bool) -> TaskReport:
    workspace = run_dir / task.slug
    workspace.mkdir(parents=True, exist_ok=True)
    for rel_path, content in task.setup_files.items():
        target = workspace / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    print(f"  -> running {task.slug} ({task.category}) on {model_id} ..."
          + (f" [{len(task.setup_files)} seed file(s)]" if task.setup_files else ""))

    t0 = time.perf_counter()
    try:
        result = await run_agent(
            task=task.prompt, model_id=model_id,
            task_type=task.task_type, workspace=str(workspace),
        )
    except Exception as exc:
        return TaskReport(
            slug=task.slug, category=task.category, model_id=model_id,
            passed=False, build_ok=None, build_output_tail="",
            verifier_findings=[], vision_findings=[],
            iterations=0, total_ms=(time.perf_counter() - t0) * 1000,
            files_created=0, files_edited=0, error=str(exc),
        )

    project_root = _find_project_root(workspace)
    verifier_findings = await run_verifiers(project_root)
    build_ok, build_tail = _run_build_or_tests(project_root)
    vision_findings: list[str] = []
    if use_vision and task.category == "design" and build_ok:
        vision_findings = await _run_vision_qa(project_root)

    # A backend task with build_ok=None means no test_*.py file was found at
    # all — for this category that's a missing deliverable, not a no-op, so
    # it must not silently pass the gate the way "no package.json to build"
    # correctly does for a task that never asked for one. Caught live: a run
    # that wrote the module plus an empty tests/__init__.py stub (no actual
    # test file) finished in 2 iterations and would otherwise have "passed".
    if task.category == "backend" and build_ok is None:
        build_ok = False
        build_tail = "no test_*.py file found — task asked for a pytest suite"

    passed = (build_ok is not False) and not verifier_findings

    return TaskReport(
        slug=task.slug, category=task.category, model_id=model_id,
        passed=passed, build_ok=build_ok, build_output_tail=build_tail,
        verifier_findings=verifier_findings, vision_findings=vision_findings,
        iterations=result.iterations, total_ms=result.total_ms,
        files_created=len(result.files_created), files_edited=len(result.files_edited),
    )


def _print_summary(reports: list[TaskReport]) -> None:
    print("\n" + "=" * 72)
    print(f"{'TASK':<24} {'PASS':<6} {'BUILD':<8} {'FINDINGS':<10} {'ITERS':<6} {'TIME(s)'}")
    print("-" * 72)
    for r in reports:
        build_s = "n/a" if r.build_ok is None else ("ok" if r.build_ok else "FAIL")
        print(f"{r.slug:<24} {'YES' if r.passed else 'NO':<6} {build_s:<8} "
              f"{len(r.verifier_findings):<10} {r.iterations:<6} {r.total_ms/1000:.1f}")
    print("=" * 72)
    for r in reports:
        if r.error:
            print(f"[{r.slug}] ERROR: {r.error}")
        for f in r.verifier_findings:
            print(f"[{r.slug}] {f}")
        for f in r.vision_findings:
            print(f"[{r.slug}] {f}")
    n_pass = sum(r.passed for r in reports)
    print(f"\n{n_pass}/{len(reports)} tasks passed the mechanical gate "
          f"(build/tests succeed AND zero deterministic verifier findings).")
    print("Vision-QA findings and subjective quality (copy, layout taste) are "
          "reported above but NOT part of the pass/fail gate — read the "
          "workspace + transcript for those.")


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default="glm_47_cerebras", help="model_id to grade")
    parser.add_argument("--task", default=None, help="run only this task slug")
    parser.add_argument("--no-vision", action="store_true", help="skip the vision-QA pass")
    args = parser.parse_args()

    tasks = [t for t in TASKS if args.task is None or t.slug == args.task]
    if not tasks:
        print(f"no task matches slug {args.task!r}; available: {[t.slug for t in TASKS]}")
        sys.exit(2)

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = RUNS_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    print(f"eval run {run_id} — model={args.model} — {len(tasks)} task(s)")

    reports = []
    for task in tasks:
        reports.append(await run_task(task, args.model, run_dir, use_vision=not args.no_vision))

    report_path = run_dir / "report.json"
    report_path.write_text(
        json.dumps([asdict(r) for r in reports], indent=2), encoding="utf-8"
    )
    print(f"\nfull report written to {report_path}")

    _print_summary(reports)
    sys.exit(0 if all(r.passed for r in reports) else 1)


if __name__ == "__main__":
    asyncio.run(main())
