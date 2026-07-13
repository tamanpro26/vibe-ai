"""
test_vibe.py
VibeAI Test Suite

Run:
  python test_vibe.py            Full test (all providers + pipeline)
  python test_vibe.py --quick    Only check API keys, no actual calls
  python test_vibe.py --team     Only test Free Manager Team
  python test_vibe.py --prompt   Only test a full pipeline prompt

What this tests:
  1. API key presence
  2. Each provider responds (Groq, Cerebras, Google, OpenRouter)
  3. Free Manager Team routing + response
  4. Manager fallback chain
  5. Full pipeline with a simple coding prompt
"""
import argparse
import asyncio
import os
import sys

if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
import time

# ── Minimal colour output without rich dependency ─────────────────────────────

class C:
    GREEN  = "\033[92m"
    RED    = "\033[91m"
    YELLOW = "\033[93m"
    CYAN   = "\033[96m"
    BOLD   = "\033[1m"
    DIM    = "\033[2m"
    RESET  = "\033[0m"

def ok(msg):    print(f"  {C.GREEN}✓{C.RESET}  {msg}")
def fail(msg):  print(f"  {C.RED}✗{C.RESET}  {msg}")
def warn(msg):  print(f"  {C.YELLOW}⚠{C.RESET}  {msg}")
def info(msg):  print(f"  {C.DIM}→{C.RESET}  {msg}")
def header(msg):print(f"\n{C.BOLD}{C.CYAN}{msg}{C.RESET}")
def sep():      print(f"  {'─'*50}")

# ── Add project root to path ──────────────────────────────────────────────────
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


# ══════════════════════════════════════════════════════════════════════════════
# TEST 1 — API Keys
# ══════════════════════════════════════════════════════════════════════════════

def test_api_keys() -> dict[str, bool]:
    header("Test 1 — API Keys")
    from config.settings import settings

    keys = {
        "ANTHROPIC_API_KEY":  (settings.anthropic_api_key,  "Primary manager"),
        "GEMINI_API_KEY":     (settings.gemini_api_key,     "Free team Dispatcher"),
        "GROQ_API_KEY":       (settings.groq_api_key,       "Free team Reviewer"),
        "CEREBRAS_API_KEY":   (settings.cerebras_api_key,   "Free team Synthesizer"),
        "OPENROUTER_API_KEY": (settings.openrouter_api_key, "6 specialist teams"),
        "HF_TOKEN":           (settings.hf_token,           "FLUX.1-dev images (optional)"),
    }

    results = {}
    for key, (val, desc) in keys.items():
        present = bool(val) and val not in ("sk-ant-api03-", "gsk_", "csk_", "AIza", "sk-or-v1-")
        results[key] = present
        if present:
            masked = val[:8] + "..." + val[-4:] if len(val) > 12 else "***"
            ok(f"{key} = {masked}  [{desc}]")
        else:
            opt = "(optional)" in desc or "optional" in desc.lower()
            if opt:
                warn(f"{key} not set  [{desc}]")
            else:
                fail(f"{key} MISSING  [{desc}]")

    critical = ["GEMINI_API_KEY", "GROQ_API_KEY", "CEREBRAS_API_KEY", "OPENROUTER_API_KEY"]
    all_critical = all(results.get(k) for k in critical)

    sep()
    if results.get("ANTHROPIC_API_KEY"):
        ok("Claude Sonnet 4.6 ready (primary manager)")
    else:
        warn("ANTHROPIC_API_KEY missing — Free Manager Team will handle all requests")

    if all_critical:
        ok("Free Manager Team fully configured ✓")
    else:
        missing = [k for k in critical if not results.get(k)]
        fail(f"Free Manager Team incomplete — missing: {', '.join(missing)}")

    return results


# ══════════════════════════════════════════════════════════════════════════════
# TEST 2 — Individual provider pings
# ══════════════════════════════════════════════════════════════════════════════

async def ping_provider(model_id: str, prompt: str = "Say: OK") -> tuple[bool, str, float]:
    """Ping a single model. Returns (success, response_snippet, latency_ms)."""
    from models.registry import registry
    t0 = time.perf_counter()
    try:
        connector = registry.get(model_id)
        result = await connector.generate(prompt=prompt, max_tokens=10, temperature=0.0)
        ms = (time.perf_counter() - t0) * 1000
        return True, result[:30].strip(), ms
    except Exception as exc:
        ms = (time.perf_counter() - t0) * 1000
        return False, str(exc)[:60], ms


