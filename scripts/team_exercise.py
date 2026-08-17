#!/usr/bin/env python3
"""
scripts/team_exercise.py — provoke every team, tool and feature, and PROVE it.

An answer coming back proves nothing about which specialist produced it. This
harness instruments the pipeline so each task reports the teams that actually
instantiated, the models that were actually called, and the tools that actually
ran -- then grades the output with a deterministic check wherever execution can
decide it, rather than asking a model whether it did well.

    python scripts/team_exercise.py            # everything
    python scripts/team_exercise.py code brain # only these task ids

Checks are execution- or regex-based on purpose. The one place that is
unavoidably fuzzy (image content) says so in its own result.
"""
from __future__ import annotations

import asyncio
import base64
import io
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("VIBE_SCAFFOLD_CONFIG", str(ROOT / "configs/scaffold.json"))

TRACE: dict[str, list] = {"teams": [], "models": [], "tools": []}


def _install_probes() -> None:
    """Record real activity. Patch the module-level bindings too -- teams import
    generate_resilient by name, so patching only the registry misses most calls.
    """
    import models.registry as reg
    import manager.claude_manager as mgr

    orig_generate = reg.generate_resilient

    async def traced_generate(model_id, *a, **kw):
        TRACE["models"].append(model_id if isinstance(model_id, str) else str(model_id))
        return await orig_generate(model_id, *a, **kw)

    reg.generate_resilient = traced_generate
    import teams.base_team, teams.prompt_refiner, teams.prompt_enhancer, teams.code, teams.research
    for m in (teams.base_team, teams.prompt_refiner, teams.prompt_enhancer,
              teams.code, teams.research, mgr):
        if hasattr(m, "generate_resilient"):
            m.generate_resilient = traced_generate

    orig_get_team = mgr._get_team

    def traced_get_team(name):
        TRACE["teams"].append(name)
        return orig_get_team(name)

    mgr._get_team = traced_get_team

    # Distinguish "the fast path answered it" from "the team failed to run".
    # Without this the report shows an empty team list for both, which reads as
    # a failure when it is often the intended routing.
    orig_fast = mgr.ClaudeManager._try_fast_path

    async def traced_fast(self, prompt):
        result = await orig_fast(self, prompt)
        if result is not None:
            TRACE["teams"].append("(fast-path)")
        return result

    mgr.ClaudeManager._try_fast_path = traced_fast

    # Tools: the harness claim ("tools work") needs evidence, not assumption.
    try:
        from tools.code_executor import CodeExecutor
        orig_run = CodeExecutor.run

        async def traced_run(self, code):
            TRACE["tools"].append("code_executor")
            return await orig_run(self, code)
        CodeExecutor.run = traced_run
    except Exception as exc:   # a broken probe must not fail the exercise
        print(f"[probe] code_executor not instrumented: {exc}")

    try:
        import tools.search as se
        orig_search = se.SearchIntelligenceStack.search

        async def traced_search(self, query, extract_full=None):
            TRACE["tools"].append("search")
            return await orig_search(self, query, extract_full)
        se.SearchIntelligenceStack.search = traced_search
    except Exception:
        pass


def _test_image() -> str:
    """A deterministic image whose content we KNOW, so a vision claim is
    checkable instead of plausible-sounding."""
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (512, 512), "white")
    d = ImageDraw.Draw(img)
    d.ellipse((140, 140, 372, 372), fill="red")
    d.rectangle((30, 30, 110, 110), fill="blue")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


# ── checks ────────────────────────────────────────────────────────────────────

def check_runs(assertion: str):
    """Execute the answer's Python and assert on it -- the strongest check."""
    def _check(answer: str) -> tuple[bool, str]:
        import subprocess, tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8") as fh:
            fh.write(answer)
            p = fh.name
        try:
            r = subprocess.run(
                [str(ROOT / ".venv/Scripts/python.exe"), str(ROOT / "scripts/vibeloop_check.py"), p, assertion],
                capture_output=True, text=True, timeout=120,
            )
            return r.returncode == 0, (r.stdout + r.stderr).strip()[:110]
        except Exception as e:
            return False, f"{type(e).__name__}: {e}"
        finally:
            os.unlink(p)
    return _check


