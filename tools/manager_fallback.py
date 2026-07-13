"""
tools/manager_fallback.py  (v3 — direct failover to Free Manager Team)

Flow:
  1. Claude Sonnet 4.6 is tried first (paid, frontier quality)
  2. If Claude fails with credit/auth error → Free Manager Team activates immediately
  3. If Claude fails with rate limit       → Free Manager Team activates with cooldown
  4. Every 5 minutes the system checks if Claude is back online
  5. If Claude recovers → Free Manager Team deactivates automatically

The Free Manager Council is NEVER a last resort — it is the DIRECT replacement
for Claude. This is a 2-tier system (Claude → Council), not a 6-tier chain.

Free Manager Council composition (5 models, collaborating on every call) --
see manager/free_manager.py's COUNCIL list for the live roster; call
free_manager_team.roster_summary() rather than hardcoding it here again --
a hardcoded copy of this list went stale in three different places at once
(this docstring, the Council's own Synthesizer prompt, and active_name
below) before anyone noticed two of the five models had been swapped out
from under it.
"""
from __future__ import annotations

import asyncio
import time
from enum import Enum
from typing import Any

from loguru import logger


class ManagerStatus(str, Enum):
    ACTIVE     = "active"
    RATE_LIMIT = "rate_limited"
    EXHAUSTED  = "exhausted"
    FAILED     = "failed"


_CREDIT_PHRASES = [
    "insufficient credit", "credit balance is too low", "insufficient_quota",
    "insufficient balance", "billing_hard_limit", "credit limit",
    "out of credits", "quota exceeded", "payment required",
]
_RATE_PHRASES = [
    "rate limit", "rate_limit_exceeded", "too many requests",
    "requests per minute", "tokens per minute", "overloaded", "throttl",
]
_AUTH_PHRASES = [
    "authentication", "invalid x-api-key", "invalid api key",
    "unauthorized", "401", "403",
]


def _classify_error(exc: Exception) -> str:
    msg  = str(exc).lower()
    code = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    if code in (401, 403) or any(p in msg for p in _AUTH_PHRASES):
        return "auth"
    if code == 402 or any(p in msg for p in _CREDIT_PHRASES):
        return "credits"
    if code == 429 or any(p in msg for p in _RATE_PHRASES):
        return "rate_limit"
    return "other"


BACKUP_MANAGER_SYSTEM = """You are a backup manager in VibeAI — a 31-model AI system.
Claude Sonnet 4.6 is temporarily unavailable. You have taken over.

REVIEW task — score output against success_criteria[]. Return ONLY JSON:
{
  "quality_score": 0.0-1.0,
  "criteria_passed": ["..."],
  "criteria_failed": ["..."],
  "issues": ["specific issue"],
  "refine_instruction": "exact actionable fix",
  "action": "APPROVE|REFINE|ESCALATE"
}
APPROVE >= 0.85. REFINE below. ESCALATE after 3 iterations.

SYNTHESIS task — combine team outputs into one polished response."""


