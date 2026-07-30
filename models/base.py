"""
models/base.py
Abstract base connector — all 31 connectors inherit from this.
"""
from __future__ import annotations

import asyncio
import time
from abc import ABC, abstractmethod
from typing import Any

from loguru import logger
from tenacity import AsyncRetrying, stop_after_attempt, wait_exponential, retry_if_exception

from config.models_config import ModelDef
from config.settings import settings
from models.circuit_breaker import is_broken, record_failure


def _should_retry(exc: BaseException) -> bool:
    """
    Retrying can't fix bad keys, missing models, oversized requests, or
    malformed requests. Also: every connector raises a plain RuntimeError
    with "not set" in the message when its API key isn't configured (a
    RuntimeError has no status_code/code, so the check below alone would
    retry it 3x with backoff — wasted latency that directly matters now that
    the agent fallback chain can reach not-yet-configured tiers, e.g. a fresh
    install without ZAI_API_KEY/NVIDIA_API_KEY set).

    CancelledError MUST NOT be retried: verified live that a caller wrapping
    connector.generate() in asyncio.wait_for(timeout=...) had its timeout
    silently ignored (a 1.5s deadline let a call run its full ~10s) because
    tenacity's retry_if_exception(_should_retry) predicate saw CancelledError
    (no status_code/code -> None not in (...) -> True) and launched a fresh
    attempt instead of letting the cancellation propagate. Raising a bare
    asyncio.wait_for around ANY connector call was silently a no-op before
    this fix — a real correctness gap, not specific to one caller.
    """
    if isinstance(exc, asyncio.CancelledError):
        return False
    if isinstance(exc, RuntimeError) and "not set" in str(exc):
        return False
    code = getattr(exc, "status_code", None) or getattr(exc, "code", None)
    return code not in (400, 401, 403, 404, 413)


def finish_reason_class(finish_reason: str | None, produced_valid_output: bool) -> str:
    """Classify a call outcome from its finish_reason -- the ground-truth
    discriminator this project decided it needed after the truncation-mystery
    night (a 237-char response was misdiagnosed as a budget clip because this
    field wasn't captured). Rules (jcode Feature 1 port):
      - finish_reason == "length" on a FAILED attempt  -> "budget_artifact"
        (the model ran out of output budget mid-answer -- NOT a reasoning
        failure; never count it against the model).
      - finish_reason == "stop" but no valid output    -> "empty_or_malformed"
      - anything else with no valid output              -> "incomplete"
      - valid output                                    -> "ok"
    """
    if produced_valid_output:
        return "ok"
    if finish_reason == "length":
        return "budget_artifact"
    if finish_reason == "stop":
        return "empty_or_malformed"
    return "incomplete"