def check_contains(*needles: str, n: int = 1):
    def _check(answer: str) -> tuple[bool, str]:
        low = (answer or "").lower()
        hits = [x for x in needles if x.lower() in low]
        return len(hits) >= n, f"matched {hits or 'nothing'}"
    return _check


def check_cited(min_urls: int = 2):
    def _check(answer: str) -> tuple[bool, str]:
        urls = set(re.findall(r"https?://[^\s)\]]+", answer or ""))
        return len(urls) >= min_urls, f"{len(urls)} distinct source url(s)"
    return _check


def check_image(answer: str) -> tuple[bool, str]:
    # The first version of this only matched URLs ending in .png/.jpg and would
    # have failed a PERFECT answer: this project's image endpoint is
    # Pollinations, whose URLs look like
    # https://image.pollinations.ai/prompt/<encoded prompt> -- no extension at
    # all. A check that cannot pass a correct answer is worse than no check.
    text = answer or ""
    patterns = (
        r"https?://\S+\.(?:png|jpg|jpeg|webp|gif)",   # extension-style
        r"https?://\S*(?:pollinations|image|img|cdn)\S*/\S+",  # host/path-style
        r"!\[[^\]]*\]\(https?://",                     # markdown image
        r"data:image/",                                 # inline base64
    )
    hit = next((p for p in patterns if re.search(p, text, re.I)), None)
    return bool(hit), f"image reference found ({hit})" if hit else "no image returned"


# ── tasks ─────────────────────────────────────────────────────────────────────
# `expect` names the team this task is BUILT to provoke. Others may legitimately
# also run -- that is the design, not a failure -- so the report shows what
# actually activated rather than asserting an exact set.

TASKS = [
    dict(id="code", expect="code", force="code", prompt=(
        "Write a Python class RingBuffer(capacity) with append(item), and to_list() "
        "returning items oldest-first. When full, appending overwrites the oldest. "
        "capacity must be >= 1 or raise ValueError."
    ), check=check_runs(
        "rb=RingBuffer(3)\n"
        "for i in range(5): rb.append(i)\n"
        "assert rb.to_list()==[2,3,4], rb.to_list()\n"
        "rb2=RingBuffer(1); rb2.append('a'); rb2.append('b')\n"
        "assert rb2.to_list()==['b']\n"
        "try:\n    RingBuffer(0); assert False, 'should raise'\nexcept ValueError: pass"
    )),

    dict(id="debug", expect="code", force="code", prompt=(
        "This function is wrong -- it mutates its input and loses duplicates. Fix it "
        "and return the corrected function:\n\n"
        "def merge_sorted(a, b):\n"
        "    out = a\n"
        "    for x in b:\n"
        "        if x not in out:\n"
        "            out.append(x)\n"
        "    return sorted(out)"
    ), check=check_runs(
        "a=[1,3,3]; b=[2,3]\n"
        "r=merge_sorted(a,b)\n"
        "assert r==[1,2,3,3,3], r\n"
        "assert a==[1,3,3], f'input mutated: {a}'"
    )),

    dict(id="brain", expect="brain", force="brain", prompt=(
        "A tank holds 240 litres. Pipe A fills it in 12 minutes, pipe B in 8 minutes, "
        "and a drain empties a full tank in 24 minutes. All three open at once on an "
        "empty tank. Work out how many minutes until it is full. State the final "
        "number of minutes explicitly."
    # A=20 L/min, B=30 L/min, drain=10 L/min -> net 40 -> 240/40 = 6 minutes.
    # Recomputed by hand after the first run: the original expected value here
    # was wrong, and a wrong check silently corrupts every later comparison.
    ), check=check_contains("6 minutes", "= 6", "is 6", "6.0")),

    dict(id="research", expect="research", force="research", prompt=(
        "What is the current supported status of free-threaded (no-GIL) CPython, and "
        "which Python version made it officially supported? Cite your sources."
    ), check=check_cited(2)),

    dict(id="vision", expect="vision", force="vision", prompt=(
        "Look at this image. Name the two shapes and their colours."
    ), image=True, check=check_contains("red", "blue", n=2)),

    dict(id="design", expect="design", force="design", prompt=(
        "Generate an image: a minimalist logo of a mountain at sunrise, flat vector style."
    ), check=check_image),

    dict(id="executor", expect="code", prompt=(
        "Compute the 20th prime number. Verify it by actually running code, then state "
        "the number."
    ), check=check_contains("71")),

    dict(id="multipart", expect="brain", prompt=(
        "Three things, all of them: (1) give the time complexity of binary search, "
        "(2) write a one-line Python expression that reverses a string, "
        "(3) name the data structure best suited to LIFO access."
    ), check=check_contains("log n", n=1)),
]