async def test_providers() -> dict[str, bool]:
    header("Test 2 — Provider Pings")
    info("Sending 'Say: OK' to one model per provider...\n")

    tests = [
        ("gemini_flash",        "Gemini 2.5 Flash",     "Google AI Studio"),
        ("qwen36_27b_verifier",    "GPT-OSS 120B",         "Groq"),
        ("gpt_oss_120b_planner","GPT-OSS 120B",         "Cerebras"),
        ("nemotron_nano_format",      "Nemotron Omni 30B",    "OpenRouter"),
        ("nemotron_super_spec",   "Nemotron Super 120B",  "OpenRouter"),
    ]

    results = {}
    for model_id, model_name, provider in tests:
        success, snippet, ms = await ping_provider(model_id)
        results[model_id] = success
        if success:
            ok(f"{model_name:25s} [{provider:18s}] {ms:.0f}ms  '{snippet}'")
        else:
            fail(f"{model_name:25s} [{provider:18s}] ERROR: {snippet}")

    return results


# ══════════════════════════════════════════════════════════════════════════════
# TEST 3 — Free Manager Team
# ══════════════════════════════════════════════════════════════════════════════

async def test_free_manager_team() -> bool:
    header("Test 3 — Free Manager Council")
    from manager.free_manager import FreeManagerTeam

    team = FreeManagerTeam()

    # 3a — Review-vs-pipeline routing logic
    info("Testing review detection...\n")
    route_tests = [
        ("review the output and give a quality_score",     True),
        ("synthesize the team outputs into final response", False),
        ("assign the brain team to analyse this codebase", False),
        ("criteria_passed criteria_failed refine_instruction APPROVE", True),
        ("=== code === === brain === combine these outputs", False),
    ]
    routing_ok = True
    for prompt, expected in route_tests:
        actual = team._is_review(prompt, "")
        if actual == expected:
            ok(f"'{prompt[:45]}'  →  is_review={actual}")
        else:
            fail(f"'{prompt[:45]}'  expected is_review={expected}, got {actual}")
            routing_ok = False

    # 3b — Live call to each council member
    sep()
    info("Live call to each council member...\n")
    live_ok = True
    member_tests = [
        ("Planner",     "gemini_flash_council", "List 3 Python web frameworks in one sentence."),  # Gemini 2.0 Flash, isolated quota
        ("Drafter",     "gpt_oss_120b_coder",   "Say: Draft complete. (one line only)"),  # GPT-OSS 120B (Groq)
        ("Critic",      "gpt_oss_120b_debug",   "Say: Critique complete. (one line only)"),  # GPT-OSS 120B (Groq)
        ("Refiner",     "glm_47_cerebras",      "Say: Refinement complete. (one line only)"),  # GLM 4.7 (Cerebras)
        ("Synthesizer", "glm_47_flash_zai",     "Say: Synthesis complete. (one line only)"),  # GLM 4.7 Flash (Z.AI)
    ]
    for role, model_id, prompt in member_tests:
        success, snippet, ms = await ping_provider(model_id, prompt)
        if success:
            ok(f"{role:14s} ({model_id:18s}) {ms:.0f}ms  '{snippet[:40]}'")
        else:
            fail(f"{role:14s} ({model_id:18s}) ERROR: {snippet}")
            live_ok = False

    return routing_ok and live_ok


# ══════════════════════════════════════════════════════════════════════════════
# TEST 4 — Manager fallback chain
# ══════════════════════════════════════════════════════════════════════════════

async def test_fallback_chain() -> bool:
    header("Test 4 — Manager Fallback Chain")
    from tools.manager_fallback import ManagerFallbackChain, _classify_error

    chain = ManagerFallbackChain()

    # 4a — Error classification
    info("Error classification...\n")
    class MockError(Exception):
        def __init__(self, msg, code=None):
            super().__init__(msg)
            self.status_code = code

    error_tests = [
        (MockError("Your credit balance is too low"), "credits"),
        (MockError("rate_limit_exceeded"),             "rate_limit"),
        (MockError("invalid api key", 401),            "auth"),
        (MockError("unexpected error"),                "other"),
    ]
    for exc, expected in error_tests:
        actual = _classify_error(exc)
        if actual == expected:
            ok(f"'{str(exc)[:40]}'  →  {actual}")
        else:
            fail(f"'{str(exc)[:40]}'  expected {expected}, got {actual}")

    # 4b — Status report
    sep()
    report = chain.status_report()
    info(f"Active manager:   {report['active_manager']}")
    info(f"Using free team:  {report['using_free_team']}")
    ok("Fallback chain configured correctly")

    return True


