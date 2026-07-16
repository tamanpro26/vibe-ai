"""
manager/ceo.py — on-demand aggregate oversight for the whole AI organization.

NOT a per-request gate. nemotron_ultra_ceo is a 550B-parameter model with an
observed ~20s response time (see config/models_config.py's live-verification
comment for this entry) — putting it in the path of every Council response
would defeat the entire point of a free-tier system built around cheap,
fast models doing the routine work. This is instead a deliberately-invoked
report: something a human runs to ask "is the AI organization healthy?",
the same way a real CEO doesn't sit in every standup but reads the metrics
their managers and leads already produced.

Those metrics already exist and are read here, not re-invented:
  - manager/supervisor.py's per-stage verdicts (logs/supervisor_verdicts.jsonl)
  - teams/leadership.py's per-team Leader verdicts (logs/leader_verdicts.jsonl)
  - manager/free_manager.py's own call/fail counters (FreeManagerTeam.status())
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

from loguru import logger

from models.registry import generate_resilient

_CEO_MODEL = "nemotron_ultra_ceo"
_SUPERVISOR_LOG = Path("./logs/supervisor_verdicts.jsonl")
_LEADER_LOG = Path("./logs/leader_verdicts.jsonl")

_CEO_SYSTEM = """You are the CEO overseeing VibeAI's AI organization: a Free
Manager Council (5 stages -- Planner, Drafter, Critic, Refiner, Synthesizer)
and 5 specialist teams (brain, code, vision, design, router), each with its
own team Leader. You are given raw metrics: stage supervision verdicts,
team Leader verdicts, and per-role call/fail counts. Write a short, direct
health report for a human reading it once: what's working, what's degraded
or failing, and at most one concrete recommendation if something needs
attention. Do not restate the raw numbers back verbatim -- synthesize. If
the data shows no real problems, say so plainly instead of inventing
concerns."""


def _read_jsonl(path: Path, limit: int = 200) -> list[dict]:
    if not path.exists():
        return []
    try:
        lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
        return [json.loads(line) for line in lines[-limit:]]
    except Exception as exc:
        logger.warning(f"[ceo] failed reading {path}: {str(exc)[:80]}")
        return []


def _summarize_supervisor(records: list[dict]) -> dict:
    by_stage: dict[str, dict] = defaultdict(lambda: {"total": 0, "escalate": 0, "correct": 0, "drift": 0})
    for r in records:
        bucket = by_stage[r.get("stage", "unknown")]
        bucket["total"] += 1
        recommend = r.get("recommend")
        if recommend == "escalate":
            bucket["escalate"] += 1
        elif recommend == "correct":
            bucket["correct"] += 1
        if r.get("drift_detected"):
            bucket["drift"] += 1
    return dict(by_stage)


def _summarize_leaders(records: list[dict]) -> dict:
    by_team: dict[str, dict] = defaultdict(lambda: {"total": 0, "rejected": 0})
    for r in records:
        bucket = by_team[r.get("team", "unknown")]
        bucket["total"] += 1
        if not r.get("approved", True):
            bucket["rejected"] += 1
    return dict(by_team)


async def generate_oversight_report() -> str:
    """Aggregate real signal from disk plus the Council's live status, then
    ask the CEO model to synthesize a short report. On-demand only -- see
    module docstring for why this must never sit on the per-request path.
    """
    from manager.free_manager import free_manager_team  # local import: avoid import cycle at module load

    supervisor_records = _read_jsonl(_SUPERVISOR_LOG)
    leader_records = _read_jsonl(_LEADER_LOG)
    council_status = free_manager_team.status()

    if not supervisor_records and not leader_records and not council_status.get("total_calls", 0):
        return (
            "No activity recorded yet — the Council and teams haven't run "
            "since the logs were last cleared, so there is nothing to evaluate."
        )

    metrics = {
        "council_stage_verdicts": _summarize_supervisor(supervisor_records),
        "team_leader_verdicts":   _summarize_leaders(leader_records),
        "council_status":         council_status,
    }

    try:
        report = await generate_resilient(
            _CEO_MODEL,
            prompt=f"RAW METRICS:\n{json.dumps(metrics, indent=2)[:6000]}",
            system=_CEO_SYSTEM,
            max_tokens=600,
            temperature=0.2,
        )
        return report.strip()
    except Exception as exc:
        logger.warning(f"[ceo] report generation failed: {str(exc)[:80]}")
        return (
            "CEO report unavailable (model call failed) — raw metrics:\n"
            f"{json.dumps(metrics, indent=2)[:2000]}"
        )
