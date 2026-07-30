"""
config/settings.py
All settings loaded from .env — every module imports from here.
"""
import os

from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── Primary manager (paid) ────────────────────────────
    anthropic_api_key: str = Field(default="", description="Anthropic — console.anthropic.com/settings/keys")
    # Separate, optional real key for claude_opus_4_6 (manual /model selection only).
    # Kept distinct from anthropic_api_key above, which stays invalid on purpose.
    anthropic_api_key_2: str = Field(default="", description="Anthropic — secondary/real key for the optional Claude Opus model")

    # ── Free Manager Team providers ───────────────────────
    # All 3 required for the Free Manager Team to work
    gemini_api_key:    str = Field(default="", description="Google AI Studio — aistudio.google.com/apikey")
    groq_api_key:      str = Field(default="", description="Groq — console.groq.com")
    cerebras_api_key:  str = Field(default="", description="Cerebras — cloud.cerebras.ai")

    # ── OpenRouter (free :free models) ───────────────────
    openrouter_api_key:  str = Field(default="", description="OpenRouter — openrouter.ai/settings/keys")
    openrouter_base_url: str = "https://openrouter.ai/api/v1"

    # ── Cross-provider redundancy (added 2026-07, all optional) ───────────
    # None of these are required — the system runs fine without them. They
    # exist to reduce correlated Groq+Cerebras outages (both flaked in the
    # same session live) by adding fallback tiers on independent providers.
    nvidia_api_key: str = Field(default="", description="NVIDIA NIM — build.nvidia.com (free, ~40 RPM shared/key)")
    zai_api_key:    str = Field(default="", description="Z.AI (Zhipu GLM) — z.ai/model-api (GLM-4.7-Flash free)")
    mistral_api_key: str = Field(default="", description="Mistral La Plateforme — console.mistral.ai (free Experiment tier, eval/prototype ToS only)")

    # ── Voice input (optional) ────────────────────────────
    # Voice transcription runs on free Groq Whisper by default. Wispr Flow's
    # API is sales-gated; a key here is a placeholder for a future
    # integration (see tools/voice_input.py docstring), not an active path.
    wispr_api_key: str = Field(default="", description="Wispr Flow — api-docs.wisprflow.ai (optional, sales-gated)")

    # ── Web search ─────────────────────────────────────────
    # Replaced the DuckDuckGo-HTML-scraping stack (fragile: soft-blocked
    # entire query TOPICS under normal use, no official contract) with two
    # real APIs, both live-verified (2026-07-27): Firecrawl (primary --
    # JS-rendered, clean markdown, real /search endpoint, free 1,000
    # pages/month, no card) and Exa (neural/semantic search, used for
    # relevance ranking instead of an LLM call). brave_api_key kept only
    # because Brave now requires a card even on its free tier -- confirmed
    # live the same day -- so it was never wired in; not used by search.py.
    firecrawl_api_key: str = Field(default="", description="Firecrawl — firecrawl.dev/app/api-keys (primary search+scrape source)")
    exa_api_key: str = Field(default="", description="Exa — dashboard.exa.ai/api-keys (semantic search / relevance ranking)")
    brave_api_key: str = Field(default="", description="Brave Search — brave.com/search/api (unused: requires a card even for free tier)")

    # ── OmniRoute (optional, local gateway) ────────────────
    # A self-hosted AI gateway (github.com/diegosouzapw/OmniRoute) run
    # locally via `npm run dev` in a separate checkout. Not started
    # automatically by VibeAI, and not guaranteed to be running -- the
    # existing circuit-breaker/fallback machinery routes around it like any
    # other unavailable connector. Get the key from the local dashboard's
    # Endpoints page (http://localhost:20128 by default).
    omniroute_api_key:  str = Field(default="", description="OmniRoute local gateway key — dashboard Endpoints page")
    omniroute_base_url: str = "http://localhost:20128/v1"

    # ── Image generation (free) ───────────────────────────
    hf_token: str = Field(default="", description="HuggingFace — huggingface.co/settings/tokens")
    # Pollinations.ai: no key needed

    # ── Local inference ───────────────────────────────────
    ollama_base_url: str = "http://localhost:11434/v1"

    # ── GitHub Integration ────────────────────────────────
    github_token: str = Field(default="", description="GitHub PAT — github.com/settings/tokens (needs repo + workflow scopes)")

    # ── Legacy (no longer used but kept for compat) ───────
    together_api_key:    str = Field(default="", description="Not used — replaced by Pollinations")
    together_base_url:   str = "https://api.together.xyz/v1"

    # ── Pipeline settings ─────────────────────────────────
    max_review_iterations: int   = 3
    quality_threshold:     float = 0.85
    default_token_budget:  int   = 8_000
    default_timeout_ms:    int   = 30_000

    # ── Storage ───────────────────────────────────────────
    session_db_path: str = "./logs/sessions.db"

    # ── API server ────────────────────────────────────────
    # SECURITY: localhost by default. The API executes shell commands via the
    # agent — bind 0.0.0.0 only behind a reverse proxy AND with vibe_api_token set.
    api_host:  str = "127.0.0.1"
    api_port:  int = 8000
    # Bearer token required on all mutating API routes when set (see api/server.py).
    vibe_api_token: str = Field(default="", description="Bearer token for mutating API routes")
    log_level: str = "INFO"


settings = Settings()

# pydantic-settings reads .env straight into this object's fields -- it never
# touches os.environ. huggingface_hub (used by sentence-transformers for
# tools/memory.py's embedding model) reads the token directly from the
# process environment instead, so a real HF_TOKEN in .env was silently never
# reaching it -- found live (2026-07-15): every SentenceTransformer load hit
# "sending unauthenticated requests to the HF Hub" despite HF_TOKEN being set.
if settings.hf_token and not os.environ.get("HF_TOKEN"):
    os.environ["HF_TOKEN"] = settings.hf_token
