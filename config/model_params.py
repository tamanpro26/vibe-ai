"""
config/model_params.py
Maximum performance parameters for every free model in VibeAI.

Each model has:
  max_tokens    — Absolute maximum for that provider/model
  temperature   — Provider-recommended optimal for the task type
  top_p         — Nucleus sampling (where supported)
  extra         — Provider-specific flags (thinking, reasoning effort, etc.)

Task types:
  "reasoning"   — Analysis, verification, quality scoring → low temp, max thinking
  "coding"      — Code generation, debugging → low-medium temp, high tokens
  "creative"    — UI design, writing, brainstorming → higher temp
  "routing"     — Fast classification → very low temp, few tokens
  "default"     — Balanced general purpose

Sources:
  Groq:     docs.groq.com/models
  Cerebras: docs.cerebras.ai/models
  Google:   ai.google.dev/gemini-api/docs (Gemini 2.5 recommends temp=1.0)
  OpenRouter: openrouter.ai/models
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ModelParams:
    max_tokens:  int
    temperature: dict[str, float]   # per task type
    top_p:       float = 0.95
    extra:       dict[str, Any] = field(default_factory=dict)

    def for_task(self, task_type: str = "default") -> dict:
        """Return a flat dict of params for the given task type."""
        return {
            "max_tokens":  self.max_tokens,
            "temperature": self.temperature.get(task_type, self.temperature["default"]),
            "top_p":       self.top_p,
            **self.extra,
        }


# ── Per-model optimal params ──────────────────────────────────────────────────

PARAMS: dict[str, ModelParams] = {

    # ── Groq models ───────────────────────────────────────────────────────────

    # GPT-OSS 120B on Groq — strongest reasoning model on Groq (verified live 2026-06-12)
    "openai/gpt-oss-120b": ModelParams(
        max_tokens=32_768,
        temperature={"reasoning": 0.3, "coding": 0.2, "creative": 0.8, "routing": 0.1, "default": 0.6},
        top_p=0.9,
    ),

    # Llama 3.3 70B — versatile general model
    "llama-3.3-70b-versatile": ModelParams(
        max_tokens=32_768,
        temperature={"reasoning": 0.3, "coding": 0.2, "creative": 0.9, "routing": 0.1, "default": 0.7},
        top_p=0.9,
    ),

    # Qwen3 32B — multilingual + strong reasoning
    # (reasoning_format=hidden is applied by the Groq connector)
    "qwen/qwen3-32b": ModelParams(
        max_tokens=32_768,
        temperature={"reasoning": 0.7, "coding": 0.3, "creative": 0.9, "routing": 0.1, "default": 0.7},
        top_p=0.95,
    ),

    # Llama 4 Scout — long context planning (Groq)
    "meta-llama/llama-4-scout-17b-16e-instruct": ModelParams(
        max_tokens=16_384,
        temperature={"reasoning": 0.4, "coding": 0.3, "creative": 0.8, "routing": 0.1, "default": 0.7},
        top_p=0.9,
    ),

    # Whisper — transcription (no text generation params)
    "whisper-large-v3": ModelParams(max_tokens=0, temperature={"default": 0.0}),

    # Qwen3.6-27B — Groq's stated successor to the deprecated Qwen3-32B
    "qwen/qwen3.6-27b": ModelParams(
        max_tokens=16_384,
        temperature={"reasoning": 0.6, "coding": 0.3, "creative": 0.9, "routing": 0.1, "default": 0.7},
        top_p=0.95,
    ),

    # Llama 3.1 8B Instant — routing workhorse: 14,400 req/day + 500k tokens/day free
    "llama-3.1-8b-instant": ModelParams(
        max_tokens=8_192,
        temperature={"reasoning": 0.3, "coding": 0.2, "creative": 0.8, "routing": 0.05, "default": 0.5},
        top_p=0.9,
    ),

    # ── Cerebras models (free tier has only these 2, verified live 2026-06-12) ─

    # GPT-OSS 120B on Cerebras — ~2,600 TPS, reasoning model
    "gpt-oss-120b": ModelParams(
        max_tokens=16_384,
        temperature={"reasoning": 0.3, "coding": 0.2, "creative": 0.8, "routing": 0.05, "default": 0.6},
        top_p=0.9,
    ),

    # GLM 4.7 on Cerebras
    "zai-glm-4.7": ModelParams(
        max_tokens=16_384,
        temperature={"reasoning": 0.3, "coding": 0.2, "creative": 0.9, "routing": 0.05, "default": 0.7},
        top_p=0.9,
    ),

    # ── Google Gemini models ──────────────────────────────────────────────────

    # Gemini 2.5 Flash — Google recommends temp=1.0 for Gemini 2.5
    # Enable thinking for complex tasks (thinking_budget tokens)
    "gemini-2.5-flash": ModelParams(
        max_tokens=65_536,
        temperature={"reasoning": 1.0, "coding": 1.0, "creative": 1.0, "routing": 0.3, "default": 1.0},
        top_p=0.95,
        extra={
            "thinking_config": {"thinking_budget": 5_000},  # Extended thinking for hard tasks
        },
    ),

    # Gemini 2.0 Flash — standard multimodal
    "gemini-2.0-flash": ModelParams(
        max_tokens=32_768,
        temperature={"reasoning": 0.7, "coding": 0.5, "creative": 0.9, "routing": 0.2, "default": 0.7},
        top_p=0.95,
    ),

    # ── OpenRouter :free models (verified live on 2026-06-09) ─────────────────

    # GPT-OSS 120B:free — large OpenAI-family model, strong instruction following
    "openai/gpt-oss-120b:free": ModelParams(
        max_tokens=16_384,
        temperature={"reasoning": 0.3, "coding": 0.2, "creative": 0.8, "routing": 0.1, "default": 0.7},
        top_p=0.9,
    ),

    # GPT-OSS 20B:free — kept for backwards compat
    "openai/gpt-oss-20b:free": ModelParams(
        max_tokens=16_384,
        temperature={"reasoning": 0.3, "coding": 0.2, "creative": 0.8, "routing": 0.1, "default": 0.7},
        top_p=0.9,
    ),

    # NVIDIA Nemotron 3 Super 120B:free — strong reasoning
    "nvidia/nemotron-3-super-120b-a12b:free": ModelParams(
        max_tokens=32_768,
        temperature={"reasoning": 0.2, "coding": 0.2, "creative": 0.8, "routing": 0.1, "default": 0.6},
        top_p=0.9,
    ),

    # NVIDIA Nemotron Omni 30B reasoning:free — chain-of-thought reasoning
    "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free": ModelParams(
        max_tokens=16_384,
        temperature={"reasoning": 0.3, "coding": 0.3, "creative": 0.7, "routing": 0.1, "default": 0.5},
        top_p=0.9,
    ),

    # NVIDIA Nemotron Nano 12B VL:free — vision-language model
    "nvidia/nemotron-nano-12b-v2-vl:free": ModelParams(
        max_tokens=8_192,
        temperature={"reasoning": 0.4, "coding": 0.3, "creative": 0.8, "routing": 0.1, "default": 0.7},
        top_p=0.9,
    ),

    # Liquid LFM 2.5 1.2B:free — tiny, ultra-fast routing model
    "liquid/lfm-2.5-1.2b-instruct:free": ModelParams(
        max_tokens=4_096,
        temperature={"reasoning": 0.1, "coding": 0.1, "creative": 0.6, "routing": 0.05, "default": 0.3},
        top_p=0.9,
    ),

    # Gemma 4 31B:free — Google's latest Gemma for multimodal routing
    "google/gemma-4-31b-it:free": ModelParams(
        max_tokens=8_192,
        temperature={"reasoning": 0.3, "coding": 0.2, "creative": 0.8, "routing": 0.1, "default": 0.6},
        top_p=0.9,
    ),

    # Claude Opus 4.6 — optional, manual-selection-only premium model. High
    # max_tokens is safe here because the connector streams whenever max_tokens
    # is large, avoiding the HTTP timeout that a non-streaming call would hit.
    "claude-opus-4-6": ModelParams(
        max_tokens=64_000,
        temperature={"reasoning": 0.3, "coding": 0.2, "creative": 0.8, "routing": 0.1, "default": 0.7},
        top_p=0.95,
    ),
}

# Fallback params for any model not in the table
DEFAULT_PARAMS = ModelParams(
    max_tokens=8_192,
    temperature={"reasoning": 0.3, "coding": 0.2, "creative": 0.8, "routing": 0.1, "default": 0.7},
    top_p=0.9,
)


def get_params(api_model: str, task_type: str = "default") -> dict:
    """
    Get optimal params for a given api_model string and task type.
    Returns a flat dict ready to unpack into generate() kwargs.
    """
    p = PARAMS.get(api_model, DEFAULT_PARAMS)
    return p.for_task(task_type)


def get_max_tokens(api_model: str) -> int:
    return PARAMS.get(api_model, DEFAULT_PARAMS).max_tokens or 8_192