async def run_task(task: dict) -> dict:
    from manager.claude_manager import manager
    for k in TRACE:
        TRACE[k].clear()
    extra = {"image_b64": _test_image()} if task.get("image") else None
    # `force` exists because natural routing legitimately sends several of these
    # to the fast path (a single model, no team) -- which is the right product
    # behaviour but means the team under test never runs. Forcing it is how we
    # exercise the specialist; the unforced pass above the table shows what
    # routing would have done on its own.
    t0 = time.perf_counter()
    try:
        answer = await asyncio.wait_for(
            manager.handle_user_request(
                task["prompt"], extra=extra, forced_team=task.get("force"),
            ),
            timeout=420,
        )
        err = None
    except asyncio.TimeoutError:
        answer, err = "", "TIMEOUT at 420s"
    except Exception as e:
        answer, err = "", f"{type(e).__name__}: {str(e)[:80]}"
    elapsed = time.perf_counter() - t0
    passed, detail = (False, err) if err else task["check"](answer)
    return dict(
        id=task["id"], expect=task["expect"], passed=passed, detail=detail,
        elapsed=elapsed, answer_len=len(answer or ""),
        teams=sorted(set(TRACE["teams"])), tools=sorted(set(TRACE["tools"])),
        models=len(TRACE["models"]), distinct_models=len(set(TRACE["models"])),
    )


async def main() -> int:
    _install_probes()
    from core.state import state
    await state.init()

    wanted = set(sys.argv[1:])
    tasks = [t for t in TASKS if not wanted or t["id"] in wanted]
    rows = []
    for task in tasks:
        row = await run_task(task)
        rows.append(row)
        mark = "PASS" if row["passed"] else "FAIL"
        print(f"[{mark}] {row['id']:<10} {row['elapsed']:>6.1f}s  "
              f"expect={row['expect']:<8} ran={','.join(row['teams']) or '-':<28} "
              f"tools={','.join(row['tools']) or '-':<22} "
              f"calls={row['models']:>2} ({row['distinct_models']} distinct)  {row['detail'][:70]}", flush=True)

    print("\n" + "=" * 100)
    ok = sum(r["passed"] for r in rows)
    print(f"{ok}/{len(rows)} passed   total {sum(r['elapsed'] for r in rows):.0f}s")
    provoked = {r["expect"] for r in rows if r["passed"] and r["expect"] in r["teams"]}
    missed = [r for r in rows if r["expect"] not in r["teams"]]
    print(f"teams provably activated by their own task: {sorted(provoked) or 'none'}")
    for r in missed:
        print(f"  !! {r['id']}: expected {r['expect']} to run, actual: {r['teams'] or 'none'}")
    print(f"tools exercised: {sorted({t for r in rows for t in r['tools']}) or 'none'}")
    return 0 if ok == len(rows) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
