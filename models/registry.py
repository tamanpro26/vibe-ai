"""
models/registry.py
Registry that maps every model_id to a live connector instance.
Import `registry` and call registry.get("gpt_oss_120b_coder") anywhere.
"""
from __future__ import annotations

import asyncio

from loguru import logger


# ── Per-provider concurrency limits (free-tier rate limits) ───────────────────
# Groq:       30,000 tokens/min → cap at 5 concurrent (~6000 tok each)
# Cerebras:   lighter limits    → cap at 3 concurrent
# Google:     generous limits   → cap at 5 concurrent
# OpenRouter: mixed providers   → cap at 4 concurrent
# NVIDIA:     ~40 RPM SHARED across the whole key (not per-model, unverified
#             SLA) → cap at 1 concurrent, this tier is meant to be rarely hit
# Z.AI:       ~1000 req/day reported → cap at 3 concurrent, similar to Cerebras
_PROVIDER_CONCURRENCY: dict[str, int] = {
    "groq":       5,
    "cerebras":   3,
    "google":     5,
    "openrouter": 4,
    "nvidia":     1,
    "zai":        3,
    # A local gateway, not a rate-limited remote API -- concurrency is
    # bounded by the OmniRoute process itself, not by us. Kept modest
    # anyway since it fans out to real upstream providers underneath.
    "omniroute":  3,
}
_PROVIDER_SEMAPHORES: dict[str, asyncio.Semaphore] = {}
_PROVIDER_CALL_COUNTS: dict[str, int] = {}


def _get_sem(provider: str) -> asyncio.Semaphore | None:
    limit = _PROVIDER_CONCURRENCY.get(provider)
    if limit is None:
        return None
    if provider not in _PROVIDER_SEMAPHORES:
        _PROVIDER_SEMAPHORES[provider] = asyncio.Semaphore(limit)
    return _PROVIDER_SEMAPHORES[provider]


def get_call_stats() -> dict[str, int]:
    """Return per-provider call counts for the current process lifetime."""
    return dict(_PROVIDER_CALL_COUNTS)

from config.models_config import MODEL_REGISTRY, ModelDef
from models.base import BaseModelConnector
from models.connectors.anthropic_conn import AnthropicConnector
from models.connectors.openrouter import OpenRouterConnector
from models.connectors.ollama import OllamaConnector
from models.connectors.together_conn import TogetherConnector
from models.connectors.google_conn import GoogleConnector
from models.connectors.groq_conn import GroqConnector
from models.connectors.cerebras_conn import CerebrasConnector
from models.connectors.pollinations import PollinationsConnector
from models.connectors.huggingface import HuggingFaceConnector
from models.connectors.nvidia_conn import NvidiaConnector
from models.connectors.zai_conn import ZaiConnector
from models.connectors.mistral_conn import MistralConnector
from models.connectors.omniroute_conn import OmniRouteConnector


def _build_connector(model_def: ModelDef) -> BaseModelConnector:
    """Instantiate the correct connector for a given model definition."""
    match model_def.provider:
        case "anthropic":
            return AnthropicConnector(model_def)
        case "openrouter":
            return OpenRouterConnector(model_def)
        case "ollama":
            return OllamaConnector(model_def)
        case "together":
            return TogetherConnector(model_def)
        case "google":
            return GoogleConnector(model_def)
        case "groq":
            return GroqConnector(model_def)
        case "cerebras":
            return CerebrasConnector(model_def)
        case "pollinations":
            return PollinationsConnector(model_def)
        case "huggingface":
            return HuggingFaceConnector(model_def)
        case "nvidia":
            return NvidiaConnector(model_def)
        case "zai":
            return ZaiConnector(model_def)
        case "mistral":
            return MistralConnector(model_def)
        case "omniroute":
            return OmniRouteConnector(model_def)
        case _:
            raise ValueError(f"Unknown provider: {model_def.provider}")


class ModelRegistry:
    """
    Lazy-initialises connectors on first access.
    All model instances are singletons within this registry.
    """

    def __init__(self) -> None:
        self._connectors: dict[str, BaseModelConnector] = {}

    def get(self, model_id: str) -> BaseModelConnector:
        if model_id not in self._connectors:
            if model_id not in MODEL_REGISTRY:
                raise KeyError(f"Unknown model_id '{model_id}'. Check config/models_config.py")
            self._connectors[model_id] = _build_connector(MODEL_REGISTRY[model_id])
            logger.debug(f"[registry] instantiated {model_id}")
        return self._connectors[model_id]

    def get_team(self, team: str) -> list[BaseModelConnector]:
        """Return all connectors for a team in config order."""
        return [
            self.get(mid)
            for mid, mdef in MODEL_REGISTRY.items()
            if mdef.team == team
        ]

    def preload_all(self) -> None:
        """Eagerly instantiate all connectors (useful at startup)."""
        for model_id in MODEL_REGISTRY:
            self.get(model_id)
        logger.info(f"[registry] {len(self._connectors)} connectors ready")

    def __repr__(self) -> str:
        loaded = len(self._connectors)
        total = len(MODEL_REGISTRY)
        return f"<ModelRegistry {loaded}/{total} loaded>"