class BaseModelConnector(ABC):

    def __init__(self, model_def: ModelDef) -> None:
        self.model_def = model_def
        self.model_id  = model_def.model_id
        self.api_model = model_def.api_model
        # Circuit-breaker key: (provider, api_model) rather than model_id —
        # several model_ids can share one underlying API model + key (e.g.
        # gemini_flash, gemini_flash_prompt, and gemini_flash_vision all hit gemini-2.5-flash on
        # the same GEMINI_API_KEY), so they share the same quota and must
        # share the same breaker, or tripping it via one model_id wouldn't
        # stop the others from wastefully rediscovering the same exhaustion.
        self._breaker_key = f"{model_def.provider}:{model_def.api_model}"
        # Last-call metadata, captured by _note_completion() so eval harnesses,
        # canary_stats, and guard logs can read it WITHOUT changing generate()'s
        # str return type across every caller. finish_reason classification
        # (Feature 1, jcode port): "length" on a failed attempt is a
        # budget_artifact, not a model failure -- see finish_reason_class().
        self.last_finish_reason: str | None = None
        self.last_usage: Any = None

    # ── Provider request-body extras (jcode extra_body port) ──────────────────

    @staticmethod
    def _deep_merge(base: dict, extra: dict) -> dict:
        """Merge `extra` into `base` in place; nested dicts merge, scalars from
        `extra` win on collision. Used to fold a model's configured extra_body
        into the request without clobbering sibling keys."""
        for k, v in extra.items():
            if isinstance(v, dict) and isinstance(base.get(k), dict):
                BaseModelConnector._deep_merge(base[k], v)
            else:
                base[k] = v
        return base

    def _merged_extra_body(self, existing: dict | None = None) -> dict | None:
        """Return the extra_body to pass to the OpenAI SDK's extra_body= kwarg:
        the connector's own `existing` extras deep-merged with the model's
        configured extra_body (config wins). A malformed config value is logged
        and ignored -- a bad line must never take the seat down (jcode policy)."""
        out: dict = dict(existing) if existing else {}
        cfg = getattr(self.model_def, "extra_body", None)
        if cfg:
            if isinstance(cfg, dict):
                self._deep_merge(out, cfg)
            else:
                logger.warning(f"[{self.model_id}] extra_body is not a dict ({type(cfg).__name__}) — ignoring")
        return out or None

    def _request_timeout(self) -> float | None:
        """Per-model total-timeout override (seconds), or None for the client
        default. The non-streaming equivalent of jcode's stream_idle_timeout."""
        return getattr(self.model_def, "request_timeout_s", None)

    def _note_completion(self, resp: Any) -> None:
        """Capture finish_reason + usage off an OpenAI-style response object.
        Best-effort: never raises (a metadata-capture bug must not fail a real,
        successful call)."""
        try:
            self.last_finish_reason = resp.choices[0].finish_reason
        except Exception:
            self.last_finish_reason = None
        try:
            self.last_usage = getattr(resp, "usage", None)
        except Exception:
            self.last_usage = None

    # ── Standard text generation ──────────────────────────────────────────────

    async def generate(
        self,
        prompt:      str,
        system:      str = "",
        images:      list[str] | None = None,
        max_tokens:  int | None = None,
        temperature: float = 0.7,
        task_type:   str = "default",
        **kwargs: Any,
    ) -> str:
        from config.model_params import get_params, get_max_tokens
        # Use per-model optimal max_tokens unless caller specifies
        if max_tokens is None or max_tokens == settings.default_token_budget:
            max_tokens = get_max_tokens(self.api_model)

        logger.info(
            f"[{self.model_id}] generate | max_tokens={max_tokens} | "
            f"task={task_type} | images={bool(images)}"
        )

        remaining = is_broken(self._breaker_key)
        if remaining is not None:
            raise RuntimeError(
                f"{self.model_id} circuit-broken for {remaining:.0f}s more "
                f"(recent quota/rate-limit failure — skipping retry)"
            )

        t0 = time.perf_counter()
        try:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(3),
                wait=wait_exponential(multiplier=1, min=2, max=10),
                retry=retry_if_exception(_should_retry),
                reraise=True,
            ):
                with attempt:
                    result = await self._call(
                        prompt=prompt, system=system, images=images or [],
                        max_tokens=max_tokens, temperature=temperature,
                        task_type=task_type, **kwargs,
                    )
                    if not result or not result.strip():
                        # Some providers return a 200 with empty content on a
                        # transient glitch instead of an error (already worked
                        # around ad hoc for Ollama's edge router and
                        # Pollinations' empty-200 CDN response) -- verified
                        # live (2026-07-14): Cerebras/GLM-4.7 returned 0 chars
                        # on a real successful call, no rate limit involved.
                        # Treat it as retry-worthy like any other failure
                        # instead of handing callers "" as if it were real.
                        raise RuntimeError(f"{self.model_id} returned an empty response")
                    duration_ms = (time.perf_counter() - t0) * 1000
                    logger.info(f"[{self.model_id}] done | {len(result)} chars")
                    try:
                        from core.activity_log import activity_log
                        activity_log.log_model(
                            model_id=self.model_id,
                            api_model=self.api_model,
                            provider=str(self.model_def.provider),
                            role=task_type,
                            prompt=prompt[:100],
                            output=result[:100],
                            duration_ms=duration_ms,
                            success=True,
                        )
                    except Exception:
                        pass
                    return result
        except Exception as exc:
            duration_ms = (time.perf_counter() - t0) * 1000
            logger.error(f"[{self.model_id}] failed: {exc}")
            record_failure(self._breaker_key, exc)
            try:
                from core.activity_log import activity_log
                activity_log.log_model(
                    model_id=self.model_id, api_model=self.api_model,
                    provider=str(self.model_def.provider), role=task_type,
                    prompt=prompt[:80], output=f"ERROR: {str(exc)[:80]}",
                    duration_ms=duration_ms, success=False,
                )
            except Exception:
                pass
            raise
        return ""

    # ── Agentic tool-calling generation ──────────────────────────────────────

    async def generate_with_tools(
        self,
        messages:    list[dict],
        tools:       list[dict],
        max_tokens:  int = 16_384,
        temperature: float = 0.7,
        task_type:   str = "coding",
        **kwargs: Any,
    ) -> dict:
        """
        Call the model with tools. Returns either:
          {"type": "text", "content": "..."}          — final response
          {"type": "tool_calls", "tool_calls": [...]}  — requests tool execution
          {"type": "both", "content": "...", "tool_calls": [...]}
        """
        logger.info(
            f"[{self.model_id}] generate | max_tokens={max_tokens} | tools={len(tools)}"
        )

        remaining = is_broken(self._breaker_key)
        if remaining is not None:
            raise RuntimeError(
                f"{self.model_id} circuit-broken for {remaining:.0f}s more "
                f"(recent quota/rate-limit failure — skipping retry)"
            )

        t0 = time.perf_counter()
        try:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(3),
                wait=wait_exponential(multiplier=2, min=8, max=30),
                retry=retry_if_exception(_should_retry),
                reraise=True,
            ):
                with attempt:
                    result = await self._call_with_tools(
                        messages, tools, max_tokens, temperature, **kwargs
                    )
        except Exception as exc:
            logger.error(f"[{self.model_id}] generate_with_tools failed after retries: {exc}")
            record_failure(self._breaker_key, exc)
            raise
        duration_ms = (time.perf_counter() - t0) * 1000
        try:
            from core.activity_log import activity_log
            tool_names   = [tc["name"] for tc in result.get("tool_calls", [])]
            last_user    = next(
                (str(m.get("content", "")) for m in reversed(messages) if m.get("role") == "user"), ""
            )
            output_preview = (
                f"→ tool_calls: {tool_names}" if tool_names
                else result.get("content", "")[:100]
            )
            activity_log.log_model(
                model_id=self.model_id,
                api_model=self.api_model,
                provider=str(self.model_def.provider),
                role=f"agent/{task_type}",
                prompt=last_user[:100],
                output=output_preview,
                duration_ms=duration_ms,
                success=True,
            )
        except Exception:
            pass
        return result

    async def _call_with_tools(
        self,
        messages:    list[dict],
        tools:       list[dict],
        max_tokens:  int,
        temperature: float,
        **kwargs: Any,
    ) -> dict:
        """Override in subclasses that support native tool calling."""
        # Default: convert last user message to a prompt and call without tools
        last_user = next(
            (m["content"] for m in reversed(messages) if m["role"] == "user"),
            "Continue.",
        )
        system = next(
            (m["content"] for m in messages if m["role"] == "system"),
            "",
        )
        text = await self._call(
            prompt=str(last_user), system=system, images=[],
            max_tokens=max_tokens, temperature=temperature,
        )
        return {"type": "text", "content": text}

    @abstractmethod
    async def _call(
        self,
        prompt:      str,
        system:      str,
        images:      list[str],
        max_tokens:  int,
        temperature: float,
        **kwargs: Any,
    ) -> str: ...

    def supports(self, capability: str) -> bool:
        return capability in self.model_def.capabilities

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} id={self.model_id}>"
