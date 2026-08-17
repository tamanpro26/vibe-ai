"""
config/models_config.py  (v7 — honest naming: every model_id says what actually runs)

WHAT THIS IS: a multi-provider orchestration layer over ~12 distinct free
models. Registry entries are (real model × provider × role) slots — the same
strong model deliberately appears in several roles (e.g. gpt-oss-120b serves
as coder, debugger, and planner across Groq/Cerebras/OpenRouter, which also
gives cross-provider redundancy when one provider rate-limits).

Naming rule: model_id = <real_model>_<provider-or-role>. If you rename or
re-point an entry, keep the id truthful — ids that lie about the underlying
model make cost, limit, and capability reasoning impossible.

Providers (all free tiers, no credit card unless noted):
  google      → Gemini 2.5 / 2.0 Flash — https://aistudio.google.com/apikey
                (free tier is ~20 req/day/model — verified live 2026-06-30)
  groq        → GPT-OSS-120B, Llama 3.3 70B, Llama 3.1 8B, Qwen3.6-27B, Whisper
                https://console.groq.com (per-model TPM/TPD limits — see groq_conn)
  cerebras    → GLM-4.7, GPT-OSS-120B — https://cloud.cerebras.ai (1M tokens/day,
                but free-tier context limit FLUCTUATES — see cerebras_conn)
  openrouter  → :free variants only (GPT-OSS, Nemotron, Llama 4 Maverick, Gemma,
                LFM) — https://openrouter.ai (~50 req/day free; the tightest tier)
  nvidia      → NIM catalog (DeepSeek-class open models) — build.nvidia.com
                ~40 RPM shared across ALL models on one key (traffic-dependent,
                not a published SLA — confirmed by NVIDIA staff on their forums,
                2026 sources). Genuinely useful as a DIFFERENT-PROVIDER fallback
                tier (independent of the Groq/Cerebras outage correlation we hit
                live), not as a primary — 40 RPM starves an agent loop fast.
  zai         → Z.AI (Zhipu) — GLM-4.7-Flash / GLM-4.5-Flash are free outright,
                ~1000 req/day (provider has revised free-tier limits twice in
                the past year per 2026 trackers — treat as approximate).
                https://z.ai/model-api. Same model FAMILY as our Cerebras
                primary (glm_47_cerebras) — different provider, so a Cerebras
                outage doesn't take this down too.
  mistral     → La Plateforme free "Experiment" tier — ~1B tokens/month, but
                Mistral's own ToS scopes this to evaluation/prototyping, NOT
                production traffic, and no longer publishes exact rate limits.
                Registered here for manual /model use only — deliberately NOT
                wired into the automatic agent fallback chain (see agent_loop.py)
                to respect that restriction.
  ollama      → local Qwen3.5-0.8B. No key. https://ollama.com
  pollinations→ FLUX image generation. No key, no signup.
  huggingface → FLUX.1-dev (needs Inference-Provider token permission)
  anthropic   → optional paid Claude (manager tier + manual /model selection);
                without a valid key the Free Council is the primary — by design.

Operational history (deprecations, limit discoveries) lives in DECISIONS.md.
"""
from dataclasses import dataclass, field
from typing import Literal

Provider = Literal[
    "anthropic", "google", "groq", "cerebras",
    "openrouter", "ollama", "pollinations", "huggingface",
    "nvidia", "zai", "mistral",
]
Team = Literal["manager", "prompt", "brain", "code", "vision", "design", "router"]