# Singleton — import everywhere
registry = ModelRegistry()


# ── Cross-model failover ───────────────────────────────────────────────────────
#
# Providers whose models are interchangeable text/vision LLMs. Image-gen
# (pollinations/huggingface) and paid Anthropic are excluded — they either
# have different generate() semantics or shouldn't be silently substituted.
# "mistral" is DELIBERATELY excluded too: its free tier's ToS scopes it to
# evaluation/prototyping, not production traffic — auto-substituting it into
# every failover would route regular agent work through a tier we don't have
# the right to use that way. It stays manual-/model-select only.
_LLM_PROVIDERS = {"google", "groq", "cerebras", "openrouter", "ollama", "nvidia", "zai"}

# Cross-team safety nets, spread across different providers so a single
# provider outage can't take them all down at once.
_TEXT_SAFETY_NET   = ["qwen36_27b_verifier", "llama33_70b_memory", "gpt_oss_120b_coder"]
_VISION_SAFETY_NET = ["nemotron_vl", "gemini_flash"]


async def generate_resilient(
    model_id: str, exclude: set[str] | None = None, **kwargs,
) -> str:
    """
    generate() with automatic cross-model failover.

    Order: requested model → same-team LLMs → cross-team safety net.
    Vision calls only fall back to vision-capable models. Duplicate
    (provider, api_model) endpoints are tried once — if an endpoint is
    down for one model_id it's down for all of them.

    `exclude` removes specific model_ids from the candidate list entirely
    (not just skipped-and-revisited) — for an INDEPENDENT verifier/reviewer
    call, this must include the model_id whose output is being judged, so
    an outage of the verifier's primary can never silently fail over onto
    grading its own work (e.g. _TEXT_SAFETY_NET's gpt_oss_120b_coder is
    also a code-cascade tier model — without this, a verifier outage could
    resolve straight into that same model reviewing itself). Also excludes
    every OTHER model_id sharing the same (provider, api_model) endpoint —
    several registry entries are the same real model wearing a different
    role (e.g. gpt_oss_120b_coord/_coder/_debug are all openai/gpt-oss-120b
    on Groq under different team labels), so excluding by model_id alone
    would let the fallback reach an identically-biased "different" model.
    See core/confidence_cascade.py's `_score()` for the call site.
    """
    primary = MODEL_REGISTRY[model_id]
    if primary.provider not in _LLM_PROVIDERS or "audio" in primary.capabilities:
        return await registry.get(model_id).generate(**kwargs)

    needs_vision = bool(kwargs.get("images"))
    candidates = [model_id]
    candidates += [
        mid for mid, d in MODEL_REGISTRY.items()
        if mid != model_id
        and d.team == primary.team
        and d.provider in _LLM_PROVIDERS
        and "audio" not in d.capabilities
    ]
    for mid in (_VISION_SAFETY_NET if needs_vision else _TEXT_SAFETY_NET):
        if mid not in candidates:
            candidates.append(mid)
    if exclude:
        excluded_endpoints = {
            (MODEL_REGISTRY[mid].provider, MODEL_REGISTRY[mid].api_model)
            for mid in exclude if mid in MODEL_REGISTRY
        }
        candidates = [
            mid for mid in candidates
            if mid not in exclude
            and (MODEL_REGISTRY[mid].provider, MODEL_REGISTRY[mid].api_model) not in excluded_endpoints
        ]
        if not candidates:
            raise RuntimeError(
                f"No candidates left for {model_id} after excluding {exclude}"
            )

    tried_endpoints: set[tuple[str, str]] = set()
    last_exc: Exception | None = None
    for mid in candidates:
        d = MODEL_REGISTRY[mid]
        if needs_vision and not ({"vision", "multimodal"} & set(d.capabilities)):
            continue
        endpoint = (d.provider, d.api_model)
        if endpoint in tried_endpoints:
            continue
        tried_endpoints.add(endpoint)
        try:
            sem = _get_sem(d.provider)
            if sem:
                async with sem:
                    result = await registry.get(mid).generate(**kwargs)
            else:
                result = await registry.get(mid).generate(**kwargs)
            _PROVIDER_CALL_COUNTS[d.provider] = _PROVIDER_CALL_COUNTS.get(d.provider, 0) + 1
            if mid != model_id:
                logger.info(f"[registry] {model_id} down → {mid} answered instead")
            return result
        except Exception as exc:
            last_exc = exc
            logger.warning(
                f"[registry] {mid} failed ({str(exc)[:60]}) — trying next fallback"
            )

    raise last_exc or RuntimeError(f"All fallbacks exhausted for {model_id}")