class ManagerFallbackChain:
    """
    2-tier failover: Claude Sonnet 4.6 → Free Manager Team.
    The Free Manager Team is the DIRECT replacement, not a last resort.
    """

    RATE_LIMIT_COOLDOWN       = 90      # seconds
    PRIMARY_RECOVERY_INTERVAL = 300     # seconds (5 min)

    def __init__(self) -> None:
        # Council-first when there is no plausible Anthropic key: previously the
        # chain ALWAYS started ACTIVE, so the first manager call of every process
        # burned a guaranteed failed round-trip to a credential known to be
        # invalid, and the recovery loop then re-pinged that dead key every 5
        # minutes forever. If the key is absent or a placeholder, the Council IS
        # the manager — start there, skip the doomed attempt and the pings.
        self._has_plausible_key = self._key_looks_plausible()
        self._claude_status = (
            ManagerStatus.ACTIVE if self._has_plausible_key else ManagerStatus.EXHAUSTED
        )
        self._claude_cooldown_until  = 0.0
        self._last_recovery_attempt  = 0.0
        self._claude_total_calls     = 0
        self._claude_total_fails     = 0
        self._lock = asyncio.Lock()

    @staticmethod
    def _key_looks_plausible() -> bool:
        """A real Anthropic key is 'sk-ant-' + a long body. Placeholders like
        'sk-ant-...' or an empty string are not worth an API round-trip."""
        from config.settings import settings
        key = (settings.anthropic_api_key or "").strip()
        return key.startswith("sk-ant-") and len(key) > 40

    # ── Public interface ──────────────────────────────────────────────────────

    @property
    def using_backup(self) -> bool:
        return self._claude_status != ManagerStatus.ACTIVE

    @property
    def active_name(self) -> str:
        if self._claude_status == ManagerStatus.ACTIVE:
            return "Claude Sonnet 4.6"
        from manager.free_manager import free_manager_team
        return f"Free Manager Council ({free_manager_team.roster_summary()})"

    async def generate(
        self,
        prompt:      str,
        system:      str = "",
        images:      list[str] | None = None,
        max_tokens:  int   = 1000,
        temperature: float = 0.3,
        **kwargs: Any,
    ) -> str:
        # Periodically attempt to recover Claude
        await self._maybe_recover_primary()

        # ── Try Claude ────────────────────────────────────────────────────────
        if self._claude_is_available():
            try:
                result = await self._call_claude(
                    prompt, system, images or [], max_tokens, temperature
                )
                self._claude_total_calls += 1
                self._claude_status = ManagerStatus.ACTIVE
                return result

            except Exception as exc:
                self._claude_total_fails += 1
                err_type = _classify_error(exc)
                await self._handle_claude_failure(exc, err_type)

        # ── Claude unavailable → Free Manager Team ────────────────────────────
        return await self._call_free_team(
            prompt, system, images, max_tokens, temperature, **kwargs
        )

    # ── Claude ────────────────────────────────────────────────────────────────

    def _claude_is_available(self) -> bool:
        if self._claude_status == ManagerStatus.EXHAUSTED:
            return False
        if self._claude_status == ManagerStatus.FAILED:
            return False
        if self._claude_status == ManagerStatus.RATE_LIMIT:
            return time.time() > self._claude_cooldown_until
        return True

    async def _call_claude(self, prompt, system, images, max_tokens, temperature):
        from models.registry import registry
        connector = registry.get("claude_sonnet_4.6")
        return await connector.generate(
            prompt=prompt, system=system, images=images,
            max_tokens=max_tokens, temperature=temperature,
        )

    async def _handle_claude_failure(self, exc: Exception, err_type: str) -> None:
        async with self._lock:
            if err_type in ("credits", "auth"):
                self._claude_status = ManagerStatus.EXHAUSTED
                logger.warning(
                    f"[manager_chain] 💳 Claude Sonnet 4.6 unavailable ({err_type}) — "
                    f"Free Manager Team activating now"
                )
            elif err_type == "rate_limit":
                self._claude_status = ManagerStatus.RATE_LIMIT
                self._claude_cooldown_until = time.time() + self.RATE_LIMIT_COOLDOWN
                logger.warning(
                    f"[manager_chain] ⏳ Claude rate limited — "
                    f"Free Manager Team active for {self.RATE_LIMIT_COOLDOWN}s"
                )
            else:
                self._claude_status = ManagerStatus.FAILED
                logger.warning(
                    f"[manager_chain] ❌ Claude error — "
                    f"Free Manager Team taking over: {str(exc)[:60]}"
                )

    # ── Free Manager Team ─────────────────────────────────────────────────────

    async def _call_free_team(
        self, prompt, system, images, max_tokens, temperature, **kwargs
    ) -> str:
        from manager.free_manager import free_manager_team
        return await free_manager_team.generate(
            prompt=prompt,
            system=system,
            images=images or [],
            max_tokens=max_tokens,
            temperature=temperature,
            **kwargs,
        )

    # ── Recovery ──────────────────────────────────────────────────────────────

    async def _maybe_recover_primary(self) -> None:
        if not self._has_plausible_key:
            return  # no key configured — the Council IS the manager; nothing to recover
        if self._claude_status == ManagerStatus.ACTIVE:
            return
        if self._claude_status == ManagerStatus.EXHAUSTED:
            # Exhausted = credits gone — only try after very long interval
            if time.time() - self._last_recovery_attempt < self.PRIMARY_RECOVERY_INTERVAL:
                return
        if time.time() - self._last_recovery_attempt < self.PRIMARY_RECOVERY_INTERVAL:
            return

        self._last_recovery_attempt = time.time()
        logger.info("[manager_chain] checking if Claude Sonnet 4.6 is back online...")

        try:
            from models.registry import registry
            connector = registry.get("claude_sonnet_4.6")
            test = await connector.generate(prompt="Respond with OK", max_tokens=5)
            if test:
                self._claude_status = ManagerStatus.ACTIVE
                self._last_recovery_attempt = 0.0
                from manager.free_manager import free_manager_team
                free_manager_team.deactivate()
                logger.info("[manager_chain] ✓ Claude Sonnet 4.6 recovered — resuming as primary")
        except Exception:
            pass

    async def force_primary(self) -> bool:
        """Force a recovery attempt. Call this after topping up Anthropic credits."""
        self._last_recovery_attempt = 0.0
        await self._maybe_recover_primary()
        return self._claude_status == ManagerStatus.ACTIVE

    # ── Status ────────────────────────────────────────────────────────────────

    def status_report(self) -> dict:
        from manager.free_manager import free_manager_team
        cooldown_s = 0
        if self._claude_status == ManagerStatus.RATE_LIMIT:
            cooldown_s = max(0, int(self._claude_cooldown_until - time.time()))

        return {
            "active_manager":    self.active_name,
            "using_free_team":   self.using_backup,
            "claude": {
                "status":     self._claude_status.value,
                "calls":      self._claude_total_calls,
                "fails":      self._claude_total_fails,
                "cooldown_s": cooldown_s,
            },
            "free_team":         free_manager_team.status(),
            "recovery_interval": self.PRIMARY_RECOVERY_INTERVAL,
        }


# Singleton
fallback_chain = ManagerFallbackChain()
