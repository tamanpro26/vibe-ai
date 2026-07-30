"""
models/connectors/groq_conn.py
Groq — free, 14,400 req/day, 300-800 tokens/sec, native tool calling.
Sign up: https://console.groq.com
"""
from __future__ import annotations
import json, httpx
from typing import Any
from openai import AsyncOpenAI
from loguru import logger
from config.models_config import ModelDef
from config.settings import settings
from config.model_params import get_params
from models.base import BaseModelConnector

# ── SINGLE source of truth for Groq free-tier TPM limits ──────────────────────
# Both message truncation AND max_tokens clamping derive from THIS table.
# (There used to be two independent tables here; they drifted — the old one
# still listed deprecated models and defaulted unknown models to 28k TPM, which
# re-produced the exact 413 "request too large" errors already fixed once.)
# Unknown models get the most conservative budget observed on the free tier.
# Sources: console.groq.com/docs/rate-limits + live 413 error bodies.
GROQ_TPM: dict[str, int] = {
    "openai/gpt-oss-120b":      8_000,   # verified live: "Limit 8000" in 413 body
    "llama-3.3-70b-versatile": 12_000,   # verified live: 12k TPM (+100k TPD daily cap)
    "llama-3.1-8b-instant":     6_000,
    "qwen/qwen3.6-27b":         6_000,   # conservative until observed otherwise
}
GROQ_TPM_DEFAULT = 6_000
_OUTPUT_HEADROOM = 2_000  # slice of TPM reserved for the model's own output