@dataclass
class ModelDef:
    model_id:       str
    provider:       Provider
    api_model:      str
    team:           Team
    role:           str
    context_window: int
    capabilities:   list[str] = field(default_factory=list)
    # Name of the Settings attribute to read the API key from. None = the
    # connector's own default (e.g. AnthropicConnector defaults to
    # settings.anthropic_api_key). Lets two ModelDefs share a provider/connector
    # while using different keys — e.g. claude_opus_4_6 uses anthropic_api_key_2
    # instead of the intentionally-invalid anthropic_api_key.
    api_key_field:  str | None = None
    # Provider-specific request-body fields merged verbatim into the OpenAI SDK
    # call via its native extra_body= kwarg (deep-merged, this wins on key
    # collisions). Concept ported from jcode v0.54.4 (MIT). The headline case:
    # NVIDIA NIM DeepSeek models only enable their thinking phase when the
    # request carries chat_template_kwargs -- without it they answer with
    # reasoning OFF (or hang). A bad value here is logged and ignored, never
    # fails the call.
    extra_body:     dict | None = None
    # Per-request total timeout override (seconds). The non-streaming
    # equivalent of jcode's stream_idle_timeout: a silently-thinking reasoning
    # model can exceed the default 30s client timeout before emitting anything,
    # so reasoning seats get a longer ceiling. None = the connector default.
    request_timeout_s: float | None = None


