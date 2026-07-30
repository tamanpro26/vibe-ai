"""
core/ambient.py -- overnight ambient runner: burn expiring free quota.

Concept ported from jcode v0.54.4 (MIT), adapted to VibeAI's actual #1
constraint. VibeAI's daily free quotas (Groq RPD, Cerebras 1M tokens/day,
Gemini RPD) reset at midnight -- unused quota is free compute evaporating.
Ambient mode converts "rate limits" into a scheduling problem: run
consolidation / recalibration / queued evals overnight, down to a reserve
floor, so a morning session never starts starved.

Non-negotiable rules (jcode "only one ambient instance ever" is a CORRECTNESS
rule, not a preference):
  * SINGLE instance, lockfile-guarded, within a hard time window.
  * Per-provider quota gate with a RESERVE FLOOR (keep e.g. 20% of each daily
    limit untouched).
  * Jobs are STEP-RESUMABLE: each step() is minutes-sized and journaled, so a
    crash or window-end loses one step, not a night.
  * LOCAL-ONLY safety: ambient does read/compute/local-write only. Anything
    that leaves the sandbox (git push, publish, external POST) goes to a
    review queue the user confirms in the morning -- ambient never does it.
  * Morning report: one markdown file summarising what ran, what it found,
    quota spent, and pending permissions.

This module is the RUNNER + framework. The concrete jobs (memory
consolidation, passport recalibration, canary battery, queued k-run evals)
register as AmbientJob subclasses; the eval/canary logic already exists as
scripts, ambient just schedules it.
"""
from __future__ import annotations

import json
import os
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from loguru import logger


# ── Jobs ────────────────────────────────────────────────────────────────────

class AmbientJob(ABC):
    """A resumable, idempotent overnight job. Priority = list order in the
    runner. Each step() must be small (minutes) and journal-safe."""
    name: str = "job"
    est_cost: int = 1          # rough per-step cost units for the quota gate
    provider: str = "groq"     # which provider's quota this job spends

    @abstractmethod
    def has_work(self) -> bool: ...

    @abstractmethod
    async def step(self) -> str:
        """Do ONE small unit of work. Return a one-line summary for the report."""
        ...


# ── Quota gate ──────────────────────────────────────────────────────────────

@dataclass
class QuotaGate:
    """Per-provider spend gate with a reserve floor. Only spends down to
    (limit * reserve_fraction) so a morning session isn't starved."""
    limits: dict[str, int]                     # provider -> daily budget (request/step units)
    reserve_fraction: float = 0.20             # keep this fraction untouched
    usage_fn: Callable[[], dict[str, int]] | None = None   # provider -> used-so-far
    _spent: dict[str, int] = field(default_factory=dict)   # this session's ambient spend

    def _used(self, provider: str) -> int:
        base = 0
        if self.usage_fn:
            try:
                base = self.usage_fn().get(provider, 0)
            except Exception:
                base = 0
        return base + self._spent.get(provider, 0)

    def safe_to_spend(self, provider: str, cost: int) -> bool:
        limit = self.limits.get(provider)
        if limit is None:
            return True                        # unknown provider -> no gate
        floor = limit * self.reserve_fraction
        return (self._used(provider) + cost) <= (limit - floor)

    def record_spend(self, provider: str, cost: int) -> None:
        self._spent[provider] = self._spent.get(provider, 0) + cost


# ── Review queue (external actions never auto-run) ──────────────────────────

class ReviewQueue:
    """Anything that would leave the local sandbox is appended here for the
    user to confirm in the morning -- ambient NEVER performs it."""
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.items: list[dict] = []

    def enqueue(self, action: str, detail: str) -> None:
        self.items.append({"ts": time.time(), "action": action, "detail": detail})
        logger.info(f"[ambient] queued for morning review: {action}")

    def flush(self) -> None:
        if not self.items:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as f:
            for it in self.items:
                f.write(json.dumps(it) + "\n")


# ── Runner ──────────────────────────────────────────────────────────────────

@dataclass
class AmbientResult:
    steps_run: int
    report_lines: list[str]
    quota_spent: dict[str, int]
    stopped_reason: str        # "no_work" | "window_end" | "quota_exhausted"


class AmbientRunner:
    def __init__(
        self,
        jobs: list[AmbientJob],
        quota: QuotaGate,
        *,
        lockfile: str | Path = "./logs/ambient.lock",
        report_path: str | Path = "./logs/ambient_report.md",
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.jobs = jobs
        self.quota = quota
        self.lockfile = Path(lockfile)
        self.report_path = Path(report_path)
        self.clock = clock

    # -- single-instance lock ----------------------------------------------
    def _acquire_lock(self) -> bool:
        if self.lockfile.exists():
            try:
                pid = int(self.lockfile.read_text().strip() or "0")
            except Exception:
                pid = 0
            if pid and _pid_alive(pid):
                logger.warning(f"[ambient] another instance is live (pid {pid}) — refusing to start")
                return False
            # stale lock (dead pid) -> take it over
        self.lockfile.parent.mkdir(parents=True, exist_ok=True)
        self.lockfile.write_text(str(os.getpid()))
        return True

    def _release_lock(self) -> None:
        try:
            self.lockfile.unlink(missing_ok=True)
        except OSError:
            pass

    # -- main loop ----------------------------------------------------------
    async def run_window(self, window_secs: float) -> AmbientResult:
        if not self._acquire_lock():
            return AmbientResult(0, ["refused: another instance is live"], {}, "no_work")
        end = self.clock() + window_secs
        report: list[str] = []
        steps = 0
        reason = "no_work"
        try:
            while self.clock() < end:
                job = next((j for j in self.jobs if j.has_work()), None)
                if job is None:
                    reason = "no_work"; break
                if not self.quota.safe_to_spend(job.provider, job.est_cost):
                    # this job is quota-blocked; try any OTHER job with work + budget
                    alt = next((j for j in self.jobs
                                if j.has_work() and self.quota.safe_to_spend(j.provider, j.est_cost)), None)
                    if alt is None:
                        reason = "quota_exhausted"; break
                    job = alt
                try:
                    summary = await job.step()
                    self.quota.record_spend(job.provider, job.est_cost)
                    steps += 1
                    report.append(f"[{job.name}] {summary}")
                except Exception as exc:
                    report.append(f"[{job.name}] ERROR: {str(exc)[:100]}")
                    logger.warning(f"[ambient] {job.name} step failed: {exc}")
                if self.clock() >= end:
                    reason = "window_end"; break
            else:
                reason = "window_end"
        finally:
            self._release_lock()
        self._write_report(report, reason)
        return AmbientResult(steps, report, dict(self.quota._spent), reason)

    def _write_report(self, lines: list[str], reason: str) -> None:
        try:
            self.report_path.parent.mkdir(parents=True, exist_ok=True)
            ts = time.strftime("%Y-%m-%d %H:%M:%S")
            body = [f"# Ambient run — {ts}", "",
                    f"- stopped: **{reason}**",
                    f"- steps: **{len(lines)}**",
                    f"- quota spent: `{json.dumps(self.quota._spent)}`", "",
                    "## What ran", ""]
            body += [f"- {ln}" for ln in lines] or ["- (nothing)"]
            self.report_path.write_text("\n".join(body), encoding="utf-8")
        except OSError as exc:
            logger.warning(f"[ambient] report write failed: {exc}")


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        # On Windows os.kill(pid, 0) raises for a dead pid (PermissionError for
        # some live ones); treat any error as "cannot confirm alive".
        return False
    except Exception:
        return False