class GroqConnector(BaseModelConnector):
    BASE_URL       = "https://api.groq.com/openai/v1"
    AUDIO_ENDPOINT = "https://api.groq.com/openai/v1/audio/transcriptions"

    def __init__(self, model_def: ModelDef) -> None:
        super().__init__(model_def)
        # timeout: found in code review (2026-07-13) that no connector set one,
        # relying on SDK defaults (~600s) and blocking the fallback chain on a hang.
        self._client = AsyncOpenAI(
            api_key=settings.groq_api_key or "not-configured", base_url=self.BASE_URL,
            timeout=settings.default_timeout_ms / 1000,
        )

    # ── Context management ─────────────────────────────────────────────────────

    @staticmethod
    def _est(text: str) -> int:
        """Fast char-based token estimate. JSON/code is ~3 chars/token; use 3 to stay conservative."""
        return max(1, len(text) // 3)

    def _msg_cost(self, m: dict) -> int:
        content = m.get("content") or ""
        if isinstance(content, list):
            content = " ".join(p.get("text", "") for p in content if isinstance(p, dict))
        cost = self._est(str(content))
        # tool_calls.function.arguments holds the actual payload (e.g. full file content
        # for create_file). Content is None/empty for these messages, so we must count
        # the args separately — otherwise they're estimated at ~1 token and never truncated.
        tool_calls_data = m.get("tool_calls")
        if tool_calls_data:
            try:
                cost += self._est(json.dumps(tool_calls_data))
            except Exception:
                cost += 500
        return cost

    @staticmethod
    def _group_turns(convo: list[dict]) -> list[list[dict]]:
        """Group messages into TURNS: each block starts with a non-tool
        message and absorbs every tool message that immediately follows it —
        a tool result and its parent assistant tool_calls message are kept
        or dropped together, never separated. Mirrors the same fix applied
        to cerebras_conn.py (2026-07-07): the old message-level window could
        keep a lone tool result with no parent, which is either an invalid
        message to send (a `tool` message needs its preceding assistant
        tool_calls message) or, if it survives, gives the model a result
        with no memory of what call produced it."""
        blocks: list[list[dict]] = []
        for m in convo:
            if m.get("role") != "tool" or not blocks:
                blocks.append([m])
            else:
                blocks[-1].append(m)
        return blocks

    _ANCHOR_MAX_CHARS = 2_000

    @classmethod
    def _anchor_content(cls, content: str) -> str:
        """Bounded HEAD+TAIL slice for re-anchoring — mirrors the identical
        fix in cerebras_conn.py (2026-07-09): the agent loop prepends several
        KB of injected context before the task in the first user message, so
        a head-only slice can contain zero task text."""
        if len(content) <= cls._ANCHOR_MAX_CHARS:
            return content
        head = content[:500]
        tail = content[-(cls._ANCHOR_MAX_CHARS - 520):]
        return f"{head}\n...\n{tail}"

    def _truncate_messages(self, messages: list[dict], tools: list[dict]) -> list[dict]:
        """
        Sliding-window truncation over whole TURNS: drops the oldest turns
        from the front to keep the total token estimate under the model's
        TPM budget. System prompt is always preserved; a user message
        (re-anchored on the original task if none survives) is guaranteed.
        """
        budget      = GROQ_TPM.get(self.api_model, GROQ_TPM_DEFAULT) - _OUTPUT_HEADROOM
        tools_toks  = self._est(json.dumps(tools))
        remaining   = budget - tools_toks

        if remaining <= 500:
            logger.warning(
                f"[groq/{self.api_model}] tools schema alone ~{tools_toks} tok "
                f"exceeds budget {budget} — cannot truncate further"
            )
            remaining = 500

        system = [m for m in messages if m.get("role") == "system"]
        convo  = [m for m in messages if m.get("role") != "system"]

        for m in system:
            remaining -= self._est(str(m.get("content", "")))

        turns = self._group_turns(convo)
        kept_turns: list[list[dict]] = []
        for turn in reversed(turns):
            cost = sum(self._msg_cost(m) for m in turn)
            if remaining - cost < 0 and kept_turns:
                break
            remaining -= cost
            kept_turns.append(turn)
        kept_turns.reverse()
        kept: list[dict] = [m for turn in kept_turns for m in turn]

        if not any(m.get("role") == "user" for m in kept):
            # Mirrors cerebras_conn._pick_anchor_message (2026-07-12): the
            # first user message in a history-carrying session can be a bare
            # greeting ("hi" -> a 2-char anchor observed live) — prefer the
            # first substantial user message, else the longest.
            from models.connectors.cerebras_conn import CerebrasConnector
            anchor_src = CerebrasConnector._pick_anchor_message(convo)
            if anchor_src:
                content = self._anchor_content(anchor_src)
                kept.insert(0, {"role": "user", "content": content})
                logger.warning(
                    f"[groq/{self.api_model}] truncation left no user message — "
                    f"re-anchored on the original task ({len(content)} chars)"
                )

        dropped = len(convo) - len(kept)
        if dropped:
            logger.warning(
                f"[groq/{self.api_model}] truncated {dropped} old message(s) "
                f"to stay inside {budget} TPM budget"
            )

        return system + kept

    async def _call(self, prompt, system, images, max_tokens, temperature, **kwargs) -> str:
        if not settings.groq_api_key:
            raise RuntimeError("GROQ_API_KEY not set — sign up free at https://console.groq.com")
        if "audio" in self.model_def.capabilities:
            return await self._transcribe(kwargs.get("audio_path", ""))
        messages = []
        if system:    messages.append({"role": "system",  "content": system})
        messages.append({"role": "user", "content": prompt})
        # Qwen3 on Groq emits <think> blocks into content; reasoning_format=hidden
        # keeps the reasoning but strips it from the returned text.
        # (chat_template_kwargs is no longer supported by Groq.)
        extra = {}
        if "qwen3" in self.api_model:
            extra = {"extra_body": {"reasoning_format": "hidden"}}
            # Reasoning still CONSUMES max_tokens even when hidden — observed live:
            # qwen3.6-27b returned 0 chars on a max_tokens=1000 planning call because
            # the whole budget went to reasoning before any visible text was emitted.
            # Same floor the Cerebras connector already applies for its reasoning models.
            max_tokens = max(max_tokens, 2048)
        resp = await self._client.chat.completions.create(
            model=self.api_model, messages=messages,
            max_tokens=min(max_tokens, 32_768), temperature=temperature, **extra,
        )
        return resp.choices[0].message.content or ""

    @staticmethod
    def _lenient_json_load(raw: str) -> Any | None:
        """Strict json.loads first; if that fails, try one deterministic
        repair (single-quoted Python-dict-style output is the single most
        common near-miss for a model that meant JSON) before giving up.
        Phase 0.4, UPGRADE_ROADMAP.md §7: "most malformed calls are one
        bracket away from valid" -- this is the "one bracket away" repair."""
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            pass
        # Only attempt the quote-swap if the string doesn't already contain
        # double quotes -- if it does, blindly swapping would corrupt a
        # string value that legitimately contains an apostrophe.
        if '"' not in raw:
            try:
                return json.loads(raw.replace("'", '"'))
            except (json.JSONDecodeError, ValueError):
                pass
        return None

    @staticmethod
    def _parse_failed_generation(text: str) -> dict | None:
        """
        Recovers tool calls from a model's raw text when Groq rejects the
        request with HTTP 400 tool_use_failed (error.failed_generation holds
        the model's real output). Tries progressively more general patterns
        -- most weak-model malformations fall into one of these three
        shapes, in roughly descending order of how often they're observed:

          1. Native text format:  <function=name>{json}</function>
             (Llama 3.x models on Groq, the original/most common case)
          2. Markdown-fenced JSON: a ```json ... ``` block containing
             {"name": ..., "arguments"/"parameters": {...}}
          3. A bare JSON object with the same shape, no fence or tag at all

        Each match's argument JSON also gets the lenient-repair pass above
        before being discarded. Returns None only if nothing recoverable is
        found by any pattern -- the caller falls back to the plain-text
        response as before.
        """
        import re

        calls: list[dict] = []

        # 1. <function=name>{json}</function>
        for name, args_str in re.findall(r"<function=(\w+)>(.*?)</function>", text, re.DOTALL):
            args = GroqConnector._lenient_json_load(args_str.strip())
            if args is not None:
                calls.append({"id": f"recovered_{len(calls)}", "name": name, "args": args})

        # 2 & 3. {"name": "...", "arguments"/"parameters": {...}} -- with or
        # without a markdown fence around it. Matched directly against the
        # inner object shape rather than the fence, so both cases share one
        # pattern instead of needing the fence stripped first.
        if not calls:
            obj_pattern = re.compile(
                r'\{\s*"name"\s*:\s*"(\w+)"\s*,\s*"(?:arguments|parameters)"\s*:\s*(\{.*?\})\s*\}',
                re.DOTALL,
            )
            for name, args_str in obj_pattern.findall(text):
                args = GroqConnector._lenient_json_load(args_str.strip())
                if args is not None:
                    calls.append({"id": f"recovered_{len(calls)}", "name": name, "args": args})

        return {"type": "tool_calls", "tool_calls": calls} if calls else None

    def _clamp_to_tpm(self, max_tokens: int, messages, tools=None) -> int:
        """
        Shrink max_tokens so est_input + max_tokens fits the model's TPM budget
        (from the single GROQ_TPM table above). Groq counts max_tokens toward
        the request size when enforcing TPM — a request is rejected with 413
        when est_input + max_tokens exceeds the budget, EVEN IF the actual
        completion would have been short. Observed live: a 742-token compact
        prompt with max_tokens=8192 was rejected by gpt-oss-120b as
        "Requested 8934, Limit 8000".
        """
        tpm = GROQ_TPM.get(self.api_model, GROQ_TPM_DEFAULT)
        # chars/3, not /4: URL-encoded strings and JSON tokenize much denser than
        # prose, and /4 caused live overshoots of ~300 tokens (Requested 8318 vs
        # Limit 8000; 12180 vs 12000) — the clamp landed exactly just-over.
        est_input = sum(len(str(m)) for m in messages) // 3
        if tools:
            est_input += sum(len(str(t)) for t in tools) // 3
        fitted = tpm - est_input - 800  # margin for tokenizer variance
        return max(1024, min(max_tokens, fitted))

    async def _call_with_tools(self, messages, tools, max_tokens, temperature, **kwargs) -> dict:
        """Native Groq tool calling."""
        if not settings.groq_api_key:
            raise RuntimeError("GROQ_API_KEY not set")
        messages = self._truncate_messages(messages, tools)
        max_tokens = self._clamp_to_tpm(max_tokens, messages, tools)
        try:
            resp = await self._client.chat.completions.create(
                model=self.api_model, messages=messages, tools=tools,
                tool_choice="auto", max_tokens=min(max_tokens, 32_768), temperature=temperature,
            )
        except Exception as exc:
            # Llama 3.x on Groq sometimes generates <function=name>{args}</function>
            # in plain text rather than using the API's structured tool-call format.
            # Groq returns 400 "tool_use_failed" with the raw text in failed_generation.
            # Parse it and return a proper tool-call dict so the agent doesn't crash.
            if getattr(exc, "status_code", None) == 400:
                # openai SDK already unwraps the outer {"error": {...}} envelope
                # before storing .body (see openai._client._make_status_error:
                # `data = body.get("error", body) if is_mapping(body) else body`)
                # — .body IS the inner {"message", "code", "failed_generation", ...}
                # dict directly. body.get("error", {}) here always returned {} and
                # silently discarded every recoverable failed_generation, verified
                # live: llama33_70b_coder's failed_generation was visible in the
                # exception text but never reached the recovery path below.
                body = getattr(exc, "body", None) or {}
                failed_gen = body.get("failed_generation", "") if isinstance(body, dict) else ""
                if failed_gen:
                    recovered = self._parse_failed_generation(failed_gen)
                    if recovered:
                        logger.warning(
                            f"[groq/{self.api_model}] recovered {len(recovered['tool_calls'])} "
                            f"tool call(s) from failed_generation (Llama text-format fallback)"
                        )
                        return recovered
                    # No <function=name> patterns — model gave a pure text explanation.
                    # Return it as a text response so the agent loop finishes gracefully
                    # rather than crashing the entire app. tool_call_failed=True is the
                    # signal the caller needs: this text is a FAILED tool-call attempt,
                    # not a legitimate answer -- found live (2026-07-15): without this
                    # marker, agent_loop.py's only escalation trigger (the empty-streak
                    # guard) checks response length, and a failed_generation dump can run
                    # to 2000 chars, so it never counted as a failure signal. The same
                    # broken model got re-tried 5 times in a row on an identical
                    # create_file call, and every retry failed the same way, shipping a
                    # broken page (missing stylesheet) the deterministic verifier had
                    # already correctly flagged five times over.
                    logger.warning(
                        f"[groq/{self.api_model}] 400 tool_use_failed with pure-text "
                        f"failed_generation — returning as text response"
                    )
                    return {"type": "text", "content": failed_gen[:2000], "tool_call_failed": True}
            raise
        msg = resp.choices[0].message
        if msg.tool_calls:
            calls = [
                {
                    "id":   tc.id,
                    "name": tc.function.name,
                    "args": json.loads(tc.function.arguments or "{}"),
                }
                for tc in msg.tool_calls
            ]
            result = {"type": "tool_calls", "tool_calls": calls}
            if msg.content:
                result["content"] = msg.content
            return result
        return {"type": "text", "content": msg.content or ""}

    async def _transcribe(self, audio_path: str) -> str:
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                with open(audio_path, "rb") as f:
                    resp = await client.post(
                        self.AUDIO_ENDPOINT,
                        headers={"Authorization": f"Bearer {settings.groq_api_key}"},
                        files={"file": (audio_path, f, "audio/mpeg")},
                        data={"model": self.api_model, "response_format": "text"},
                    )
                    resp.raise_for_status()
                    return resp.text
        except Exception as exc:
            logger.error(f"[groq/whisper] {exc}")
            raise