MODEL_REGISTRY: dict[str, ModelDef] = {

    # ── MANAGER (paid — Claude Sonnet 4.6 only) ────────────────────────────
    "claude_sonnet_4.6": ModelDef(
        model_id="claude_sonnet_4.6",
        provider="anthropic",
        api_model="claude-sonnet-4-6",
        team="manager",
        role="Manager & controller",
        context_window=200_000,
        capabilities=["reasoning", "orchestration", "review", "code", "vision"],
    ),

    # Optional premium model — NOT part of any automated pipeline or fallback
    # chain (see _LLM_PROVIDERS in models/registry.py, which excludes "anthropic"
    # from generate_resilient; registry.get_team() is also never called anywhere
    # in this codebase, so team membership alone never triggers automatic use).
    # Select it explicitly with `/model claude_opus_4_6`. Uses anthropic_api_key_2,
    # a separate real key, so it has no effect on the intentionally-invalid
    # anthropic_api_key the Free Manager Council relies on. team="code" so it
    # shows up in the CLI's `models` listing alongside the other coding models.
    "claude_opus_4_6": ModelDef(
        model_id="claude_opus_4_6",
        provider="anthropic",
        api_model="claude-opus-4-6",
        team="code",
        role="Optional premium model (manual /model selection only, real key required)",
        context_window=200_000,
        capabilities=["reasoning", "orchestration", "review", "code", "vision", "agentic_coding"],
        api_key_field="anthropic_api_key_2",
    ),

    # Dedicated Gemini slot for the Free Manager Council's Planner role.
    # Deliberately a DIFFERENT api_model ("gemini-2.0-flash") than gemini_flash/
    # gemini_flash_prompt/gemini_flash_vision (all "gemini-2.5-flash") -- found
    # live (2026-07-13): Google's free-tier quota key is
    # GenerateRequestsPerDayPerProjectPerModel (~20 req/day), scoped per
    # (project, model name), not per our internal registry slot name. Brain
    # team, vision team, and the prompt-refiner's context-enricher stage all
    # already share that one 2.5-flash bucket and were observed exhausting it
    # mid-session. Since the Free Manager Council is the primary manager
    # whenever no paid Anthropic key is configured (see tools/manager_fallback.py),
    # its Planner role hitting the SAME already-contended bucket meant a
    # fourth consumer competing for the scarcest quota in the whole system.
    # gemini-2.0-flash is a separate quota bucket entirely (already proven
    # live via gemini_20_flash_ocr below) -- giving the Council's Planner its
    # own private headroom instead of a fourth straw in an already-dry well.
    "gemini_flash_council": ModelDef(
        model_id="gemini_flash_council",
        provider="google",
        api_model="gemini-2.0-flash",
        team="manager",
        role="Free Council planner (isolated quota bucket)",
        context_window=1_000_000,
        capabilities=["reasoning", "planning"],
    ),
    # CEO — system-wide oversight above the Free Manager Council (see
    # manager/ceo.py). Deliberately the single largest model in this entire
    # registry: 550B parameters, live-verified 2026-07-16 direct against
    # OpenRouter's real /api/v1/models catalog (not a secondhand list) and
    # confirmed working with a real API call (19.9s, correct response).
    # Reserved for aggregate oversight reports, NOT per-request use -- a
    # 550B free-tier model in the path of every single Council response
    # would defeat the whole point of a fast free-tier pipeline. This is
    # exactly the right way to spend a slow/heavy model's budget: rarely,
    # for judgment that actually needs the extra capability.
    "nemotron_ultra_ceo": ModelDef(
        model_id="nemotron_ultra_ceo",
        provider="openrouter",
        api_model="nvidia/nemotron-3-ultra-550b-a55b:free",
        team="manager",
        role="CEO — system-wide oversight (on-demand reports only, not per-request)",
        context_window=1_000_000,
        capabilities=["reasoning", "oversight"],
    ),

    # ── PROMPT REFINER TEAM (all free) ────────────────────────────────────
    "gpt_oss_120b_free_intent": ModelDef(
        model_id="gpt_oss_120b_free_intent",
        provider="openrouter",
        # Was openai/gpt-oss-120b:free. OpenRouter retired that slug: probed
        # live 2026-08-09, it returns 404 on EVERY call -- "unavailable for
        # free ... use this slug instead: openai/gpt-oss-120b" (the paid one).
        # This is refiner STAGE 1, so every non-fast-path request in the system
        # was paying a full generate_resilient failover walk before recovering.
        # Repointed to a free slug verified answering the same day rather than
        # to the paid one, which would silently start billing.
        api_model="nvidia/nemotron-3-super-120b-a12b:free",
        team="prompt",
        role="Intent parser",
        context_window=128_000,
        capabilities=["instruction_following", "intent_extraction"],
    ),
    "gemini_flash_prompt": ModelDef(
        model_id="gemini_flash_prompt",
        provider="google",
        api_model="gemini-2.5-flash",           # Hybrid reasoning, free, 1M ctx
        team="prompt",
        role="Context enricher",
        context_window=1_000_000,
        capabilities=["reasoning", "context_enrichment", "agent"],
    ),
    "gpt_oss_20b_free": ModelDef(
        model_id="gpt_oss_20b_free",
        provider="openrouter",
        api_model="openai/gpt-oss-20b:free",  # Verified working + tool-calling, free
        team="prompt",
        role="Ambiguity resolver",
        context_window=128_000,
        capabilities=["instruction_following", "function_calling"],
    ),
    "nemotron_super_spec": ModelDef(
        model_id="nemotron_super_spec",
        provider="openrouter",
        api_model="nvidia/nemotron-3-super-120b-a12b:free",  # NVIDIA 120B, strong reasoning, free
        team="prompt",
        role="Technical translator",
        context_window=128_000,
        capabilities=["reasoning", "math", "logic", "specification"],
    ),
    "nemotron_nano_format": ModelDef(
        model_id="nemotron_nano_format",
        provider="openrouter",
        api_model="nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",  # Reasoning + structured output
        team="prompt",
        role="Instruction formatter",
        context_window=128_000,
        capabilities=["reasoning", "structured_output", "formatting"],
    ),

    # ── BRAIN TEAM (all free) ─────────────────────────────────────────────
    "gemini_flash": ModelDef(
        model_id="gemini_flash",
        provider="google",
        api_model="gemini-2.5-flash",           # Strong orchestration + multimodal, free
        team="brain",
        role="Master orchestrator",
        context_window=1_000_000,
        capabilities=["orchestration", "long_context", "code", "reasoning", "vision"],
    ),
    "gpt_oss_120b_planner": ModelDef(
        model_id="gpt_oss_120b_planner",
        provider="cerebras",
        api_model="gpt-oss-120b",               # llama-4-scout removed from Cerebras free tier
        team="brain",
        role="Long-context planner",
        context_window=131_000,
        capabilities=["planning", "long_context", "decomposition"],
    ),
    "qwen36_27b_verifier": ModelDef(
        model_id="qwen36_27b_verifier",
        provider="groq",
        api_model="qwen/qwen3.6-27b",           # Qwen3-32B DEPRECATED by Groq 2026-06-17 (also had a hard 6k TPM wall, hit live); qwen3.6-27b is Groq's stated successor
        team="brain",
        role="Reasoning verifier",
        context_window=128_000,
        capabilities=["reasoning", "verification", "quality_gate"],
    ),
    "llama33_70b_memory": ModelDef(
        model_id="llama33_70b_memory",
        provider="groq",
        api_model="llama-3.3-70b-versatile",    # 12k TPM — 2× headroom vs 8B-instant (actual 6k)
        team="brain",
        role="Memory manager",
        context_window=128_000,
        capabilities=["long_context", "state_management"],
    ),
    "gpt_oss_120b_coord": ModelDef(
        model_id="gpt_oss_120b_coord",
        provider="groq",
        api_model="openai/gpt-oss-120b",        # Qwen3-32B DEPRECATED by Groq 2026-06-17; gpt-oss-120b has 0% failure rate in our logs
        team="brain",
        role="Multimodal coordinator",
        context_window=128_000,
        capabilities=["multimodal", "coordination", "reasoning"],
    ),

    # ── CODE TEAM (all free) ──────────────────────────────────────────────
    "gpt_oss_120b_coder": ModelDef(
        model_id="gpt_oss_120b_coder",
        provider="groq",
        api_model="openai/gpt-oss-120b",   # Moved off OpenRouter 2026-06-30: 41% observed failure rate there (11/27 calls, empty responses under load, 50 req/day cap). Same model on Groq = 0% failure rate in our logs.
        team="code",
        role="Primary vibe coder",
        context_window=128_000,
        capabilities=["code_generation", "visual_to_code", "agentic_coding"],
    ),
    "gpt_oss_120b_debug": ModelDef(
        model_id="gpt_oss_120b_debug",
        provider="groq",
        api_model="openai/gpt-oss-120b",  # Llama 4 Scout DEPRECATED by Groq 2026-06-17; gpt-oss-120b is Groq's stated migration target. Scout was also the weakest model in live testing (broken JSX, abandoned scaffolds, file corruption).
        team="code",
        role="Debugging specialist",
        context_window=128_000,
        capabilities=["debugging", "code_reasoning", "error_analysis"],
    ),
    "llama33_70b_coder": ModelDef(
        model_id="llama33_70b_coder",
        provider="groq",
        api_model="llama-3.3-70b-versatile",    # Good quality BUT real free limits are 1,000 req/day + 100k tokens/day (hit live 2026-06-30) — runs out fast under agent workloads
        team="code",
        role="Fast iteration layer",
        context_window=128_000,
        capabilities=["code_generation", "fast_inference"],
    ),
    "glm_47_cerebras": ModelDef(
        model_id="glm_47_cerebras",
        provider="cerebras",
        api_model="zai-glm-4.7",                 # llama3.3-70b removed from Cerebras free tier; GLM 4.7 verified live
        team="code",
        role="Full-stack generator",
        context_window=128_000,
        capabilities=["code_generation", "full_stack", "speed"],
    ),
    # Added 2026-07: third-tier fallback on a provider INDEPENDENT of Groq/Cerebras,
    # added specifically because both flaked in the same session (Cerebras context
    # limit dropped to 8k, Groq TPM walls hit) — a fallback chain that's all
    # Groq+Cerebras shares their outage risk. Same GLM-4.7 model family as our
    # primary, so similar behavior/quality, but Z.AI's own infrastructure.
    "glm_47_flash_zai": ModelDef(
        model_id="glm_47_flash_zai",
        provider="zai",
        api_model="glm-4.7-flash",
        team="code",
        role="Cross-provider fallback (GLM family, Z.AI infra)",
        context_window=128_000,
        capabilities=["code_generation", "full_stack"],
    ),
    # NVIDIA NIM: ~40 RPM shared across the whole key (not model-specific), so
    # this is a THIN fallback — fine as tier 4 (rarely reached), bad as primary.
    # CATALOG SLUGS SHIFT: NIM's model catalog is reported to change frequently
    # (2026 sources). If this model_id starts failing with 404, check the
    # current slug at https://build.nvidia.com/models and update api_model —
    # the failure mode is safe (this tier just never contributes) but silent,
    # since it only gets reached after 3 other tiers already failed.
    "deepseek_v4_flash_nim": ModelDef(
        model_id="deepseek_v4_flash_nim",
        provider="nvidia",
        api_model="deepseek-ai/deepseek-v4-flash",   # NIM catalog, 2026: "optimized for fast coding and agents"
        team="code",
        role="Deep fallback (different provider, thin ~40 RPM quota)",
        context_window=1_000_000,
        capabilities=["code_generation", "reasoning", "agentic_coding"],
        # jcode Feature 1 headline case: NIM DeepSeek enables its thinking phase
        # only when the request carries chat_template_kwargs. NOT live-verified
        # here (NVIDIA_API_KEY unset in this environment) -- value is from the
        # jcode v0.54.4 doc; verify with a live call once a key is added (assert
        # usage.reasoning_tokens > 0 with vs. without). request_timeout gives
        # the silent thinking phase room past the 30s client default.
        extra_body={"chat_template_kwargs": {"thinking": True, "reasoning_effort": "medium"}},
        request_timeout_s=120.0,
    ),
    # Manual-selection-only (see provider docstring above) — NOT in the
    # automatic fallback chain out of respect for Mistral's own eval/prototype-
    # only ToS on this free tier.
    "codestral_mistral": ModelDef(
        model_id="codestral_mistral",
        provider="mistral",
        api_model="codestral-latest",
        team="code",
        role="Manual-select coder (ToS: evaluation tier, not production)",
        context_window=256_000,
        capabilities=["code_generation"],
    ),
    "nemotron_super_bulk": ModelDef(
        model_id="nemotron_super_bulk",
        provider="openrouter",
        api_model="nvidia/nemotron-3-super-120b-a12b:free",  # Verified working + tool-calling, free
        team="code",
        role="Bulk processor",
        context_window=128_000,
        capabilities=["bulk_generation", "test_writing", "boilerplate"],
    ),
    # Registered 2026-07-16 for teams/leadership.py's Code-team-leader
    # candidate search: a real, currently-live OpenRouter free model
    # (confirmed via direct /api/v1/models fetch, not a secondhand list),
    # 1M context, coding-specialized. NOT used as the automatic team leader
    # despite that -- verified live TWICE the same day and failed both times
    # with "temporarily rate-limited upstream" (OpenRouter's own shared free
    # pool for this popular model is oversubscribed, ~155s and ~183s before
    # giving up). Manual-select only until it proves reliable; glm_47_cerebras
    # holds the Code team Leader role instead (proven 0% failure rate).
    "qwen3_coder_openrouter": ModelDef(
        model_id="qwen3_coder_openrouter",
        provider="openrouter",
        # Was qwen/qwen3-coder:free -- 404 as of 2026-08-09, same OpenRouter
        # free-slug retirement. gpt-oss-20b:free probed alive the same day.
        api_model="openai/gpt-oss-20b:free",
        team="code",
        role="Manual-select coder",
        context_window=1_048_576,
        capabilities=["code_generation"],
    ),

    # ── VISION TEAM (all free) ────────────────────────────────────────────
    "gemini_flash_vision": ModelDef(
        model_id="gemini_flash_vision",
        provider="google",
        api_model="gemini-2.5-flash",           # Best free vision: text+image+audio+video
        team="vision",
        role="UI screenshot critic",
        context_window=1_000_000,
        capabilities=["vision", "ocr", "screenshot", "multimodal"],
    ),
    "nemotron_vl": ModelDef(
        model_id="nemotron_vl",
        provider="openrouter",
        api_model="nvidia/nemotron-nano-12b-v2-vl:free",  # NVIDIA vision-language model, free
        team="vision",
        role="Long video analyst",
        context_window=128_000,
        capabilities=["vision", "video_understanding", "multimodal"],
    ),
    "gemini_20_flash_ocr": ModelDef(
        model_id="gemini_20_flash_ocr",
        provider="google",
        api_model="gemini-2.0-flash",           # Free, great OCR and document understanding
        team="vision",
        role="OCR specialist",
        context_window=1_000_000,
        capabilities=["ocr", "document_understanding", "vision"],
    ),
    "llama4_maverick": ModelDef(
        model_id="llama4_maverick",
        provider="openrouter",
        # Was meta-llama/llama-4-maverick:free -- 404 as of 2026-08-09.
        # Its callers (teams/vision.py, cli.py, tools/agent_tools.py) all list
        # it as a VISION fallback, so it is repointed to a free slug that is
        # both alive and actually multimodal, not merely alive.
        api_model="nvidia/nemotron-nano-12b-v2-vl:free",
        team="vision",
        role="Visual debugger",
        context_window=524_288,
        capabilities=["vision", "multimodal", "debugging"],
    ),
    "whisper_large_v3": ModelDef(
        model_id="whisper_large_v3",
        provider="groq",
        api_model="whisper-large-v3",           # SAME MODEL as before, free on Groq
        team="vision",
        role="Audio transcriber",
        context_window=0,
        capabilities=["audio", "transcription", "video_audio"],
    ),

    # ── DESIGN TEAM (all free, unchanged from v5) ─────────────────────────
    "flux_asset": ModelDef(
        model_id="flux_asset",
        provider="pollinations",
        api_model="flux",
        team="design",
        role="Static asset gen",
        context_window=0,
        capabilities=["image_generation", "ui_assets", "high_resolution"],
    ),
    "flux_typography": ModelDef(
        model_id="flux_typography",
        provider="pollinations",
        api_model="flux",   # seedream returns HTTP 500 server-side (verified live 2026-07-03) — every icon generated with it was permanently broken
        team="design",
        role="Typography renderer",
        context_window=0,
        capabilities=["image_generation", "text_rendering", "typography"],
    ),
    "flux_world": ModelDef(
        model_id="flux_world",
        provider="pollinations",
        api_model="flux",   # HF token lacks Inference-Provider permissions (403 on every call, observed live 2026-06-30); Pollinations flux is keyless and has a 100% success rate in our logs
        team="design",
        role="World-aware gen",
        context_window=0,
        capabilities=["image_generation", "world_knowledge"],
    ),
    "flux_realism_gen": ModelDef(
        model_id="flux_realism_gen",
        provider="pollinations",
        api_model="flux-realism",
        team="design",
        role="Animation generator",
        context_window=0,
        capabilities=["image_generation", "motion", "animation"],
    ),
    "turbo_fast": ModelDef(
        model_id="turbo_fast",
        provider="pollinations",
        api_model="turbo",
        team="design",
        role="UI motion demo",
        context_window=0,
        capabilities=["image_generation", "fast_generation"],
    ),

    # ── ROUTER TEAM (all free) ────────────────────────────────────────────
    "gpt_oss_120b_dispatch": ModelDef(
        model_id="gpt_oss_120b_dispatch",
        provider="cerebras",
        api_model="gpt-oss-120b",               # llama3.3-70b removed; still ~2,600 TPS on Cerebras
        team="router",
        role="Primary dispatcher",
        context_window=128_000,
        capabilities=["routing", "classification", "fast_inference"],
    ),
    "lfm_router": ModelDef(
        model_id="lfm_router",
        provider="openrouter",
        # Was liquid/lfm-2.5-1.2b-instruct:free -- OpenRouter returns
        # "No endpoints found for liquid/..." as of 2026-08-09, i.e. the model
        # is gone rather than merely paid. gemma-4-31b-it:free probed alive and
        # is small enough to keep routing cheap.
        api_model="google/gemma-4-31b-it:free",
        team="router",
        role="Logic router",
        context_window=32_768,
        capabilities=["routing", "logic", "structured_output"],
    ),
    # CONFIRMED BROKEN live (2026-07-13) — do not wire new callers to this
    # model_id without re-verifying first. The slug itself is real (checked
    # OpenRouter's current catalog, not a stale/renamed model), but every
    # single live call this session failed with the same two-step pattern:
    # OpenRouter's "Google AI Studio" backend returns 429 (rate-limited),
    # then its "OpenInference" fallback backend returns 404 "Model not
    # found" -- i.e. OpenRouter's own free-tier routing pool for this model
    # is currently degraded on the backend that would 404, not a mistake on
    # our end. Both of this model_id's real callers (Free Manager Council's
    # Synthesizer role, VibeMind's BRAIN_MODEL) were moved to glm_47_flash_zai
    # (verified working in production logs) rather than left silently eating
    # a guaranteed-fail round trip on every single call. Left registered
    # here rather than deleted in case OpenRouter's free-tier availability
    # recovers — re-verify live before reconnecting anything to it.
    "gemma_4": ModelDef(
        model_id="gemma_4",
        provider="openrouter",
        api_model="google/gemma-4-31b-it:free",  # Gemma 4 31B — multimodal routing
        team="router",
        role="Multimodal gatekeeper",
        context_window=128_000,
        capabilities=["routing", "multimodal", "fallback"],
    ),
    "llama31_8b_router": ModelDef(
        model_id="llama31_8b_router",
        provider="groq",
        api_model="llama-3.1-8b-instant",       # Qwen3-32B DEPRECATED by Groq 2026-06-17; 8b-instant is ideal for routing: 14,400 req/day + 500k tokens/day, the most generous quota on Groq's free tier
        team="router",
        role="Cost-overflow handler",
        context_window=128_000,
        capabilities=["routing", "fast_inference"],
    ),
    "qwen25_3b_ollama": ModelDef(
        model_id="qwen25_3b_ollama",
        provider="ollama",
        api_model="qwen2.5:3b-instruct",         # 100% local, completely free
        # Was qwen3.5:0.8b ("qwen3_5_0_8b") — verified live (2026-07-06) that
        # model returned 0 chars on every trial (5/5, 3 prompt variants,
        # max_tokens to 2000) against the router's real classification
        # schema; it's a "thinking" model that never converged to visible
        # output for a schema this size. qwen2.5:3b-instruct has no thinking
        # mode and a documented edge on JSON-schema compliance, and it works:
        # verified live, correct schema-matching JSON in 1.1s once warm (one-
        # time ~45s cold start to load into the RTX 3050's 4GB VRAM).
        team="router",
        role="Edge micro-router",
        context_window=32_768,                   # verified via ollama /api/show
        capabilities=["routing", "edge", "micro_tasks", "on_device"],
    ),

    # Local OmniRoute gateway (github.com/diegosouzapw/OmniRoute), run
    # manually via `npm run dev` in a separate checkout, NOT started by
    # VibeAI. Live-verified 2026-07-28 against a local instance on :20128
    # with 6 providers connected -- "auto/best-coding" resolved to
    # nvidia/llama-3.1-nemotron-nano-vl-8b-v1, real content, finish_reason
    # "stop", real token usage. Manual-select only, same treatment as
    # qwen3_coder_openrouter above: excluded from _LLM_PROVIDERS (models/
    # registry.py) so it is never auto-eligible for escalation/peer-consult
    # pools, since the local server is not guaranteed to be running. Most of
    # OmniRoute's connected providers (groq, cerebras, gemini, nvidia) are
    # ALSO reached directly elsewhere in this registry -- the real value here
    # is OmniRoute's own "auto/*" meta-routing across whichever of its
    # connected providers is currently available, which VibeAI has no direct
    # equivalent for.
    "omniroute_auto_coding": ModelDef(
        model_id="omniroute_auto_coding",
        provider="omniroute",
        api_model="auto/best-coding",
        team="code",
        role="Manual-select fallback -- OmniRoute's own best-available-coding-model router",
        context_window=1_048_576,
        capabilities=["coding", "tool_calling", "reasoning"],
    ),

    # ── Fleet expansion (2026-08-09) ──────────────────────────────────────────
    # Every entry below was probed with a real generate() call BEFORE being
    # added, because a provider catalog is not availability: NVIDIA advertises
    # 102 models on this key and only 5 actually answer -- the rest return
    # 404 "Function not found" because the tier does not have them deployed.
    # That is the same failure that left four 404 models sitting in this
    # registry for weeks, so nothing goes in here unprobed.
    #
    # Deliberately NOT added: meta/llama-3.3-70b-instruct (answers, but took
    # 86.5s for a two-token reply -- cold start or not, it is unverified as
    # fast and this fleet already loses ~28% of its time to slow providers),
    # meta/llama-3.2-90b-vision-instruct and meta/llama-3.2-1b/3b-instruct
    # (90s timeouts).

    "north_mini_code": ModelDef(
        model_id="north_mini_code",
        provider="openrouter",
        api_model="cohere/north-mini-code:free",
        team="code",
        role="Compact code generator",
        context_window=128_000,
        capabilities=["code_generation", "instruction_following"],
    ),
    "laguna_s_coder": ModelDef(
        model_id="laguna_s_coder",
        provider="openrouter",
        api_model="poolside/laguna-s-2.1:free",
        team="code",
        role="Code specialist (Poolside)",
        context_window=128_000,
        capabilities=["code_generation", "reasoning"],
    ),
    "llama32_11b_vision": ModelDef(
        model_id="llama32_11b_vision",
        provider="nvidia",
        api_model="meta/llama-3.2-11b-vision-instruct",
        team="vision",
        role="Screenshot analyst",
        context_window=128_000,
        capabilities=["vision", "multimodal", "image_understanding"],
    ),
    "nemotron_nano_vl_8b": ModelDef(
        model_id="nemotron_nano_vl_8b",
        provider="nvidia",
        api_model="nvidia/llama-3.1-nemotron-nano-vl-8b-v1",
        team="vision",
        role="Compact visual reasoner",
        context_window=128_000,
        capabilities=["vision", "multimodal", "reasoning"],
    ),
    "nemotron_super_49b": ModelDef(
        model_id="nemotron_super_49b",
        provider="nvidia",
        api_model="nvidia/llama-3.3-nemotron-super-49b-v1.5",
        team="brain",
        role="Deep reasoner",
        context_window=128_000,
        capabilities=["reasoning", "long_context", "analysis"],
    ),
    "llama31_70b_nim": ModelDef(
        model_id="llama31_70b_nim",
        provider="nvidia",
        api_model="meta/llama-3.1-70b-instruct",
        team="brain",
        role="General analyst",
        context_window=128_000,
        capabilities=["reasoning", "instruction_following"],
    ),
    "nemotron_lightning": ModelDef(
        model_id="nemotron_lightning",
        provider="openrouter",
        api_model="nvidia/nemotron-3.5-lightning:free",
        team="router",
        role="Fast classifier",
        context_window=128_000,
        capabilities=["routing", "fast_inference", "structured_output"],
    ),
    "lfm_26b_router": ModelDef(
        model_id="lfm_26b_router",
        provider="openrouter",
        api_model="liquid/lfm-2.5-2.6b:free",
        team="router",
        role="Tiny logic router",
        context_window=32_768,
        capabilities=["routing", "logic", "fast_inference"],
    ),
    "nemotron_nano_9b": ModelDef(
        model_id="nemotron_nano_9b",
        provider="openrouter",
        api_model="nvidia/nemotron-nano-9b-v2:free",
        team="prompt",
        role="Constraint extractor",
        context_window=128_000,
        capabilities=["instruction_following", "intent_extraction"],
    ),
    "gemma4_26b_prompt": ModelDef(
        model_id="gemma4_26b_prompt",
        provider="openrouter",
        api_model="google/gemma-4-26b-a4b-it:free",
        team="prompt",
        role="Requirement clarifier",
        context_window=128_000,
        capabilities=["instruction_following", "structured_output"],
    ),
    "nemotron_nano_30b": ModelDef(
        model_id="nemotron_nano_30b",
        provider="openrouter",
        api_model="nvidia/nemotron-3-nano-30b-a3b:free",
        team="prompt",
        role="Scope analyst",
        context_window=128_000,
        capabilities=["instruction_following", "reasoning"],
    ),
    "laguna_xs_manager": ModelDef(
        model_id="laguna_xs_manager",
        provider="openrouter",
        api_model="poolside/laguna-xs-2.1:free",
        team="manager",
        role="Lightweight coordinator",
        context_window=128_000,
        capabilities=["instruction_following", "coordination"],
    ),
}


def get_team_models(team: Team) -> list[ModelDef]:
    return [m for m in MODEL_REGISTRY.values() if m.team == team]


def get_model(model_id: str) -> ModelDef:
    if model_id not in MODEL_REGISTRY:
        raise KeyError(f"Unknown model_id: {model_id}")
    return MODEL_REGISTRY[model_id]