# ══════════════════════════════════════════════════════════════════════════════
# TEST 5 — Full pipeline
# ══════════════════════════════════════════════════════════════════════════════

async def test_full_pipeline() -> bool:
    header("Test 5 — Full Pipeline (end-to-end)")
    info("Running a simple coding prompt through the 31-model pipeline...\n")
    info("This calls: Prompt Refiner → Brain → Code → Manager Review → Synthesis\n")

    from core.state import state
    from manager.claude_manager import manager
    await state.init()
    await manager.startup()

    prompt = "Write a Python function called add(a, b) that returns the sum of two numbers. Include a docstring."

    t0 = time.perf_counter()
    try:
        result = await manager.handle_user_request(prompt)
        ms = (time.perf_counter() - t0) * 1000

        ok(f"Pipeline completed in {ms:.0f}ms")
        sep()
        print(f"\n  {C.DIM}Response preview:{C.RESET}")
        for line in result[:400].split("\n"):
            print(f"  {C.DIM}{line}{C.RESET}")
        if len(result) > 400:
            print(f"  {C.DIM}... ({len(result)} chars total){C.RESET}")
        print()
        return True

    except Exception as exc:
        ms = (time.perf_counter() - t0) * 1000
        fail(f"Pipeline failed after {ms:.0f}ms: {exc}")
        return False


# ══════════════════════════════════════════════════════════════════════════════
# Main runner
# ══════════════════════════════════════════════════════════════════════════════

async def run_tests(quick=False, team_only=False, prompt_only=False):
    print(f"\n{C.BOLD}{C.CYAN}{'═'*54}{C.RESET}")
    print(f"{C.BOLD}{C.CYAN}  VibeAI Test Suite{C.RESET}")
    print(f"{C.BOLD}{C.CYAN}{'═'*54}{C.RESET}")

    passed, failed = 0, 0

    # Always run key check
    key_results = test_api_keys()
    if all(v for k, v in key_results.items() if k not in ("HF_TOKEN",)):
        passed += 1
    else:
        failed += 1

    if quick:
        _summary(passed, failed)
        return passed > 0

    if not team_only and not prompt_only:
        # Provider pings
        try:
            await test_providers()
            passed += 1
        except Exception as exc:
            fail(f"Provider test error: {exc}")
            failed += 1

    if not prompt_only:
        # Free Manager Team
        try:
            ok_team = await test_free_manager_team()
            if ok_team: passed += 1
            else:        failed += 1
        except Exception as exc:
            fail(f"Free Manager Team error: {exc}")
            failed += 1

        # Fallback chain
        try:
            ok_chain = await test_fallback_chain()
            if ok_chain: passed += 1
            else:         failed += 1
        except Exception as exc:
            fail(f"Fallback chain error: {exc}")
            failed += 1

    if not team_only:
        # Full pipeline
        try:
            ok_pipe = await test_full_pipeline()
            if ok_pipe: passed += 1
            else:        failed += 1
        except Exception as exc:
            fail(f"Pipeline error: {exc}")
            failed += 1

    _summary(passed, failed)
    return failed == 0


def _summary(passed: int, failed: int):
    total = passed + failed
    print(f"\n{C.BOLD}{'═'*54}{C.RESET}")
    if failed == 0:
        print(f"{C.GREEN}{C.BOLD}  ✓ All {total} tests passed{C.RESET}")
        print(f"{C.DIM}  Run the server: python main.py serve{C.RESET}")
    else:
        print(f"{C.YELLOW}{C.BOLD}  {passed}/{total} tests passed, {failed} failed{C.RESET}")
        print(f"{C.DIM}  Check your .env keys and re-run{C.RESET}")
    print(f"{C.BOLD}{'═'*54}{C.RESET}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="VibeAI Test Suite")
    parser.add_argument("--quick",  action="store_true", help="Only check API keys")
    parser.add_argument("--team",   action="store_true", help="Only test Free Manager Team")
    parser.add_argument("--prompt", action="store_true", help="Only test full pipeline")
    args = parser.parse_args()

    try:
        ok_all = asyncio.run(run_tests(
            quick=args.quick,
            team_only=args.team,
            prompt_only=args.prompt,
        ))
        sys.exit(0 if ok_all else 1)
    except KeyboardInterrupt:
        print("\n  Interrupted.")
        sys.exit(1)
